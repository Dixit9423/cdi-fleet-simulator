"""
fleet_sim/control_app.py
────────────────────────
FastAPI control panel for the fleet simulator.
Runs on a separate thread alongside the gRPC device runners.

Endpoints:
  GET  /                          → Control panel HTML UI
  GET  /api/fleet                 → All device states (JSON)
  GET  /api/fleet/summary         → Dashboard KPIs
  GET  /api/devices/{id}          → Single device state
  POST /api/devices/{id}/state    → Change device state
  POST /api/devices/{id}/patient  → Bind / release patient
  POST /api/devices/{id}/profile  → Send new ProfileMetadata
  POST /api/devices/{id}/tick     → Hot-update tick values
  GET  /api/profiles              → List available profiles
"""

import os
import sys
import threading
import time
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# Will be set by run_fleet.py before starting
_store = None
_profiles = None
_emr_service = None
_shutdown_callback = None


def set_store(store):
    global _store
    _store = store


def set_profiles(profiles):
    global _profiles
    _profiles = profiles


def set_emr_service(emr_service):
    global _emr_service
    _emr_service = emr_service


def set_shutdown_callback(callback):
    global _shutdown_callback
    _shutdown_callback = callback


app = FastAPI(
    title="CDI Fleet Simulator — Control Panel",
    version="1.0.0",
    description="Runtime control for 6 simulated CDI Core devices",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── HTML UI ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def control_panel_ui():
    # PyInstaller-aware: check _MEIPASS first, then __file__ relative
    if getattr(sys, 'frozen', False):
        html_path = os.path.join(sys._MEIPASS, "fleet_sim", "templates", "control_panel.html")
    else:
        html_path = os.path.join(os.path.dirname(__file__), "templates", "control_panel.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return f"<h1>Control Panel HTML not found at {html_path}</h1>"


# ── API: Fleet ───────────────────────────────────────────────────────────────

@app.get("/api/fleet")
def get_fleet():
    if not _store:
        raise HTTPException(503, "Fleet not yet initialized")
    return _store.all_snapshots()


@app.get("/api/fleet/summary")
def get_fleet_summary():
    if not _store:
        raise HTTPException(503, "Fleet not yet initialized")
    return _store.summary()


@app.get("/api/profiles")
def list_profiles():
    if not _profiles:
        return {}
    return {
        name: {
            "do2i_threshold": p.get("do2i_threshold"),
            "manual_hgb": p.get("manual_hgb"),
            "manual_so2": p.get("manual_so2"),
            "flow_source": p.get("flow_source"),
            "param_ids": p.get("param_ids", []),
        }
        for name, p in _profiles.items()
    }


# ── API: EMR Simulator ─────────────────────────────────────────────────────

class EMRStartRequest(BaseModel):
    case: Optional[str] = None


@app.get("/api/emr/devices")
def get_emr_devices():
    if not _emr_service:
        return {"devices": [], "enabled": False, "message": "EMR simulator is not enabled"}
    payload = _emr_service.list_devices()
    payload["enabled"] = True
    return payload


@app.post("/api/emr/{device_id}/start")
def start_emr_device(device_id: str, req: EMRStartRequest):
    if not _emr_service:
        raise HTTPException(503, "EMR simulator is not enabled")

    if req.case:
        ok = _emr_service.set_case(device_id, req.case)
        if not ok:
            raise HTTPException(400, "Invalid case or device")

    if not _emr_service.start_device(device_id):
        raise HTTPException(404, f"EMR device {device_id} not found")

    return {
        "status": "started",
        "device_id": device_id,
        "case": req.case,
    }


@app.post("/api/emr/{device_id}/stop")
def stop_emr_device(device_id: str):
    if not _emr_service:
        raise HTTPException(503, "EMR simulator is not enabled")
    if not _emr_service.stop_device(device_id):
        raise HTTPException(404, f"EMR device {device_id} not found")
    return {"status": "stopped", "device_id": device_id}


@app.post("/api/emr/clear-data")
def clear_emr_runtime_data():
    if not _emr_service:
        raise HTTPException(503, "EMR simulator is not enabled")
    cleared = _emr_service.clear_runtime_data()
    return {"status": "cleared", "devices_cleared": cleared}


# ── API: Single Device ──────────────────────────────────────────────────────

@app.get("/api/devices/{device_id}")
def get_device(device_id: str):
    if not _store:
        raise HTTPException(503, "Fleet not yet initialized")
    ds = _store.get_device(device_id)
    if not ds:
        raise HTTPException(404, f"Device {device_id} not found")
    return ds.snapshot()


# ── Commands ─────────────────────────────────────────────────────────────────

class StateChangeRequest(BaseModel):
    state: str  # MEASURING | IDLE | STANDBY
    profile: Optional[str] = None
    patient_id: Optional[str] = None
    reason: Optional[str] = None
    emr_enabled: Optional[bool] = None
    remove_emr_params: Optional[bool] = None


@app.post("/api/devices/{device_id}/state")
def change_state(device_id: str, req: StateChangeRequest):
    if not _store:
        raise HTTPException(503, "Fleet not yet initialized")
    ds = _store.get_device(device_id)
    if not ds:
        raise HTTPException(404, f"Device {device_id} not found")

    state = req.state.upper()
    with ds.lock:
        current = ds.current_state

    if state == "MEASURING":
        # Guard: can only start MEASURING from STANDBY
        if current != "STANDBY":
            raise HTTPException(
                400,
                f"Cannot start MEASURING from {current}. "
                f"Flow: IDLE → STANDBY → MEASURING. Move to STANDBY first."
            )
        cmd = {
            "type": "start_measuring",
            "profile": req.profile or ds.profile_name or "minimal",
            "patient_id": req.patient_id,
        }
    elif state == "IDLE":
        if current == "IDLE":
            raise HTTPException(400, "Device is already IDLE")
        cmd = {"type": "idle", "reason": req.reason or "StopCase"}
    elif state == "STANDBY":
        if current not in ("IDLE", "MEASURING"):
            raise HTTPException(400, f"Cannot go to STANDBY from {current}")
        if current == "MEASURING":
            default_reason = "StandByCase"
            emr_enabled = ds.emr_enabled
            remove_emr_params = ds.remove_emr_params_from_profile_metadata
        else:
            emr_enabled = bool(req.emr_enabled)
            remove_emr_params = bool(req.remove_emr_params) if not emr_enabled else False
            default_reason = "SetProfile, EMR ON" if emr_enabled else "SetProfile, EMR OFF"
            with ds.lock:
                ds.emr_enabled = emr_enabled
                ds.remove_emr_params_from_profile_metadata = remove_emr_params
        cmd = {
            "type": "standby",
            "reason": req.reason or default_reason,
            "profile": req.profile or ds.profile_name or "minimal",
            "emr_enabled": emr_enabled,
            "remove_emr_params": remove_emr_params,
        }
    else:
        raise HTTPException(400, f"Invalid state: {state}")

    _store.push_command(device_id, cmd)
    return {"status": "queued", "device_id": device_id, "command": cmd}


class PatientRequest(BaseModel):
    action: str  # bind | release
    patient_id: Optional[str] = None


@app.post("/api/devices/{device_id}/patient")
def manage_patient(device_id: str, req: PatientRequest):
    if not _store:
        raise HTTPException(503, "Fleet not yet initialized")
    ds = _store.get_device(device_id)
    if not ds:
        raise HTTPException(404, f"Device {device_id} not found")

    if req.action == "bind":
        backend_patient_id = ds.pending_patient_id or ds.patient_id
        if not backend_patient_id:
            raise HTTPException(409, "No backend-provided patient id available to bind")
        if req.patient_id and str(req.patient_id) != str(backend_patient_id):
            raise HTTPException(400, "patient_id must match the backend-provided patient id")
        cmd = {"type": "bind_patient", "patient_id": backend_patient_id}
    elif req.action == "release":
        cmd = {"type": "release_patient"}
    else:
        raise HTTPException(400, f"Invalid action: {req.action}")

    _store.push_command(device_id, cmd)
    return {"status": "queued", "device_id": device_id, "command": cmd}


class PatientDecisionRequest(BaseModel):
    decision: str  # accept | reject


@app.post("/api/devices/{device_id}/patient-decision")
def patient_decision(device_id: str, req: PatientDecisionRequest):
    if not _store:
        raise HTTPException(503, "Fleet not yet initialized")
    ds = _store.get_device(device_id)
    if not ds:
        raise HTTPException(404, f"Device {device_id} not found")

    decision = req.decision.lower()
    if decision not in ("accept", "reject"):
        raise HTTPException(400, "decision must be 'accept' or 'reject'")

    cmd = {"type": "patient_decision", "decision": decision}
    _store.push_command(device_id, cmd)
    return {"status": "queued", "device_id": device_id, "command": cmd}


class ProfileRequest(BaseModel):
    profile: str


@app.post("/api/devices/{device_id}/profile")
def set_profile(device_id: str, req: ProfileRequest):
    ds = _store.get_device(device_id)
    if not ds:
        raise HTTPException(404, f"Device {device_id} not found")

    cmd = {"type": "set_profile", "profile": req.profile}
    _store.push_command(device_id, cmd)
    return {"status": "queued", "device_id": device_id, "command": cmd}


class TickDataRequest(BaseModel):
    param_id: int
    values: list[str]


@app.post("/api/devices/{device_id}/tick")
def update_tick_data(device_id: str, req: TickDataRequest):
    ds = _store.get_device(device_id)
    if not ds:
        raise HTTPException(404, f"Device {device_id} not found")

    cmd = {"type": "update_tick_data", "param_id": req.param_id, "values": req.values}
    _store.push_command(device_id, cmd)
    return {"status": "queued", "device_id": device_id, "command": cmd}


@app.post("/api/system/stop")
def stop_simulator_process():
    """Gracefully request simulator shutdown."""
    if _shutdown_callback:
        _shutdown_callback()

        # Graceful path can take time with many device threads/reconnect loops.
        # Force-exit as a safety net so operator stop from UI always completes.
        def _force_exit_later():
            time.sleep(6)
            os._exit(0)

        threading.Thread(target=_force_exit_later, daemon=True).start()
        return {"status": "shutdown_requested", "force_exit_in_sec": 6}
    raise HTTPException(503, "Shutdown callback not configured")


# ── Run helper ───────────────────────────────────────────────────────────────

def start_control_panel(store, profiles, port: int = 8090, emr_service=None, shutdown_callback=None):
    """Start the control panel on a daemon thread."""
    set_store(store)
    set_profiles(profiles)
    set_emr_service(emr_service)
    set_shutdown_callback(shutdown_callback)

    def _run():
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"[ControlPanel] Web UI → http://localhost:{port}")
    print(f"[ControlPanel] API    → http://localhost:{port}/api/fleet")
    return t
