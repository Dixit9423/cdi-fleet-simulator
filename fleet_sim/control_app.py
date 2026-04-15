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


def set_store(store):
    global _store
    _store = store


def set_profiles(profiles):
    global _profiles
    _profiles = profiles


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
    html_path = os.path.join(os.path.dirname(__file__), "templates", "control_panel.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Control Panel HTML not found</h1>"


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
        cmd = {"type": "idle", "reason": req.reason or "ReturnToIdle"}
    elif state == "STANDBY":
        if current not in ("IDLE", "MEASURING"):
            raise HTTPException(400, f"Cannot go to STANDBY from {current}")
        cmd = {
            "type": "standby",
            "reason": req.reason or "Standby",
            "profile": req.profile or "minimal",
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
        if not req.patient_id:
            raise HTTPException(400, "patient_id required for bind")
        cmd = {"type": "bind_patient", "patient_id": req.patient_id}
    elif req.action == "release":
        cmd = {"type": "release_patient"}
    else:
        raise HTTPException(400, f"Invalid action: {req.action}")

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


# ── Run helper ───────────────────────────────────────────────────────────────

def start_control_panel(store, profiles, port: int = 8090):
    """Start the control panel on a daemon thread."""
    set_store(store)
    set_profiles(profiles)

    def _run():
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"[ControlPanel] Web UI → http://localhost:{port}")
    print(f"[ControlPanel] API    → http://localhost:{port}/api/fleet")
    return t
