"""
fleet_sim/state_store.py
────────────────────────
Thread-safe runtime state for every simulated device.
The control panel pushes commands; device runners pop them.
"""

import copy
import json
import os
import threading
import time
from queue import Queue, Empty
from typing import Any


class DeviceState:
    """Mutable per-device runtime state (protected by lock)."""

    def __init__(self, device_cfg: dict):
        self.lock = threading.Lock()
        self.device_id: str = device_cfg["device_id"]
        self.serial: str = device_cfg["serial"]
        self.site: str = device_cfg["site"]
        self.sw_version: str = device_cfg["sw_version"]
        self.current_state: str = device_cfg["initial_state"]  # MEASURING|IDLE|STANDBY
        self.patient_id: str | None = device_cfg.get("patient_id")
        self.profile_name: str | None = device_cfg.get("profile")
        self.probes: dict = device_cfg.get("probes", {})
        self.tick_data: dict[int, list[str]] = device_cfg.get("tick_data", {})
        self.tick_index: int = 0
        self.seq_no: int = 3001
        self.connected: bool = False
        self.connection_id: str | None = None
        self.measurement_session_id: str | None = None
        self.profile_version: int = 0
        self.total_ticks_sent: int = 0
        self.last_tick_utc_ms: int | None = None
        self.error: str | None = None
        self.command_queue: Queue = Queue()

    def snapshot(self) -> dict:
        """Return a JSON-serialisable copy of current state."""
        with self.lock:
            return {
                "device_id": self.device_id,
                "serial": self.serial,
                "site": self.site,
                "sw_version": self.sw_version,
                "current_state": self.current_state,
                "patient_id": self.patient_id,
                "profile_name": self.profile_name,
                "connected": self.connected,
                "connection_id": self.connection_id,
                "measurement_session_id": self.measurement_session_id,
                "profile_version": self.profile_version,
                "seq_no": self.seq_no,
                "total_ticks_sent": self.total_ticks_sent,
                "last_tick_utc_ms": self.last_tick_utc_ms,
                "error": self.error,
            }


class StateStore:
    """Central store for all device states."""

    def __init__(self, devices_cfg: list[dict]):
        self._devices: dict[str, DeviceState] = {}
        for d in devices_cfg:
            dev_id = d["device_id"]
            self._devices[dev_id] = DeviceState(d)

    def get_device(self, device_id: str) -> DeviceState | None:
        return self._devices.get(device_id)

    def all_devices(self) -> list[DeviceState]:
        return list(self._devices.values())

    def all_snapshots(self) -> list[dict]:
        return [d.snapshot() for d in self._devices.values()]

    def push_command(self, device_id: str, command: dict) -> bool:
        """Push a command for a device runner to pick up."""
        dev = self._devices.get(device_id)
        if not dev:
            return False
        dev.command_queue.put(command)
        return True

    def pop_command(self, device_id: str, timeout: float = 0) -> dict | None:
        """Non-blocking pop from a device's command queue."""
        dev = self._devices.get(device_id)
        if not dev:
            return None
        try:
            return dev.command_queue.get(timeout=timeout)
        except Empty:
            return None

    def update_tick_data(self, device_id: str, param_id: int, values: list[str]) -> bool:
        """Hot-update tick values for a param (from control panel)."""
        dev = self._devices.get(device_id)
        if not dev:
            return False
        with dev.lock:
            dev.tick_data[param_id] = values
        return True

    def summary(self) -> dict:
        """Dashboard-style summary."""
        states = [d.snapshot() for d in self._devices.values()]
        measuring = sum(1 for s in states if s["current_state"] == "MEASURING")
        idle = sum(1 for s in states if s["current_state"] == "IDLE")
        standby = sum(1 for s in states if s["current_state"] == "STANDBY")
        connected = sum(1 for s in states if s["connected"])
        return {
            "total": len(states),
            "connected": connected,
            "measuring": measuring,
            "idle": idle,
            "standby": standby,
            "devices": states,
        }

    def save_runtime_state(self, file_path: str):
        """Persist mutable runtime state to JSON file."""
        payload = {
            "version": 1,
            "devices": {},
        }
        for dev in self._devices.values():
            with dev.lock:
                payload["devices"][dev.device_id] = {
                    "current_state": dev.current_state,
                    "patient_id": dev.patient_id,
                    "profile_name": dev.profile_name,
                    "tick_data": dev.tick_data,
                }

        folder = os.path.dirname(file_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load_runtime_state(self, file_path: str):
        """Load mutable runtime state from JSON file if present."""
        if not os.path.isfile(file_path):
            return

        with open(file_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        devices = payload.get("devices", {})
        for device_id, state in devices.items():
            dev = self._devices.get(device_id)
            if not dev:
                continue
            with dev.lock:
                dev.current_state = state.get("current_state", dev.current_state)
                dev.patient_id = state.get("patient_id", dev.patient_id)
                dev.profile_name = state.get("profile_name", dev.profile_name)

                loaded_tick_data = state.get("tick_data")
                if isinstance(loaded_tick_data, dict):
                    # JSON stores dict keys as strings; normalize back to int.
                    dev.tick_data = {
                        int(k): [str(v) for v in vals]
                        for k, vals in loaded_tick_data.items()
                        if isinstance(vals, list)
                    }
