from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from simulator.api.emr_client import EMRClient
from simulator.core.models import DeviceConfig, ParameterState
from simulator.payloads.emr_payload import build_emr_payload
from simulator.simulator.generator import update_all


def run_emr_simulation_loop(
    device: DeviceConfig,
    states: list[ParameterState],
    client: EMRClient,
    profiles: dict[str, dict],
    status_lock: threading.Lock,
    status: dict,
    stop_event: threading.Event,
    find_conflict_device: Callable[[str, str], str | None] | None = None,
) -> None:
    log = logging.getLogger("simulator.emr_runner")
    last_poll = 0.0
    tick_index = 0
    last_wait_reason: str | None = None
    last_mapping_key: tuple[str | None, str | None] = (None, None)
    last_conflict_device: str | None = None

    log.info(
        "runner_started device_id=%s profile=%s case=%s poll_interval=%ss send_interval=%ss",
        device.device_id,
        device.profile,
        device.case,
        device.poll_interval,
        device.send_interval,
    )

    while not stop_event.is_set():
        now = time.monotonic()

        with status_lock:
            active = bool(status.get("active", False))
            mapped_patient_id = status.get("patient_id")
            mapped_device_id = status.get("mapped_device_id")

        if not active:
            time.sleep(0.2)
            continue

        patient = None
        assignment = None
        needs_mapping = (not mapped_patient_id) or (not mapped_device_id)
        if needs_mapping and ((now - last_poll) >= float(device.poll_interval)):
            last_poll = now
            assignment = client.get_device_assignment(device.emr_lookup_device_id)

            if not (assignment and assignment.get("device_id") and assignment.get("patient_id")):
                patient = client.get_patient(device.patient_identifier)
                if patient and patient.get("patient_id"):
                    assignment = client.get_device_assignment(
                        device.emr_lookup_device_id,
                        str(patient.get("patient_id")),
                    )

            with status_lock:
                if assignment and assignment.get("device_id") and assignment.get("patient_id"):
                    status["patient_id"] = str(assignment.get("patient_id"))
                    status["encounter_id"] = (
                        str(assignment.get("encounter_id")) if assignment.get("encounter_id") else None
                    )
                    status["mapped_device_id"] = str(assignment.get("device_id"))
                    status["last_payload_status"] = "patient_and_device_mapped"
                    mapping_key = (status["patient_id"], status["mapped_device_id"])
                    if mapping_key != last_mapping_key:
                        log.info(
                            "mapping_ready device_id=%s patient_id=%s mapped_device_id=%s",
                            device.device_id,
                            status["patient_id"],
                            status["mapped_device_id"],
                        )
                        last_mapping_key = mapping_key
                    last_wait_reason = None
                elif patient and patient.get("patient_id"):
                    candidate_patient_id = str(patient.get("patient_id"))
                    status["patient_id"] = None
                    status["encounter_id"] = None
                    status["mapped_device_id"] = None
                    status["last_payload_status"] = "waiting_for_patient_and_device_assignment"
                    if last_wait_reason != "waiting_for_device_assignment":
                        log.info(
                            "mapping_wait device_id=%s patient_id=%s reason=waiting_for_patient_and_device_assignment",
                            device.device_id,
                            candidate_patient_id,
                        )
                        last_wait_reason = "waiting_for_device_assignment"
                else:
                    status["patient_id"] = None
                    status["encounter_id"] = None
                    status["mapped_device_id"] = None
                    status["last_payload_status"] = "waiting_for_patient_and_device_assignment"
                    if last_wait_reason != "waiting_for_patient":
                        log.info(
                            "mapping_wait device_id=%s reason=waiting_for_patient_and_device_assignment",
                            device.device_id,
                        )
                        last_wait_reason = "waiting_for_patient"

        with status_lock:
            patient_id = status.get("patient_id")
            encounter_id = status.get("encounter_id")
            mapped_device_id = status.get("mapped_device_id")
            case_name = status.get("case", device.case)
            profile_name = status.get("profile", device.profile)

        if not patient_id or not mapped_device_id:
            with status_lock:
                if not patient_id:
                    status["last_payload_status"] = "waiting_for_patient_and_device_assignment"
                elif not mapped_device_id:
                    status["last_payload_status"] = "waiting_for_device_assignment"
            time.sleep(float(device.send_interval))
            continue

        conflict_device_id = (
            find_conflict_device(device.device_id, str(patient_id)) if find_conflict_device else None
        )
        if conflict_device_id:
            with status_lock:
                status["last_payload_status"] = "patient_conflict_active_device"
                status["last_error"] = (
                    f"patient {patient_id} already active on device {conflict_device_id}"
                )
            if last_conflict_device != conflict_device_id:
                log.warning(
                    "send_blocked_patient_conflict device_id=%s patient_id=%s conflict_device_id=%s",
                    device.device_id,
                    patient_id,
                    conflict_device_id,
                )
                last_conflict_device = conflict_device_id
            time.sleep(float(device.send_interval))
            continue
        if last_conflict_device is not None:
            log.info(
                "send_unblocked_patient_conflict_resolved device_id=%s patient_id=%s",
                device.device_id,
                patient_id,
            )
            last_conflict_device = None

        profile_cfg = profiles.get(profile_name, {})
        values, phase = update_all(states, case_name, profile_cfg, tick_index)
        tick_index += 1
        patient_payload = {
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "device_id": mapped_device_id,
        }
        payload = build_emr_payload(device, patient_payload, states)
        ok, send_status = client.send_device_data(payload)

        with status_lock:
            status["last_values"] = values
            status["phase"] = phase
            status["last_payload_status"] = send_status
            status["last_sent"] = datetime.now(timezone.utc).isoformat() if ok else status.get("last_sent")
            status["last_error"] = None if ok else send_status

        if not ok:
            log.warning(
                "send_failed device_id=%s patient_id=%s mapped_device_id=%s status=%s",
                device.device_id,
                patient_id,
                mapped_device_id,
                send_status,
            )
        else:
            log.info(
                "send_ok device_id=%s patient_id=%s mapped_device_id=%s phase=%s tick=%s",
                device.device_id,
                patient_id,
                mapped_device_id,
                phase,
                tick_index,
            )

        time.sleep(float(device.send_interval))

    log.info("runner_stopped device_id=%s", device.device_id)
