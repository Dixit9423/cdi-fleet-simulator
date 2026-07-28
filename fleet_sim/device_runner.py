"""
fleet_sim/device_runner.py
──────────────────────────
Per-device gRPC bidirectional-stream thread.

Each DeviceRunner:
  1. Opens its own TelemetrySession stream (mTLS or insecure).
  2. Sends DeviceAnnouncement.
  3. If initial_state == MEASURING: sends ProfileMetadata → PatientBind → CoreStateEvent(MEASURING).
  4. Enters a state-machine loop:
       • Polls the command queue for runtime commands from the control panel.
       • If MEASURING, sends DataTick every 1 second.
  5. On shutdown (stop_flag), sends CoreStateEvent(IDLE) and closes.
"""

import os
import sys
import time
import threading
import queue as qmod
import importlib
from typing import Optional

import grpc

# Proto stubs (path resolved by run_fleet.py before import)
import telemetry_pb2
import telemetry_pb2_grpc

from fleet_sim.state_store import DeviceState, DEFAULT_TICK_SEQ_NO


def _now_ms() -> int:
    return int(time.time() * 1000)


def _classify_patient_source(patient_id: str | None) -> str | None:
    """Classify patient source for UI/logging only.
    Numeric ids are treated as OpenEMR (REAL); PAT-* are DUMMY."""
    if not patient_id:
        return None
    pid = str(patient_id).strip()
    if pid.isdigit():
        return "REAL"
    if pid.upper().startswith("PAT-"):
        return "DUMMY"
    return None


