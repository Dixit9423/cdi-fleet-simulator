"""
fleet_sim/fleet_manager.py
──────────────────────────
Orchestrates all DeviceRunners and owns the shared StateStore.
"""

import threading
import time

from fleet_sim.state_store import StateStore
from fleet_sim.device_runner import DeviceRunner
from fleet_sim.mtls import create_channel


class FleetManager:
    """Start / stop / monitor all device runners."""

    def __init__(self, config: dict):
        self.config = config
        self.server_cfg = config["server"]
        self.param_catalog = config["param_catalog"]
        self.profiles = config["profiles"]
        self.store = StateStore(config["devices"])
        self.runners: list[DeviceRunner] = []
        self.stop_event = threading.Event()

    def start_all(self):
        """Create and start a DeviceRunner thread per device."""
        print()
        print("=" * 70)
        print("  CDI Fleet Simulator — Starting Devices")
        print("=" * 70)

        for ds in self.store.all_devices():
            # Each runner gets its own channel factory (independent connections)
            def make_channel(cfg=self.server_cfg):
                return create_channel(cfg)

            runner = DeviceRunner(
                device_state=ds,
                server_cfg=self.server_cfg,
                param_catalog=self.param_catalog,
                profiles=self.profiles,
                channel_factory=make_channel,
                stop_event=self.stop_event,
            )
            self.runners.append(runner)
            runner.start()
            # Stagger device connections by 0.5s to avoid overwhelming server
            time.sleep(0.5)

        print()
        print(f"  {len(self.runners)} device(s) started.")
        print("=" * 70)
        print()

    def stop_all(self):
        """Signal all runners to stop and wait for threads."""
        print("\n[Fleet] Stopping all devices...")
        self.stop_event.set()
        for r in self.runners:
            r.join(timeout=5)
        print("[Fleet] All devices stopped.")

    def wait(self):
        """Block until KeyboardInterrupt."""
        try:
            while not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
