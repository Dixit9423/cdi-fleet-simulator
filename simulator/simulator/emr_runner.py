from __future__ import annotations

import logging
import threading
import time
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
) -> None:
    log = logging.getLogger("simulator.emr_runner")
    last_poll = 0.0
    tick_index = 0

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
                elif patient and patient.get("patient_id"):
                    status["patient_id"] = str(patient.get("patient_id"))
                    status["encounter_id"] = None
                    status["mapped_device_id"] = None
                    status["last_payload_status"] = "waiting_for_device_assignment"
                else:
                    status["patient_id"] = None
                    status["encounter_id"] = None
                    status["mapped_device_id"] = None
                    status["last_payload_status"] = "waiting_for_patient"

        with status_lock:
            patient_id = status.get("patient_id")
            encounter_id = status.get("encounter_id")
            mapped_device_id = status.get("mapped_device_id")
            case_name = status.get("case", device.case)
            profile_name = status.get("profile", device.profile)

        if not patient_id or not mapped_device_id:
            with status_lock:
                if not patient_id:
                    status["last_payload_status"] = "waiting_for_patient"
                elif not mapped_device_id:
                    status["last_payload_status"] = "waiting_for_device_assignment"
            time.sleep(float(device.send_interval))
            continue

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
            log.debug("EMR send failure for %s: %s", device.device_id, send_status)

        time.sleep(float(device.send_interval))
