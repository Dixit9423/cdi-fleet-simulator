from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from simulator.core.models import DeviceConfig, ParameterState


def build_emr_payload(
    device: DeviceConfig,
    patient: dict[str, Any],
    parameters: list[ParameterState],
) -> dict[str, Any]:
    values: dict[str, float] = {}
    for state in parameters:
        values[state.definition.name] = round(float(state.current_value), 2)

    pid_raw = patient.get("patient_id")
    try:
        pid = int(str(pid_raw)) if pid_raw is not None else 0
    except ValueError:
        pid = 0

    def _as_text(name: str) -> str:
        return str(values.get(name, ""))

    payload: dict[str, Any] = {
        "pid": pid,
        "device_type": device.device_type,
        "device_id": str(patient.get("device_id") or device.device_id),
        "pulse_rate": _as_text("heart_rate"),
        "systolic_bp": _as_text("systolic_bp"),
        "diastolic_bp": _as_text("diastolic_bp"),
        "mean_arterial_pressure": _as_text("map"),
        "etco2": _as_text("etco2"),
        "arterial_flow": _as_text("arterial_flow"),
        "sweep": _as_text("sweep"),
        "fio2": _as_text("fio2"),
        "cerebral_oxygen_saturation": _as_text("rso2"),
        "act": _as_text("act"),
        "lactate": _as_text("lactate"),
        "glucose": _as_text("glucose"),
        "notes": device.notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return payload
