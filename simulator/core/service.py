from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from simulator.api.emr_client import EMRClient
from simulator.core.config_loader import (
    load_emr_api_base_url,
    load_emr_tls_verify,
    load_device_configs,
    load_emr_oauth_config,
    load_emr_profiles,
    load_parameter_definitions,
)
from simulator.core.models import DeviceConfig, ParameterState
from simulator.core.thread_manager import start_device_threads
from simulator.simulator.emr_runner import run_emr_simulation_loop


class EMRSimulatorService:
    def __init__(
        self,
        config_dir: str | Path,
        api_base_url: str,
        access_token: str | None = None,
        verify_ssl: bool | str = True,
        dry_run: bool = True,
    ):
        self.config_dir = Path(config_dir)
        self.api_base_url = api_base_url
        self.dry_run = dry_run

        self._devices: dict[str, DeviceConfig] = {}
        self._profiles: dict[str, dict] = {}
        self._param_templates: list[ParameterState] = []
        self._status: dict[str, dict] = {}
        self._status_locks: dict[str, threading.Lock] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

        self._client = EMRClient(
            base_url=api_base_url,
            access_token=access_token,
            verify_ssl=verify_ssl,
            dry_run=dry_run,
        )

    def load(self) -> None:
        single_file = self.config_dir / "emr_config.yaml"
        if single_file.exists():
            params_file = single_file
            devices_file = single_file
            profiles_file = single_file
            oauth_file = single_file
            loaded_api_base_url = load_emr_api_base_url(single_file, self.api_base_url)
            loaded_verify_ssl = load_emr_tls_verify(single_file)
        else:
            params_file = self.config_dir / "emr_params.yaml"
            devices_file = self.config_dir / "emr_devices.yaml"
            profiles_file = self.config_dir / "emr_profiles.yaml"
            oauth_file = self.config_dir / "emr_oauth.yaml"
            loaded_api_base_url = self.api_base_url
            loaded_verify_ssl = None

        self.api_base_url = (loaded_api_base_url or self.api_base_url).rstrip("/")
        self._client.base_url = self.api_base_url

        # CLI --emr-no-verify-ssl sets verify_ssl=False and must override config.
        if self._client.verify_ssl is not False and loaded_verify_ssl is not None:
            if isinstance(loaded_verify_ssl, str):
                ca_path = Path(loaded_verify_ssl)
                if not ca_path.is_absolute():
                    ca_path = (self.config_dir / ca_path).resolve()
                if not ca_path.exists():
                    raise ValueError(f"Configured ca_cert_path does not exist: {ca_path}")
                self._client.verify_ssl = str(ca_path)
            else:
                self._client.verify_ssl = loaded_verify_ssl

        definitions = load_parameter_definitions(params_file)
        known_params = {item.name for item in definitions}
        self._profiles = load_emr_profiles(profiles_file, known_params)
        oauth_cfg = load_emr_oauth_config(oauth_file)
        if oauth_cfg and not self._client.access_token:
            self._client.oauth_config = oauth_cfg
        devices = load_device_configs(devices_file)

        self._param_templates = [
            ParameterState(definition=item, current_value=(item.min_value + item.max_value) / 2.0)
            for item in definitions
        ]

        for device in devices:
            if not device.enabled or device.mode != "emr":
                continue
            if device.profile not in self._profiles:
                raise ValueError(
                    f"Device '{device.device_id}' uses unknown EMR profile '{device.profile}'"
                )
            self._devices[device.device_id] = device
            self._status_locks[device.device_id] = threading.Lock()
            self._status[device.device_id] = {
                "active": False,
                "case": device.case,
                "profile": device.profile,
                "phase": "baseline",
                "patient_id": None,
                "encounter_id": None,
                "mapped_device_id": None,
                "lookup_device_id": device.emr_lookup_device_id,
                "last_sent": None,
                "last_error": None,
                "last_values": {},
                "last_payload_status": "idle",
            }

    def start(self) -> None:
        targets: list[tuple[str, Callable[[], None]]] = []
        for device_id, device in self._devices.items():
            stop_event = threading.Event()
            self._stop_events[device_id] = stop_event
            local_states = [
                ParameterState(definition=item.definition, current_value=item.current_value)
                for item in self._param_templates
            ]
            lock = self._status_locks[device_id]
            status_ref = self._status[device_id]

            def _target(
                d: DeviceConfig = device,
                s: list[ParameterState] = local_states,
                l: threading.Lock = lock,
                st: dict = status_ref,
                ev: threading.Event = stop_event,
            ) -> None:
                run_emr_simulation_loop(
                    device=d,
                    states=s,
                    client=self._client,
                    profiles=self._profiles,
                    status_lock=l,
                    status=st,
                    stop_event=ev,
                    find_conflict_device=self.find_active_conflict_device,
                )

            targets.append((device_id, _target))

        self._threads = start_device_threads(targets)

    def shutdown(self) -> None:
        for device_id in self._devices.keys():
            with self._status_locks[device_id]:
                if not self._status[device_id].get("active"):
                    continue
            self._notify_inactive_status(device_id)

        for event in self._stop_events.values():
            event.set()
        for thread in self._threads.values():
            thread.join(timeout=2)

    def _notify_inactive_status(self, device_id: str) -> None:
        if device_id not in self._devices:
            return

        cfg = self._devices[device_id]
        with self._status_locks[device_id]:
            patient_id = self._status[device_id].get("patient_id")

        # Fallback: resolve pid from configured patient identifier when runtime mapping
        # is empty (for example after clear-data or stop-before-first-send).
        if not patient_id:
            patient = self._client.get_patient(cfg.patient_identifier)
            if patient and patient.get("patient_id"):
                patient_id = str(patient.get("patient_id"))

        if not patient_id:
            return

        ok, status_msg = self._client.send_device_status(
            lookup_device_id=cfg.emr_lookup_device_id,
            patient_id=str(patient_id),
            status="Inactive",
        )

        with self._status_locks[device_id]:
            if ok:
                self._status[device_id]["last_payload_status"] = "device_marked_inactive"
                self._status[device_id]["last_error"] = None
            else:
                self._status[device_id]["last_payload_status"] = "inactive_status_failed"
                self._status[device_id]["last_error"] = status_msg

    def list_devices(self) -> dict:
        devices = []
        for device_id, device in self._devices.items():
            with self._status_locks[device_id]:
                status = dict(self._status[device_id])
            devices.append(
                {
                    "device_id": device.device_id,
                    "mode": device.mode,
                    "case": status.get("case", device.case),
                    "profile": status.get("profile", device.profile),
                    **status,
                    "parameters": status.get("last_values", {}),
                }
            )
        return {"devices": devices}

    def find_active_conflict_device(self, device_id: str, patient_id: str) -> str | None:
        """Return active device id using the same patient, excluding device_id."""
        target_pid = str(patient_id)
        for other_id in self._devices.keys():
            if other_id == device_id:
                continue
            lock = self._status_locks.get(other_id)
            if not lock:
                continue
            with lock:
                other = self._status[other_id]
                if other.get("active") and str(other.get("patient_id") or "") == target_pid:
                    return other_id
        return None

    def start_device_with_reason(self, device_id: str) -> tuple[bool, str]:
        if device_id not in self._devices:
            return False, "device_not_found"

        with self._status_locks[device_id]:
            patient_id = self._status[device_id].get("patient_id")

        if patient_id:
            conflict_id = self.find_active_conflict_device(device_id, str(patient_id))
            if conflict_id:
                with self._status_locks[device_id]:
                    self._status[device_id]["active"] = False
                    self._status[device_id]["last_payload_status"] = "patient_conflict_active_device"
                    self._status[device_id]["last_error"] = (
                        f"patient {patient_id} already active on device {conflict_id}"
                    )
                return False, f"patient_conflict:{conflict_id}"

        with self._status_locks[device_id]:
            self._status[device_id]["active"] = True
            self._status[device_id]["last_payload_status"] = "running"
            self._status[device_id]["last_error"] = None
        return True, "ok"

    def start_device(self, device_id: str) -> bool:
        ok, _ = self.start_device_with_reason(device_id)
        return ok

    def sync_patient(self, device_id: str) -> tuple[bool, str | None]:
        """Fetch patient id and mapped device id, then cache in runtime status."""
        if device_id not in self._devices:
            return False, None

        with self._status_locks[device_id]:
            existing = self._status[device_id].get("patient_id")
            existing_device = self._status[device_id].get("mapped_device_id")
            if existing and existing_device:
                return True, str(existing)

        cfg = self._devices[device_id]

        # Prefer the latest assignment row for this device so PID follows EMR mapping.
        assignment = self._client.get_device_assignment(cfg.emr_lookup_device_id)
        if assignment and assignment.get("device_id") and assignment.get("patient_id"):
            patient_id = str(assignment.get("patient_id"))
            with self._status_locks[device_id]:
                self._status[device_id]["patient_id"] = patient_id
                self._status[device_id]["encounter_id"] = (
                    str(assignment.get("encounter_id")) if assignment.get("encounter_id") else None
                )
                self._status[device_id]["mapped_device_id"] = str(assignment.get("device_id"))
                self._status[device_id]["last_payload_status"] = "patient_and_device_mapped"
            return True, patient_id

        # Fallback: resolve patient by configured identifier, then check assignment for that patient.
        patient = self._client.get_patient(cfg.patient_identifier)
        with self._status_locks[device_id]:
            if not (patient and patient.get("patient_id")):
                self._status[device_id]["patient_id"] = None
                self._status[device_id]["encounter_id"] = None
                self._status[device_id]["mapped_device_id"] = None
                self._status[device_id]["last_payload_status"] = "waiting_for_patient"
                return False, None

        patient_id = str(patient.get("patient_id"))
        assignment = self._client.get_device_assignment(cfg.emr_lookup_device_id, patient_id)

        with self._status_locks[device_id]:
            if assignment and assignment.get("device_id"):
                self._status[device_id]["patient_id"] = patient_id
                self._status[device_id]["encounter_id"] = (
                    str(assignment.get("encounter_id")) if assignment.get("encounter_id") else None
                )
                self._status[device_id]["mapped_device_id"] = str(assignment.get("device_id"))
                self._status[device_id]["last_payload_status"] = "patient_and_device_mapped"
                return True, patient_id

            # Keep patient_id empty until assignment exists for this specific device.
            self._status[device_id]["patient_id"] = None
            self._status[device_id]["encounter_id"] = None
            self._status[device_id]["mapped_device_id"] = None
            self._status[device_id]["last_payload_status"] = "waiting_for_patient_and_device_assignment"
            return False, None

    def stop_device(self, device_id: str) -> bool:
        if device_id not in self._devices:
            return False
        self._notify_inactive_status(device_id)
        with self._status_locks[device_id]:
            self._status[device_id]["active"] = False
            self._status[device_id]["last_payload_status"] = "stopped"
            self._status[device_id]["patient_id"] = None
            self._status[device_id]["encounter_id"] = None
            self._status[device_id]["mapped_device_id"] = None
        return True

    def stop_all_devices(self) -> int:
        stopped = 0
        for device_id in self._devices.keys():
            self._notify_inactive_status(device_id)
            with self._status_locks[device_id]:
                if self._status[device_id]["active"]:
                    stopped += 1
                self._status[device_id]["active"] = False
                self._status[device_id]["last_payload_status"] = "stopped"
                self._status[device_id]["patient_id"] = None
                self._status[device_id]["encounter_id"] = None
                self._status[device_id]["mapped_device_id"] = None
        return stopped

    def clear_runtime_data(self) -> int:
        """Clear runtime EMR device data shown in UI without shutting down the process."""
        cleared = 0
        for device_id, cfg in self._devices.items():
            with self._status_locks[device_id]:
                status = self._status[device_id]
                status["patient_id"] = None
                status["encounter_id"] = None
                status["mapped_device_id"] = None
                status["phase"] = "baseline"
                status["last_sent"] = None
                status["last_error"] = None
                status["last_values"] = {}
                status["case"] = cfg.case
                status["profile"] = cfg.profile
                status["last_payload_status"] = "running" if status.get("active") else "idle"
                cleared += 1
        return cleared

    def set_case(self, device_id: str, case_name: str) -> bool:
        if device_id not in self._devices:
            return False
        case_name = case_name.lower()
        if case_name not in ("within_range", "outside_range", "low_limit", "high_limit", "mixed"):
            return False
        with self._status_locks[device_id]:
            self._status[device_id]["case"] = case_name
            self._status[device_id]["last_payload_status"] = "case_updated"
        old_cfg = self._devices[device_id]
        self._devices[device_id] = DeviceConfig(
            device_id=old_cfg.device_id,
            mode=old_cfg.mode,
            emr_lookup_device_id=old_cfg.emr_lookup_device_id,
            device_type=old_cfg.device_type,
            patient_identifier=old_cfg.patient_identifier,
            notes=old_cfg.notes,
            poll_interval=old_cfg.poll_interval,
            send_interval=old_cfg.send_interval,
            enabled=old_cfg.enabled,
            case=case_name,
            profile=old_cfg.profile,
        )
        return True

    def set_scenario(self, device_id: str, scenario: str) -> bool:
        """Backward-compat shim for older callers using normal/abnormal."""
        mapping = {
            "normal": "within_range",
            "abnormal": "outside_range",
        }
        return self.set_case(device_id, mapping.get(scenario.lower(), scenario.lower()))

    def set_profile(self, device_id: str, profile: str) -> bool:
        if device_id not in self._devices:
            return False
        profile = profile.strip()
        if profile not in self._profiles:
            return False
        with self._status_locks[device_id]:
            self._status[device_id]["profile"] = profile
            self._status[device_id]["phase"] = "baseline"
            self._status[device_id]["last_payload_status"] = "profile_updated"
        old_cfg = self._devices[device_id]
        self._devices[device_id] = DeviceConfig(
            device_id=old_cfg.device_id,
            mode=old_cfg.mode,
            emr_lookup_device_id=old_cfg.emr_lookup_device_id,
            device_type=old_cfg.device_type,
            patient_identifier=old_cfg.patient_identifier,
            notes=old_cfg.notes,
            poll_interval=old_cfg.poll_interval,
            send_interval=old_cfg.send_interval,
            enabled=old_cfg.enabled,
            case=old_cfg.case,
            profile=profile,
        )
        return True

    def list_profiles(self) -> dict[str, dict]:
        return self._profiles

    @property
    def dry_run_mode(self) -> bool:
        return self.dry_run

    @property
    def device_count(self) -> int:
        return len(self._devices)
