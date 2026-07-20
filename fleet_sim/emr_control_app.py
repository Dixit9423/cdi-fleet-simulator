"""
fleet_sim/emr_control_app.py
────────────────────────────
Standalone EMR control panel served on a separate port.
This avoids UI port conflicts with the CDI Fleet simulator UI.
"""

import os
import sys
import threading
import time
import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

_emr_service = None
log = logging.getLogger("simulator.emr_control_api")


def set_emr_service(emr_service):
    global _emr_service
    _emr_service = emr_service


app = FastAPI(
    title="EMR Hemodynamic Simulator — Control Panel",
    version="1.0.0",
    description="Standalone control panel for EMR simulator mode",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def emr_control_panel_ui():
    if getattr(sys, "frozen", False):
        html_path = os.path.join(sys._MEIPASS, "fleet_sim", "templates", "emr_control_panel.html")
    else:
        html_path = os.path.join(os.path.dirname(__file__), "templates", "emr_control_panel.html")

    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return f"<h1>EMR Control Panel HTML not found at {html_path}</h1>"


class EMRStartRequest(BaseModel):
    case: Optional[str] = None


@app.get("/api/emr/devices")
def get_emr_devices():
    if not _emr_service:
        return {"devices": [], "enabled": False, "message": "EMR simulator is not enabled"}
    payload = _emr_service.list_devices()
    payload["enabled"] = True
    return payload


@app.get("/api/emr/profiles")
def get_emr_profiles():
    if not _emr_service:
        return {"profiles": {}, "enabled": False, "message": "EMR simulator is not enabled"}
    return {"enabled": True, "profiles": _emr_service.list_profiles()}


@app.post("/api/emr/{device_id}/start")
def start_emr_device(device_id: str, req: EMRStartRequest):
    log.info("api_start requested device_id=%s case=%s", device_id, req.case)
    if not _emr_service:
        raise HTTPException(503, "EMR simulator is not enabled")

    if req.case:
        ok = _emr_service.set_case(device_id, req.case)
        if not ok:
            log.warning("api_start rejected device_id=%s reason=invalid_case_or_device case=%s", device_id, req.case)
            raise HTTPException(400, "Invalid case or device")

    mapped, patient_id = _emr_service.sync_patient(device_id)
    if not mapped:
        log.warning("api_start blocked device_id=%s reason=no_patient_mapping patient_id=%s", device_id, patient_id)
        raise HTTPException(
            409,
            "No patient mapped for this device. Start is blocked until patient is assigned by EMR API.",
        )

    started, reason = _emr_service.start_device_with_reason(device_id)
    if not started:
        if reason.startswith("patient_conflict:"):
            conflict_device = reason.split(":", 1)[1]
            log.warning(
                "api_start blocked device_id=%s reason=patient_conflict conflict_device_id=%s patient_id=%s",
                device_id,
                conflict_device,
                patient_id,
            )
            raise HTTPException(
                409,
                f"Patient {patient_id} is already active on device {conflict_device}. Stop that device first.",
            )
        if reason == "device_not_found":
            log.warning("api_start failed device_id=%s reason=device_not_found", device_id)
            raise HTTPException(404, f"EMR device {device_id} not found")
        log.warning("api_start failed device_id=%s reason=%s", device_id, reason)
        raise HTTPException(409, f"Unable to start device {device_id}: {reason}")

    log.info("api_start ok device_id=%s patient_id=%s case=%s", device_id, patient_id, req.case)

    return {
        "status": "started",
        "device_id": device_id,
        "case": req.case,
        "patient_id": patient_id,
    }


@app.post("/api/emr/{device_id}/stop")
def stop_emr_device(device_id: str):
    log.info("api_stop requested device_id=%s", device_id)
    if not _emr_service:
        raise HTTPException(503, "EMR simulator is not enabled")
    if not _emr_service.stop_device(device_id):
        log.warning("api_stop failed device_id=%s reason=device_not_found", device_id)
        raise HTTPException(404, f"EMR device {device_id} not found")
    log.info("api_stop ok device_id=%s", device_id)
    return {"status": "stopped", "device_id": device_id}


@app.post("/api/emr/{device_id}/check-patient")
def check_emr_patient(device_id: str):
    log.info("api_check_patient requested device_id=%s", device_id)
    if not _emr_service:
        raise HTTPException(503, "EMR simulator is not enabled")
    mapped, patient_id = _emr_service.sync_patient(device_id)
    log.info(
        "api_check_patient result device_id=%s mapped=%s patient_id=%s",
        device_id,
        mapped,
        patient_id,
    )
    return {
        "status": "mapped" if mapped else "not_mapped",
        "device_id": device_id,
        "patient_id": patient_id,
    }


@app.post("/api/emr/stop-all")
def stop_all_emr_devices():
    log.info("api_stop_all requested")
    if not _emr_service:
        raise HTTPException(503, "EMR simulator is not enabled")
    stopped = _emr_service.stop_all_devices()
    log.info("api_stop_all ok devices_stopped=%s", stopped)
    return {"status": "stopped", "devices_stopped": stopped}


@app.post("/api/emr/clear-data")
def clear_emr_runtime_data():
    log.info("api_clear_data requested")
    if not _emr_service:
        raise HTTPException(503, "EMR simulator is not enabled")
    cleared = _emr_service.clear_runtime_data()
    log.info("api_clear_data ok devices_cleared=%s", cleared)
    return {"status": "cleared", "devices_cleared": cleared}


@app.post("/api/emr/shutdown")
def shutdown_simulator_process():
    """Hard-stop current simulator process (CDI + EMR) for operator convenience."""
    log.warning("api_shutdown requested")

    def _exit_soon():
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=_exit_soon, daemon=True).start()
    return {"status": "shutdown_requested"}


def start_emr_control_panel(emr_service, port: int = 3001):
    """Start standalone EMR control panel on a separate daemon thread."""
    set_emr_service(emr_service)

    def _run():
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"[EMRControlPanel] Web UI → http://localhost:{port}")
    print(f"[EMRControlPanel] API    → http://localhost:{port}/api/emr/devices")
    return t