class DeviceRunner(threading.Thread):
    """Simulates one CDI Core device on its own gRPC stream."""

    def __init__(
        self,
        device_state: DeviceState,
        server_cfg: dict,
        param_catalog: dict[int, dict],
        profiles: dict[str, dict],
        channel_factory,  # callable() → grpc.Channel
        stop_event: threading.Event,
    ):
        super().__init__(daemon=True)
        self.ds = device_state
        self.server_cfg = server_cfg
        self.catalog = param_catalog
        self.profiles = profiles
        self.channel_factory = channel_factory
        self.stop_event = stop_event
        self.proto_version = getattr(self.ds, "proto_version", str(self.server_cfg.get("proto_version", "v1"))).lower()
        self._is_v2 = self.proto_version == "v2"
        self._pb2, self._stub_class = self._resolve_proto_runtime(self.proto_version)

        self.channel: Optional[grpc.Channel] = None
        self.stub = None
        self.stream = None
        self._send_q: qmod.Queue = qmod.Queue()
        self._tag = f"[{self.ds.device_id}]"
        self._ack_q: qmod.Queue = qmod.Queue()

        # Background response reader state
        self._stream_alive = True
        self._tick_interval_sec = float(self.server_cfg.get("tick_interval_sec", 1.0))
        self._verbose_tick_logs = bool(self.server_cfg.get("verbose_tick_logs", False))
        raw_jitter_ms = int(self.server_cfg.get("tick_jitter_ms", 180))
        max_safe_jitter_ms = max(0, int(self._tick_interval_sec * 400))
        jitter_ms = max(0, min(raw_jitter_ms, max_safe_jitter_ms))
        seed = sum(ord(c) for c in self.ds.device_id)
        self._tick_jitter_sec = (seed % (jitter_ms + 1)) / 1000.0 if jitter_ms > 0 else 0.0

    def _resolve_proto_runtime(self, proto_version: str):
        if proto_version == "v2":
            try:
                pb2 = importlib.import_module("core_v2_pb2")
                pb2_grpc = importlib.import_module("core_v2_pb2_grpc")
            except Exception as exc:
                raise RuntimeError(f"Failed to load v2 protobuf runtime: {exc}") from exc
            return pb2, pb2_grpc.TelemetryServiceV2Stub
        return telemetry_pb2, telemetry_pb2_grpc.TelemetryServiceStub

    # ── gRPC plumbing ────────────────────────────────────────────────────

    def _request_generator(self):
        """Yields DeviceToManager messages to the bidi stream."""
        while not self.stop_event.is_set():
            try:
                msg = self._send_q.get(timeout=0.15)
                yield msg
            except qmod.Empty:
                continue

    def _start_response_reader(self):
        """Start a background thread that continuously reads server responses.
        This ensures the main loop is NEVER blocked by next(stream)."""
        def _reader():
            try:
                for resp in self.stream:
                    if self.stop_event.is_set():
                        break
                    self._process_response(resp)
            except StopIteration:
                self._log("READER: Stream ended (server closed)")
            except grpc.RpcError as e:
                if not self.stop_event.is_set():
                    self._log(f"READER: gRPC error — {e.code()} {e.details()}")
            except Exception as e:
                if not self.stop_event.is_set():
                    self._log(f"READER: Error — {e}")
            finally:
                self._stream_alive = False
                with self.ds.lock:
                    self.ds.connected = False

        t = threading.Thread(target=_reader, daemon=True, name=f"reader-{self.ds.device_id}")
        t.start()
        return t

    def _process_response(self, resp):
        """Log a server response (called by background reader thread)."""
        try:
            if resp.HasField("manager_ack"):
                ack = resp.manager_ack
                self._ack_q.put((ack.ack_for_message_type, int(ack.ref_seq), ack.message))
                if ack.ack_for_message_type != "DataTick":
                    self._log(
                        f"<< ManagerAck  ref_seq={ack.ref_seq}  "
                        f"for={ack.ack_for_message_type}  msg='{ack.message}'"
                    )
            elif resp.HasField("stream_config"):
                self._log(f"<< StreamConfig (config_version={resp.stream_config.config_version})")
            elif resp.HasField("patient_bind"):
                patient_id = resp.patient_bind.patient_id
                source = _classify_patient_source(patient_id)
                with self.ds.lock:
                    self.ds.awaiting_backend_patient_bind = False
                    cooldown_until = self.ds.patient_cooldown_until_ms
                    cooldown_active = bool(cooldown_until and cooldown_until > _now_ms())
                    if cooldown_active:
                        cooldown_until_ms = int(cooldown_until or _now_ms())
                        ready_in_sec = int((cooldown_until_ms - _now_ms() + 999) / 1000)
                        self.ds.deferred_patient_id = patient_id
                        self.ds.pending_patient_source = source
                        self.ds.next_backend_patient_sync_until_ms = None
                        self._log(
                            f"<< PatientBind deferred for cooldown (patient={patient_id}, "
                            f"ready_in={ready_in_sec}s)"
                        )
                    else:
                        self.ds.pending_patient_id = patient_id
                        self.ds.pending_patient_source = source
                        self.ds.deferred_patient_id = None
                        self.ds.next_backend_patient_sync_until_ms = None
                        self._log(f"<< PatientBind pending decision (patient={patient_id}, source={source or 'unknown'})")
            elif resp.HasField("patient_release"):
                with self.ds.lock:
                    released_patient = resp.patient_release.patient_id
                    self.ds.patient_id = None
                    self.ds.patient_source = None
                    self.ds.pending_patient_id = None
                    self.ds.pending_patient_source = None
                    self.ds.deferred_patient_id = None
                    self.ds.awaiting_backend_patient_bind = True
                    self.ds.next_backend_patient_sync_until_ms = _now_ms() + 60000
                    self.ds.patient_cooldown_until_ms = _now_ms() + 60000
                    self.ds.current_state = "IDLE"
                    self.ds.measurement_session_id = None
                    self.ds.seq_no = DEFAULT_TICK_SEQ_NO
                    self.ds.tick_index = 0
                self._clear_hemodynamics_state(keep_inbound_metadata=True)
                self._log(f"<< PatientRelease (patient={released_patient})")
            elif self._is_v2 and resp.HasField("hemodynamics_profile_metadata"):
                profile = resp.hemodynamics_profile_metadata
                hemo_params: dict[int, dict] = {}
                for param in profile.params:
                    pid = int(param.param_id)
                    cfg_meta = self.catalog.get(pid, {})

                    incoming_alarm = {
                        "present": bool(param.alarm_limit.present),
                        "low": str(param.alarm_limit.low),
                        "high": str(param.alarm_limit.high),
                    }
                    incoming_range = {
                        "present": bool(param.range.present),
                        "display_low": str(param.range.display_low),
                        "display_high": str(param.range.display_high),
                        "operating_low": str(param.range.operating_low),
                        "operating_high": str(param.range.operating_high),
                    }

                    cfg_alarm = cfg_meta.get("alarm_limit", {})
                    cfg_range = cfg_meta.get("range", {})

                    alarm_meta = incoming_alarm
                    if cfg_alarm.get("present"):
                        alarm_meta = {
                            "present": True,
                            "low": str(cfg_alarm.get("low", "")),
                            "high": str(cfg_alarm.get("high", "")),
                        }

                    range_meta = incoming_range
                    if cfg_range.get("present"):
                        range_meta = {
                            "present": True,
                            "display_low": str(cfg_range.get("display_low", "")),
                            "display_high": str(cfg_range.get("display_high", "")),
                            "operating_low": str(cfg_range.get("operating_low", "")),
                            "operating_high": str(cfg_range.get("operating_high", "")),
                        }

                    hemo_params[pid] = {
                        "name": cfg_meta.get("name") or param.param_name,
                        "unit": cfg_meta.get("unit") or param.unit,
                        "source_personality": cfg_meta.get("source_personality") or param.source_device_personality,
                        "source_device_id": param.source_device_id,
                        "selected": bool(param.selected),
                        "alarm_limit": alarm_meta,
                        "range": range_meta,
                    }

                param_count = len(profile.params)
                with self.ds.lock:
                    self.ds.hemo_profile_received = bool(param_count > 0)
                    self.ds.hemo_profile_version = int(profile.profile_version)
                    self.ds.hemo_param_catalog = hemo_params

                params_preview = ", ".join(
                    f"{pid}:{meta.get('name', '')}"
                    for pid, meta in list(hemo_params.items())[:12]
                )
                self._log(
                    "<< HemodynamicsProfileMetadata "
                    f"device={profile.device_id or self.ds.device_id} "
                    f"session={profile.measurement_session_id or '-'} "
                    f"profile_version={profile.profile_version} "
                    f"params={param_count}"
                )
                if param_count == 0:
                    self._log("   Empty inbound hemodynamics metadata envelope (no backend hemo params)")
                if params_preview:
                    self._log(f"   EMR metadata params: {params_preview}")
                ack_msg = self._build_device_ack(
                    ack_for_message_type="hemodynamics_profile_metadata",
                    ref_seq=int(profile.profile_version),
                    message="Hemodynamics profile metadata received",
                )
                self._send_no_wait(
                    ack_msg,
                    f"DeviceAck(for=hemodynamics_profile_metadata, ref_seq={int(profile.profile_version)})",
                )
            elif self._is_v2 and resp.HasField("hemodynamics_data_tick"):
                tick = resp.hemodynamics_data_tick
                tick_values: dict[int, str] = {
                    int(value.param_id): str(value.value)
                    for value in tick.values
                }
                with self.ds.lock:
                    self.ds.hemo_last_tick_seq_no = int(tick.seq_no)
                    self.ds.hemo_last_tick_values = tick_values
                if self._verbose_tick_logs:
                    values_preview = ", ".join(
                        f"{value.param_id}={value.value}"
                        for value in tick.values
                    )
                else:
                    values_preview = ", ".join(
                        f"{value.param_id}={value.value}"
                        for value in tick.values[:8]
                    )
                self._log(
                    "<< HemodynamicsDataTick "
                    f"device={tick.device_id or self.ds.device_id} "
                    f"session={tick.measurement_session_id or '-'} "
                    f"seq_no={tick.seq_no} "
                    f"values=[{values_preview}]"
                )
                ack_msg = self._build_device_ack(
                    ack_for_message_type="hemodynamics_data_tick",
                    ref_seq=int(tick.seq_no),
                    message="Hemodynamics data tick received",
                )
                self._send_no_wait(
                    ack_msg,
                    f"DeviceAck(for=hemodynamics_data_tick, ref_seq={int(tick.seq_no)})",
                )
            else:
                self._log("<< Unknown response")
        except Exception as e:
            self._log(f"<< Response parse error: {e}")

    def _activate_deferred_patient_if_ready(self):
        """Move deferred patient to pending once cooldown expires."""
        log_reopened = False
        pending_patient: str | None = None
        with self.ds.lock:
            if self.ds.patient_id or self.ds.pending_patient_id:
                self.ds.awaiting_backend_patient_bind = False
                return

            cooldown_until = self.ds.patient_cooldown_until_ms
            if cooldown_until and cooldown_until > _now_ms():
                return
            if cooldown_until and cooldown_until <= _now_ms():
                self.ds.patient_cooldown_until_ms = None

            deferred = self.ds.deferred_patient_id
            if not deferred:
                if not self.ds.awaiting_backend_patient_bind:
                    self.ds.awaiting_backend_patient_bind = True
                    log_reopened = True
                return

            self.ds.pending_patient_id = deferred
            self.ds.pending_patient_source = _classify_patient_source(deferred)
            self.ds.deferred_patient_id = None
            self.ds.awaiting_backend_patient_bind = False
            self.ds.next_backend_patient_sync_until_ms = None
            pending_patient = deferred

        if log_reopened:
            self._log("   Backend patient bind window reopened (awaiting next PatientBind)")
        if pending_patient:
            self._log(f"   PatientBind now pending decision (patient={pending_patient})")

    def _maybe_request_backend_patient_bind(self) -> None:
        """Periodically re-play IDLE to ask the backend for the next patient binding."""
        now_ms = _now_ms()
        with self.ds.lock:
            if self.ds.patient_id or self.ds.pending_patient_id:
                self.ds.awaiting_backend_patient_bind = False
                self.ds.next_backend_patient_sync_until_ms = None
                return

            if not self.ds.awaiting_backend_patient_bind:
                return

            next_sync_until = self.ds.next_backend_patient_sync_until_ms
            if next_sync_until and next_sync_until > now_ms:
                return

            self.ds.next_backend_patient_sync_until_ms = now_ms + 60000

        self._log("   Requesting backend patient bind refresh")
        if not self._replay_current_state("PatientBindRequest"):
            self._log("   Backend patient bind refresh failed; will retry on next interval")

    def _has_accepted_patient(self) -> bool:
        """True when manager-bound patient was accepted and moved to patient_id."""
        with self.ds.lock:
            return bool(self.ds.patient_id)

    def _clear_hemodynamics_state(self, keep_inbound_metadata: bool = False) -> None:
        """Clear cached hemodynamics state.
        When keep_inbound_metadata=True, retain inbound metadata catalog for UI continuity
        across StopCase/PatientRelease on the same device."""
        with self.ds.lock:
            if keep_inbound_metadata:
                self.ds.hemo_profile_received = bool(self.ds.hemo_param_catalog)
            else:
                self.ds.hemo_profile_received = False
                self.ds.hemo_profile_version = 0
                self.ds.hemo_param_catalog = {}
            self.ds.hemo_last_tick_seq_no = None
            self.ds.hemo_last_tick_values = {}
            self.ds.hemo_outbound_profile_version = 0
            self.ds.hemo_outbound_param_catalog = {}
            self.ds.hemo_outbound_last_tick_seq_no = None
            self.ds.hemo_outbound_last_tick_values = {}

    def _send_and_wait_ack(self, msg, label: str, timeout: float = 5.0, expected_ack_type: str | None = None) -> bool:
        """Send a message and wait for the background reader to receive a response.
        Used for critical messages: Announcement, ProfileMetadata, CoreStateEvent."""
        def _normalize_ack_type(value: str | None) -> str:
            if not value:
                return ""
            return "".join(ch for ch in value.lower() if ch.isalnum())

        if not self._stream_alive:
            self._log(f"ERROR: Stream dead, cannot send: {label}")
            return False

        # Flush stale ACKs so we only evaluate responses received after this send.
        while True:
            try:
                self._ack_q.get_nowait()
            except qmod.Empty:
                break

        self._log(f">> {label}")
        self._send_q.put(msg)

        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set() and self._stream_alive:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._log(f"WARNING: No server response within {timeout}s for: {label}")
                return False
            try:
                ack_for, _, _ = self._ack_q.get(timeout=min(0.25, remaining))
            except qmod.Empty:
                continue

            if expected_ack_type and ack_for:
                if _normalize_ack_type(ack_for) != _normalize_ack_type(expected_ack_type):
                    continue
            return True

        return self._stream_alive

    def _send_no_wait(self, msg, label: str) -> bool:
        """Fire-and-forget: put message on send queue, don't wait for response.
        Used for DataTick — responses are consumed by the background reader."""
        if not self._stream_alive:
            self._log(f"ERROR: Stream dead, cannot send: {label}")
            return False
        self._log(f">> {label}")
        self._send_q.put(msg)
        return True

    def _build_device_ack(self, ack_for_message_type: str, ref_seq: int, message: str):
        ack = self._pb2.DeviceAck(
            ref_seq=ref_seq,
            message=message,
            ack_for_message_type=ack_for_message_type,
        )
        return self._pb2.DeviceToManager(device_ack=ack)

    # ── Message builders ─────────────────────────────────────────────────

    def _build_announcement(self) -> telemetry_pb2.DeviceToManager:
        connect_ms = _now_ms()
        conn_id = f"CONN-{self.ds.serial}-{connect_ms}"
        with self.ds.lock:
            self.ds.connection_id = conn_id
        ann = self._pb2.DeviceAnnouncement(
            device_id=self.ds.device_id,
            serial_number=self.ds.serial,
            software_version=self.ds.sw_version,
            protocol_version=f"telemetry.{self.proto_version}",
            connect_utc_ms=connect_ms,
            connection_id=conn_id,
        )
        return self._pb2.DeviceToManager(device_announcement=ann)

    def _build_profile_metadata(self, profile_name: str, force_new_session: bool = False) -> telemetry_pb2.DeviceToManager:
        profile = self.profiles.get(profile_name, self.profiles.get("minimal", {}))
        metadata_param_ids, _, _ = self._get_effective_profile_param_ids(profile_name)
        selected_param_ids = set(profile.get("selected_param_ids", profile.get("param_ids", [])))

        # For V2 sessions, keep configured profile param ids as the source of
        # truth. Manager-pushed hemodynamics metadata is only used later as a
        # field-level fallback when a configured param is missing in catalog.
        now_ms = _now_ms()

        with self.ds.lock:
            existing_session_id = self.ds.measurement_session_id
            ms_id = existing_session_id
            if force_new_session or not existing_session_id:
                ms_id = f"MS-{self.ds.serial}-{now_ms}"
            self.ds.profile_version += 1
            self.ds.measurement_session_id = ms_id
            self.ds.profile_name = profile_name
            patient_id = self.ds.patient_id or ""
            pv = self.ds.profile_version

        pm = self._pb2.ProfileMetadata(
            device_id=self.ds.device_id,
            measurement_session_id=ms_id,
            patient_id=patient_id,
            connection_id=self.ds.connection_id or "",
            profile_version=pv,
            sent_utc_ms=now_ms,
        )

        if hasattr(pm, "do2i_threshold_mL_min_m2"):
            pm.do2i_threshold_mL_min_m2 = profile.get("do2i_threshold", 280)
        if hasattr(pm, "manual_hgb_g_dL"):
            pm.manual_hgb_g_dL = profile.get("manual_hgb", 12.5)
        if hasattr(pm, "manual_so2_pct"):
            pm.manual_so2_pct = profile.get("manual_so2", 65)
        if hasattr(pm, "flow_source"):
            pm.flow_source = profile.get("flow_source", "Flow_Red")

        outbound_hemo_cache: dict[int, dict] = {}
        for pid in metadata_param_ids:
            cat = self.catalog.get(pid)
            if not cat and self._is_v2:
                with self.ds.lock:
                    hemo_cat = self.ds.hemo_param_catalog.get(pid)
                if hemo_cat:
                    cat = {
                        "name": hemo_cat.get("name", f"Param_{pid}"),
                        "unit": hemo_cat.get("unit", ""),
                        "source_personality": hemo_cat.get("source_personality", "Core Calculated"),
                        "alarm_limit": hemo_cat.get("alarm_limit", {}),
                        "range": hemo_cat.get("range", {}),
                    }
            if not cat:
                continue
            p = pm.params.add()
            p.param_id = pid
            p.param_name = cat["name"]
            p.unit = cat.get("unit", "")
            p.selected = pid in selected_param_ids

            # Source device
            personality = cat.get("source_personality", "Core Calculated")
            p.source_device_personality = personality
            p.source_device_id = self.ds.probes.get(personality, self.ds.serial)

            # Alarm limit
            al = cat.get("alarm_limit", {})
            if al.get("present"):
                p.alarm_limit.present = True
                p.alarm_limit.low = str(al.get("low", ""))
                p.alarm_limit.high = str(al.get("high", ""))

            # Range
            rng = cat.get("range", {})
            if rng.get("present"):
                p.range.present = True
                p.range.display_low = str(rng.get("display_low", ""))
                p.range.display_high = str(rng.get("display_high", ""))
                p.range.operating_low = str(rng.get("operating_low", ""))
                p.range.operating_high = str(rng.get("operating_high", ""))

            outbound_hemo_cache[pid] = {
                "name": p.param_name,
                "unit": p.unit,
                "source_personality": p.source_device_personality,
                "source_device_id": p.source_device_id,
                "selected": bool(p.selected),
                "alarm_limit": {
                    "present": bool(p.alarm_limit.present),
                    "low": str(p.alarm_limit.low),
                    "high": str(p.alarm_limit.high),
                },
                "range": {
                    "present": bool(p.range.present),
                    "display_low": str(p.range.display_low),
                    "display_high": str(p.range.display_high),
                    "operating_low": str(p.range.operating_low),
                    "operating_high": str(p.range.operating_high),
                },
            }

        with self.ds.lock:
            self.ds.hemo_outbound_param_catalog = outbound_hemo_cache
            self.ds.hemo_outbound_profile_version = pv

        return self._pb2.DeviceToManager(profile_metadata=pm)

    def _build_state_event(self, state_str: str, reason: str) -> telemetry_pb2.DeviceToManager:
        state_map = {
            "IDLE":      self._pb2.CORE_STATE_IDLE,
            "STANDBY":   self._pb2.CORE_STATE_STANDBY,
            "MEASURING": self._pb2.CORE_STATE_MEASURING,
        }
        ev = self._pb2.CoreStateEvent(
            device_id=self.ds.device_id,
            measurement_session_id=self.ds.measurement_session_id or "",
            state=state_map.get(state_str, self._pb2.CORE_STATE_UNSPECIFIED),
            state_utc_ms=_now_ms(),
            reason=reason,
        )
        return self._pb2.DeviceToManager(core_state_event=ev)

    def _replay_current_state(self, reason: str) -> bool:
        with self.ds.lock:
            current_state = self.ds.current_state

        msg = self._build_state_event(current_state, reason)
        return self._send_and_wait_ack(
            msg,
            f"CoreStateEvent({current_state}, {reason})",
            expected_ack_type="CoreStateEvent",
        )

    def _build_data_tick(self) -> telemetry_pb2.DeviceToManager:
        with self.ds.lock:
            seq = self.ds.seq_no
            self.ds.seq_no += 1
            tick_data = self.ds.tick_data
            idx = self.ds.tick_index
            self.ds.tick_index += 1
            profile_name = self.ds.profile_name

        param_ids, _, _ = self._get_effective_profile_param_ids(profile_name or "minimal")

        now_ms = _now_ms()
        dt = self._pb2.DataTick(
            device_id=self.ds.device_id,
            measurement_session_id=self.ds.measurement_session_id or "",
            seq_no=seq,
            sample_utc_ms=now_ms,
            state=self._pb2.CORE_STATE_MEASURING,
        )

        for pid in param_ids:
            values_list = tick_data.get(pid)
            if not values_list:
                continue
            pv = dt.values.add()
            pv.param_id = pid
            pv.value = str(values_list[idx % len(values_list)])
            # Source device from probes
            cat = self.catalog.get(pid, {})
            personality = cat.get("source_personality", "Core Calculated")
            pv.source_device_id = self.ds.probes.get(personality, self.ds.serial)

        with self.ds.lock:
            self.ds.total_ticks_sent += 1
            self.ds.last_tick_utc_ms = now_ms
            self.ds.hemo_outbound_last_tick_seq_no = int(seq)
            self.ds.hemo_outbound_last_tick_values = {
                int(value.param_id): str(value.value)
                for value in dt.values
            }

        return self._pb2.DeviceToManager(measurement_data_tick=dt)

    def _is_emr_param(self, param_id: int, profile: dict | None = None) -> bool:
        if profile and param_id in set(profile.get("emr_param_ids", [])):
            return True

        cat = self.catalog.get(param_id, {})
        personality = str(cat.get("source_personality", "")).strip().lower()
        if personality in {
            "hemodynamics",
            "hlm / ecmo",
            "lab measurements",
            "cerebral oximeter",
        }:
            return True
        return False

    def _get_effective_profile_param_ids(self, profile_name: str) -> tuple[list[int], bool, bool]:
        profile = self.profiles.get(profile_name, self.profiles.get("minimal", {}))
        base_param_ids = list(profile.get("metadata_param_ids", profile.get("param_ids", [])))

        with self.ds.lock:
            emr_enabled = bool(self.ds.emr_enabled)
            remove_emr = bool(self.ds.remove_emr_params_from_profile_metadata)

        if emr_enabled or not remove_emr:
            return base_param_ids, emr_enabled, remove_emr

        filtered_param_ids = [pid for pid in base_param_ids if not self._is_emr_param(pid, profile)]
        return filtered_param_ids, emr_enabled, remove_emr

    def _build_patient_bind(self, patient_id: str) -> telemetry_pb2.DeviceToManager:
        # PatientBind is ManagerToDevice in proto, but for simulator we
        # simulate the scenario where Core sends a PatientBind-like event.
        # Since proto puts PatientBind in ManagerToDevice, we'll embed the
        # patient_id in ProfileMetadata.patient_id instead.
        # For this simulator, we just update local state.
        pass

    # ── State transitions ────────────────────────────────────────────────

    def _transition_to_measuring(self, profile_name: str, patient_id: str | None, reason: str = "StartCase") -> bool:
        """STANDBY → MEASURING: ProfileMetadata was already sent during IDLE→STANDBY.
        Just bind patient (if any) and send CoreStateEvent(MEASURING)."""
        with self.ds.lock:
            current = self.ds.current_state
            existing_profile = self.ds.profile_name

        # If profile changed since standby, resend ProfileMetadata
        if profile_name and profile_name != existing_profile:
            if not self._has_accepted_patient():
                self._log("   Cannot resend ProfileMetadata before PatientBind is accepted")
                return False
            self._log(f"   Profile changed ({existing_profile} → {profile_name}), resending ProfileMetadata")
            msg = self._build_profile_metadata(profile_name)
            param_count = len(msg.profile_metadata.params)
            if not self._send_and_wait_ack(
                msg,
                f"ProfileMetadata({profile_name}, {param_count} params)",
                expected_ack_type="ProfileMetadata",
            ):
                return False
            time.sleep(0.3)

        # 1. Update patient
        if patient_id:
            with self.ds.lock:
                self.ds.patient_id = patient_id
                self.ds.pending_patient_id = None
            self._log(f"   Patient bound: {patient_id}")

        # 2. CoreStateEvent(MEASURING)
        msg = self._build_state_event("MEASURING", reason)
        if not self._send_and_wait_ack(msg, "CoreStateEvent(MEASURING)", expected_ack_type="CoreStateEvent"):
            return False

        with self.ds.lock:
            self.ds.current_state = "MEASURING"
            self.ds.case_paused = False
        self._log(f"   Transitioned to MEASURING (profile={profile_name})")
        return True

    def _transition_to_idle(self, reason: str = "StopCase") -> bool:
        """Send CoreStateEvent(IDLE) and stop ticking."""
        msg = self._build_state_event("IDLE", reason)
        ok = self._send_and_wait_ack(
            msg,
            f"CoreStateEvent(IDLE, {reason})",
            expected_ack_type="CoreStateEvent",
        )
        # Update state regardless of ACK success - state change was initiated
        with self.ds.lock:
            self.ds.current_state = "IDLE"
            if reason != "StopCase":
                self.ds.patient_id = None
                self.ds.pending_patient_id = None
            self.ds.seq_no = DEFAULT_TICK_SEQ_NO
            self.ds.tick_index = 0
            self.ds.measurement_session_id = None
            self.ds.case_paused = False
        self._clear_hemodynamics_state(keep_inbound_metadata=True)
        self._log(f"   Transitioned to IDLE (reason: {reason})")
        return ok

    def _transition_to_standby(self, reason: str = "Standby", profile_name: str | None = None) -> bool:
        """Transition to STANDBY.
        From IDLE: requires profile_name → sends ProfileMetadata THEN CoreStateEvent(STANDBY).
        From MEASURING: just sends CoreStateEvent(STANDBY) (profile already active).
        """
        with self.ds.lock:
            current = self.ds.current_state

        if current == "IDLE":
            if not self._has_accepted_patient():
                self._log("   Waiting for PatientBind acceptance before ProfileMetadata (patient_id not accepted yet)")
                return False
            # IDLE → STANDBY: must send ProfileMetadata first
            pname = profile_name or self.ds.profile_name or "minimal"
            self._log(f"   IDLE → STANDBY: sending ProfileMetadata({pname}) first")
            pm_msg = self._build_profile_metadata(pname, force_new_session=True)
            param_count = len(pm_msg.profile_metadata.params)
            if not self._send_and_wait_ack(
                pm_msg,
                f"ProfileMetadata({pname}, {param_count} params)",
                expected_ack_type="ProfileMetadata",
            ):
                self._log("   ERROR: ProfileMetadata send failed, aborting standby")
                return False
            time.sleep(0.3)
            with self.ds.lock:
                self.ds.case_paused = False
        elif current == "MEASURING":
            self._log("   MEASURING → STANDBY: profile already active, sending state event only")
            with self.ds.lock:
                self.ds.case_paused = True
        else:
            self._log(f"   WARNING: Unexpected transition from {current} → STANDBY")

        # Send CoreStateEvent(STANDBY)
        msg = self._build_state_event("STANDBY", reason)
        ok = self._send_and_wait_ack(
            msg,
            f"CoreStateEvent(STANDBY, {reason})",
            expected_ack_type="CoreStateEvent",
        )
        # Update state regardless of ACK success
        with self.ds.lock:
            self.ds.current_state = "STANDBY"
        self._log(f"   Transitioned to STANDBY (reason: {reason})")
        return ok

    # ── Command handling ─────────────────────────────────────────────────

    def _handle_command(self, cmd: dict) -> bool:
        """Process a command from the control panel.
        Enforced state flow: IDLE → STANDBY → MEASURING → STANDBY / IDLE
        """
        cmd_type = cmd.get("type")
        with self.ds.lock:
            current = self.ds.current_state
        self._log(f">>> COMMAND: type={cmd_type}  current_state={current}  detail={cmd}")

        if cmd_type == "start_measuring":
            # Guard: MEASURING only allowed from STANDBY
            if current != "STANDBY":
                self._log(f"    ✗ Cannot start MEASURING from {current} — must be in STANDBY first")
                return False
            profile = cmd.get("profile") or self.ds.profile_name or "minimal"
            patient = cmd.get("patient_id")
            with self.ds.lock:
                event_reason = "ResumeCase" if self.ds.case_paused else "StartCase"
            self._log(f"    → STANDBY → MEASURING with profile={profile}, patient={patient}")
            return self._transition_to_measuring(profile, patient, reason=event_reason)

        elif cmd_type == "stop_measuring":
            self._log(f"    → Stopping (stop_measuring command)")
            return self._transition_to_idle(cmd.get("reason", "StopCase"))

        elif cmd_type == "standby":
            # Guard: STANDBY allowed from IDLE (with profile) or MEASURING
            if current not in ("IDLE", "MEASURING"):
                self._log(f"    ✗ Cannot go to STANDBY from {current}")
                return False
            with self.ds.lock:
                if "emr_enabled" in cmd:
                    self.ds.emr_enabled = bool(cmd.get("emr_enabled"))
                if "remove_emr_params" in cmd:
                    self.ds.remove_emr_params_from_profile_metadata = bool(cmd.get("remove_emr_params"))
            profile = cmd.get("profile")
            self._log(f"    → {current} → STANDBY (profile={profile})")
            if current == "MEASURING":
                default_reason = "StandByCase"
            else:
                emr_enabled = bool(cmd.get("emr_enabled"))
                default_reason = "SetProfile, EMR ON" if emr_enabled else "SetProfile, EMR OFF"
            return self._transition_to_standby(cmd.get("reason") or default_reason, profile_name=profile)

        elif cmd_type == "idle":
            if current == "IDLE":
                self._log(f"    → Already IDLE, ignoring")
                return True
            self._log(f"    → {current} → IDLE")
            return self._transition_to_idle(cmd.get("reason", "StopCase"))

        elif cmd_type == "bind_patient":
            with self.ds.lock:
                self.ds.patient_id = cmd.get("patient_id")
                self.ds.patient_source = _classify_patient_source(self.ds.patient_id)
                self.ds.pending_patient_id = None
                self.ds.pending_patient_source = None
                self.ds.awaiting_backend_patient_bind = False
                self.ds.next_backend_patient_sync_until_ms = None
            self._log(f"    → Patient bound: {self.ds.patient_id}")
            return True

        elif cmd_type == "release_patient":
            with self.ds.lock:
                self.ds.patient_id = None
                self.ds.patient_source = None
                self.ds.pending_patient_id = None
                self.ds.pending_patient_source = None
                self.ds.awaiting_backend_patient_bind = True
                self.ds.next_backend_patient_sync_until_ms = _now_ms() + 60000
            self._log(f"    → Patient released")
            return True

        elif cmd_type == "patient_decision":
            decision = str(cmd.get("decision", "")).lower()
            with self.ds.lock:
                pending = self.ds.pending_patient_id
                current_state = self.ds.current_state
            if not pending:
                self._log("    → No pending patient decision")
                return True

            if decision == "accept":
                with self.ds.lock:
                    self.ds.patient_id = pending
                    self.ds.patient_source = self.ds.pending_patient_source or _classify_patient_source(pending)
                    self.ds.pending_patient_id = None
                    self.ds.pending_patient_source = None
                    self.ds.awaiting_backend_patient_bind = False
                    self.ds.next_backend_patient_sync_until_ms = None
                msg = self._build_state_event(current_state, "Patient ID Accepted")
                return self._send_and_wait_ack(
                    msg,
                    f"CoreStateEvent({current_state}, patient_decision={decision})",
                    expected_ack_type="CoreStateEvent",
                )
            elif decision == "reject":
                with self.ds.lock:
                    # Rejection clears all local patient context so the device remains IDLE-only
                    # until manager issues the next bind.
                    self.ds.current_state = "IDLE"
                    self.ds.patient_id = None
                    self.ds.patient_source = None
                    self.ds.pending_patient_id = None
                    self.ds.pending_patient_source = None
                    self.ds.deferred_patient_id = None
                    self.ds.awaiting_backend_patient_bind = True
                    self.ds.next_backend_patient_sync_until_ms = _now_ms() + 60000
                    self.ds.patient_cooldown_until_ms = _now_ms() + 60000
                reject_msg = self._build_state_event("IDLE", "Patient ID Rejected")
                rejected_ok = self._send_and_wait_ack(
                    reject_msg,
                    "CoreStateEvent(IDLE, patient_decision=reject)",
                    expected_ack_type="CoreStateEvent",
                )
                return rejected_ok
            else:
                self._log(f"    ✗ Unknown patient decision: {decision}")
                return False

        elif cmd_type == "update_tick_data":
            pid = int(cmd.get("param_id", 0))
            vals = cmd.get("values", [])
            with self.ds.lock:
                self.ds.tick_data[pid] = [str(v) for v in vals]
            self._log(f"    → Tick data updated: param {pid} = {vals[:3]}...")
            return True

        elif cmd_type == "set_profile":
            profile = cmd.get("profile", "minimal")
            if not self._has_accepted_patient():
                self._log("    ✗ Cannot send ProfileMetadata before PatientBind is accepted")
                return False
            msg = self._build_profile_metadata(profile)
            return self._send_and_wait_ack(
                msg,
                f"ProfileMetadata({profile})",
                expected_ack_type="ProfileMetadata",
            )

        else:
            self._log(f"    ✗ Unknown command type: {cmd_type}")
            return False

    # ── Main thread loop ─────────────────────────────────────────────────

    def run(self):
        """Thread entry: connect, announce, enter state loop."""
        try:
            # Step 1: Connect + announce + initial state
            if not self._start_session(send_initial_state=True):
                return

            # Step 2: Main loop — NEVER blocked by server I/O
            next_tick_due = time.monotonic() + self._tick_jitter_sec
            next_reconnect_time = 0.0

            while not self.stop_event.is_set():
                self._activate_deferred_patient_if_ready()
                self._maybe_request_backend_patient_bind()

                # If server stream dropped, keep thread alive and reconnect.
                if not self._stream_alive:
                    now = time.time()
                    if now >= next_reconnect_time:
                        self._log("Stream dropped, attempting reconnect...")
                        if self._start_session(send_initial_state=False, replay_current_state=True):
                            self._log("Reconnect successful")
                            next_tick_due = time.monotonic() + self._tick_jitter_sec
                            next_reconnect_time = 0.0
                        else:
                            self._log("Reconnect failed, retrying in 2s")
                            next_reconnect_time = now + 2.0
                    self.stop_event.wait(0.2)
                    continue

                # ── PRIORITY: drain ALL queued commands immediately ───
                while not self.stop_event.is_set():
                    try:
                        cmd = self.ds.command_queue.get_nowait()
                    except qmod.Empty:
                        break
                    cmd_type = cmd.get('type', 'unknown')
                    self._log(f"=== Processing command: {cmd_type} ===")
                    ok = self._handle_command(cmd)
                    if not ok and not self.stop_event.is_set():
                        self._log(f"WARNING: Command returned error: {cmd}")
                    with self.ds.lock:
                        current = self.ds.current_state
                    self._log(f"=== State after command: {current} ===")

                # ── Re-evaluate state after commands ─────────────────
                with self.ds.lock:
                    is_measuring = self.ds.current_state == "MEASURING"

                if is_measuring:
                    # Fire-and-forget DataTick on fixed cadence (non-blocking)
                    now = time.monotonic()
                    if now >= next_tick_due:
                        msg = self._build_data_tick()
                        if self._verbose_tick_logs:
                            vals_str = ", ".join(
                                f"{v.param_id}={v.value}"
                                for v in msg.measurement_data_tick.values
                            )
                        else:
                            vals_str = ", ".join(
                                f"{v.param_id}={v.value}"
                                for v in msg.measurement_data_tick.values[:4]
                            )
                        ok = self._send_no_wait(
                            msg,
                            f"DataTick(seq={msg.measurement_data_tick.seq_no}, [{vals_str}])"
                        )
                        if not ok:
                            self._set_error("DataTick send failed (stream dead); waiting for reconnect")
                            # Don't exit thread. Reconnect path at top of loop handles recovery.
                        while next_tick_due <= now:
                            next_tick_due += self._tick_interval_sec

                    # Short sleep — commands checked every 100ms
                    self.stop_event.wait(0.1)
                else:
                    # Reset cadence anchor when not measuring.
                    next_tick_due = time.monotonic() + self._tick_jitter_sec
                    # Not measuring — poll for commands every 200ms
                    self.stop_event.wait(0.2)

        except Exception as e:
            self._set_error(str(e))
            import traceback
            traceback.print_exc()
        finally:
            self._disconnect()

    def _start_session(self, send_initial_state: bool, replay_current_state: bool = False) -> bool:
        """Establish stream, start response reader, announce and optionally send initial or replayed state."""
        self._connect()
        if not self.ds.connected:
            return False

        # Start response reader BEFORE sending anything, so responses are always consumed.
        self._start_response_reader()

        # Announce
        msg = self._build_announcement()
        if not self._send_and_wait_ack(msg, "DeviceAnnouncement", expected_ack_type="DeviceAnnouncement"):
            self._set_error("Announcement failed")
            return False
        time.sleep(0.2)

        if replay_current_state:
            if not self._replay_current_state("Reconnect"):
                self._set_error("Reconnect state replay failed")
                return False
            return True

        if not send_initial_state:
            return True

        # Initial state setup - send CoreStateEvent for all states
        initial = self.ds.current_state
        if initial == "MEASURING" and self.ds.profile_name:
            if not self._transition_to_measuring(self.ds.profile_name, self.ds.patient_id):
                self._set_error("Initial MEASURING transition failed")
                return False
        elif initial == "STANDBY":
            if not self._transition_to_standby("InitialStandby"):
                self._set_error("Initial STANDBY transition failed")
                return False
        else:
            # Default to IDLE startup event (including unknown/missing initial state)
            msg = self._build_state_event("IDLE", "Startup")
            if not self._send_and_wait_ack(
                msg,
                "CoreStateEvent(IDLE, Startup)",
                expected_ack_type="CoreStateEvent",
            ):
                self._set_error("Initial IDLE state event failed")
                return False

        self._log(f"Ready  state={self.ds.current_state}")
        return True

    # ── Helpers ───────────────────────────────────────────────────────────

    def _connect(self):
        target = f"{self.server_cfg['host']}:{self.server_cfg['port']}"
        self._log(f"Connecting to {target}...")
        try:
            if self.channel:
                try:
                    self.channel.close()
                except Exception:
                    pass
                self.channel = None

            while True:
                try:
                    self._send_q.get_nowait()
                except qmod.Empty:
                    break

            self.channel = self.channel_factory()
            try:
                grpc.channel_ready_future(self.channel).result(timeout=5)
            except grpc.FutureTimeoutError:
                self._log("WARNING: Channel not ready in 5s — proceeding anyway")

            self.stub = self._stub_class(self.channel)
            self.stream = self.stub.TelemetrySession(self._request_generator())
            self._stream_alive = True
            while True:
                try:
                    self._ack_q.get_nowait()
                except qmod.Empty:
                    break
            with self.ds.lock:
                self.ds.connected = True
                self.ds.error = None
            self._log("Connected ✓")
        except Exception as e:
            self._set_error(f"Connection failed: {e}")

    def _disconnect(self):
        self._log("Disconnecting...")
        with self.ds.lock:
            self.ds.connected = False
        if self.channel:
            try:
                self.channel.close()
            except Exception:
                pass
        self._log("Disconnected.")

    def _set_error(self, err: str):
        self._log(f"ERROR: {err}")
        with self.ds.lock:
            self.ds.error = err
            self.ds.connected = False

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        print(f"{ts} {self._tag} {msg}")
