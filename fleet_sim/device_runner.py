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
from typing import Optional

import grpc

# Proto stubs (path resolved by run_fleet.py before import)
import telemetry_pb2
import telemetry_pb2_grpc

from fleet_sim.state_store import DeviceState, StateStore


def _now_ms() -> int:
    return int(time.time() * 1000)


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

        self.channel: Optional[grpc.Channel] = None
        self.stub = None
        self.stream = None
        self._send_q: qmod.Queue = qmod.Queue()
        self._tag = f"[{self.ds.device_id}]"
        self._ack_q: qmod.Queue = qmod.Queue()

        # Background response reader state
        self._stream_alive = True
        self._tick_interval_sec = float(self.server_cfg.get("tick_interval_sec", 1.0))
        raw_jitter_ms = int(self.server_cfg.get("tick_jitter_ms", 180))
        max_safe_jitter_ms = max(0, int(self._tick_interval_sec * 400))
        jitter_ms = max(0, min(raw_jitter_ms, max_safe_jitter_ms))
        seed = sum(ord(c) for c in self.ds.device_id)
        self._tick_jitter_sec = (seed % (jitter_ms + 1)) / 1000.0 if jitter_ms > 0 else 0.0

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
                self._log(f"<< PatientBind (patient={resp.patient_bind.patient_id})")
            elif resp.HasField("patient_release"):
                self._log(f"<< PatientRelease (patient={resp.patient_release.patient_id})")
            else:
                self._log("<< Unknown response")
        except Exception as e:
            self._log(f"<< Response parse error: {e}")

    def _send_and_wait_ack(self, msg, label: str, timeout: float = 5.0, expected_ack_type: str | None = None) -> bool:
        """Send a message and wait for the background reader to receive a response.
        Used for critical messages: Announcement, ProfileMetadata, CoreStateEvent."""
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
                return True
            try:
                ack_for, _, _ = self._ack_q.get(timeout=min(0.25, remaining))
            except qmod.Empty:
                continue

            if expected_ack_type and ack_for and ack_for != expected_ack_type:
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

    # ── Message builders ─────────────────────────────────────────────────

    def _build_announcement(self) -> telemetry_pb2.DeviceToManager:
        connect_ms = _now_ms()
        conn_id = f"CONN-{self.ds.serial}-{connect_ms}"
        with self.ds.lock:
            self.ds.connection_id = conn_id
        ann = telemetry_pb2.DeviceAnnouncement(
            device_id=self.ds.device_id,
            serial_number=self.ds.serial,
            software_version=self.ds.sw_version,
            protocol_version="telemetry.v1",
            connect_utc_ms=connect_ms,
            connection_id=conn_id,
        )
        return telemetry_pb2.DeviceToManager(device_announcement=ann)

    def _build_profile_metadata(self, profile_name: str) -> telemetry_pb2.DeviceToManager:
        profile = self.profiles.get(profile_name, self.profiles.get("minimal", {}))
        metadata_param_ids = profile.get("metadata_param_ids", profile.get("param_ids", []))
        selected_param_ids = set(profile.get("selected_param_ids", profile.get("param_ids", [])))
        now_ms = _now_ms()
        ms_id = f"MS-{self.ds.serial}-{now_ms}"

        with self.ds.lock:
            self.ds.profile_version += 1
            self.ds.measurement_session_id = ms_id
            self.ds.profile_name = profile_name
            pv = self.ds.profile_version

        pm = telemetry_pb2.ProfileMetadata(
            device_id=self.ds.device_id,
            measurement_session_id=ms_id,
            connection_id=self.ds.connection_id or "",
            profile_version=pv,
            sent_utc_ms=now_ms,
            do2i_threshold_mL_min_m2=profile.get("do2i_threshold", 280),
            manual_hgb_g_dL=profile.get("manual_hgb", 12.5),
            manual_so2_pct=profile.get("manual_so2", 65),
            flow_source=profile.get("flow_source", "Flow_Red"),
        )

        for pid in metadata_param_ids:
            cat = self.catalog.get(pid)
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

        return telemetry_pb2.DeviceToManager(profile_metadata=pm)

    def _build_state_event(self, state_str: str, reason: str) -> telemetry_pb2.DeviceToManager:
        state_map = {
            "IDLE":      telemetry_pb2.CORE_STATE_IDLE,
            "STANDBY":   telemetry_pb2.CORE_STATE_STANDBY,
            "MEASURING": telemetry_pb2.CORE_STATE_MEASURING,
        }
        ev = telemetry_pb2.CoreStateEvent(
            device_id=self.ds.device_id,
            measurement_session_id=self.ds.measurement_session_id or "",
            state=state_map.get(state_str, telemetry_pb2.CORE_STATE_UNSPECIFIED),
            state_utc_ms=_now_ms(),
            reason=reason,
        )
        return telemetry_pb2.DeviceToManager(core_state_event=ev)

    def _build_data_tick(self) -> telemetry_pb2.DeviceToManager:
        with self.ds.lock:
            seq = self.ds.seq_no
            self.ds.seq_no += 1
            tick_data = self.ds.tick_data
            idx = self.ds.tick_index
            self.ds.tick_index += 1
            profile_name = self.ds.profile_name

        profile = self.profiles.get(profile_name or "minimal", {})
        param_ids = profile.get("metadata_param_ids", profile.get("param_ids", []))

        now_ms = _now_ms()
        dt = telemetry_pb2.DataTick(
            device_id=self.ds.device_id,
            measurement_session_id=self.ds.measurement_session_id or "",
            seq_no=seq,
            sample_utc_ms=now_ms,
            state=telemetry_pb2.CORE_STATE_MEASURING,
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

        return telemetry_pb2.DeviceToManager(measurement_data_tick=dt)

    def _build_patient_bind(self, patient_id: str) -> telemetry_pb2.DeviceToManager:
        # PatientBind is ManagerToDevice in proto, but for simulator we
        # simulate the scenario where Core sends a PatientBind-like event.
        # Since proto puts PatientBind in ManagerToDevice, we'll embed the
        # patient_id in ProfileMetadata.patient_id instead.
        # For this simulator, we just update local state.
        pass

    # ── State transitions ────────────────────────────────────────────────

    def _transition_to_measuring(self, profile_name: str, patient_id: str | None) -> bool:
        """STANDBY → MEASURING: ProfileMetadata was already sent during IDLE→STANDBY.
        Just bind patient (if any) and send CoreStateEvent(MEASURING)."""
        with self.ds.lock:
            current = self.ds.current_state
            existing_profile = self.ds.profile_name

        # If profile changed since standby, resend ProfileMetadata
        if profile_name and profile_name != existing_profile:
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
            self._log(f"   Patient bound: {patient_id}")

        # 2. CoreStateEvent(MEASURING)
        msg = self._build_state_event("MEASURING", "StartCase")
        if not self._send_and_wait_ack(msg, "CoreStateEvent(MEASURING)", expected_ack_type="CoreStateEvent"):
            return False

        with self.ds.lock:
            self.ds.current_state = "MEASURING"
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
            self.ds.patient_id = None
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
            # IDLE → STANDBY: must send ProfileMetadata first
            pname = profile_name or "minimal"
            self._log(f"   IDLE → STANDBY: sending ProfileMetadata({pname}) first")
            pm_msg = self._build_profile_metadata(pname)
            param_count = len(pm_msg.profile_metadata.params)
            if not self._send_and_wait_ack(
                pm_msg,
                f"ProfileMetadata({pname}, {param_count} params)",
                expected_ack_type="ProfileMetadata",
            ):
                self._log("   ERROR: ProfileMetadata send failed, aborting standby")
                return False
            time.sleep(0.3)
        elif current == "MEASURING":
            self._log("   MEASURING → STANDBY: profile already active, sending state event only")
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
            self._log(f"    → STANDBY → MEASURING with profile={profile}, patient={patient}")
            return self._transition_to_measuring(profile, patient)

        elif cmd_type == "stop_measuring":
            self._log(f"    → Stopping (stop_measuring command)")
            return self._transition_to_idle(cmd.get("reason", "StopCase"))

        elif cmd_type == "standby":
            # Guard: STANDBY allowed from IDLE (with profile) or MEASURING
            if current not in ("IDLE", "MEASURING"):
                self._log(f"    ✗ Cannot go to STANDBY from {current}")
                return False
            profile = cmd.get("profile")
            self._log(f"    → {current} → STANDBY (profile={profile})")
            return self._transition_to_standby(cmd.get("reason", "Standby"), profile_name=profile)

        elif cmd_type == "idle":
            if current == "IDLE":
                self._log(f"    → Already IDLE, ignoring")
                return True
            self._log(f"    → {current} → IDLE")
            return self._transition_to_idle(cmd.get("reason", "ReturnToIdle"))

        elif cmd_type == "bind_patient":
            with self.ds.lock:
                self.ds.patient_id = cmd.get("patient_id")
            self._log(f"    → Patient bound: {self.ds.patient_id}")
            return True

        elif cmd_type == "release_patient":
            with self.ds.lock:
                self.ds.patient_id = None
            self._log(f"    → Patient released")
            return True

        elif cmd_type == "update_tick_data":
            pid = int(cmd.get("param_id", 0))
            vals = cmd.get("values", [])
            with self.ds.lock:
                self.ds.tick_data[pid] = [str(v) for v in vals]
            self._log(f"    → Tick data updated: param {pid} = {vals[:3]}...")
            return True

        elif cmd_type == "set_profile":
            profile = cmd.get("profile", "minimal")
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
                # If server stream dropped, keep thread alive and reconnect.
                if not self._stream_alive:
                    now = time.time()
                    if now >= next_reconnect_time:
                        self._log("Stream dropped, attempting reconnect...")
                        if self._start_session(send_initial_state=False):
                            self._log("Reconnect successful")
                            next_tick_due = time.monotonic() + self._tick_jitter_sec
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

    def _start_session(self, send_initial_state: bool) -> bool:
        """Establish stream, start response reader, announce and (optionally) send initial state."""
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
            self.channel = self.channel_factory()
            try:
                grpc.channel_ready_future(self.channel).result(timeout=5)
            except grpc.FutureTimeoutError:
                self._log("WARNING: Channel not ready in 5s — proceeding anyway")

            self.stub = telemetry_pb2_grpc.TelemetryServiceStub(self.channel)
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
