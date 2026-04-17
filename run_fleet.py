#!/usr/bin/env python3
"""
run_fleet.py  —  CDI Core Fleet Simulator entry point.

Starts 6 simulated CDI Core devices (from devices_config.yaml),
each on its own gRPC bidirectional stream with mTLS,
plus a FastAPI control panel on http://localhost:8090.

Usage:
  python run_fleet.py                           # defaults
  python run_fleet.py --config my_config.yaml   # custom config
  python run_fleet.py --insecure                # force insecure channel
  python run_fleet.py --control-port 9000       # custom control panel port
    python run_fleet.py --no-persist              # disable runtime state persistence
"""

import argparse
import os
import sys
import time

# ── Resolve paths (PyInstaller-aware) ────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    _this_dir = os.path.dirname(sys.executable)
    _bundle_dir = sys._MEIPASS
    # Proto stubs are bundled at top-level of _MEIPASS
    if _bundle_dir not in sys.path:
        sys.path.insert(0, _bundle_dir)
else:
    # Running from source
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _proto_dir = os.path.normpath(os.path.join(_this_dir, "..", "src", "Telemetry", "proto"))
    if _proto_dir not in sys.path:
        sys.path.insert(0, _proto_dir)

# Verify proto stubs exist
try:
    import telemetry_pb2
    import telemetry_pb2_grpc
except ImportError:
    if getattr(sys, 'frozen', False):
        print("[Fleet] ERROR: Proto stubs not found in bundled executable.")
        print("[Fleet] This is a packaging bug — rebuild with: pyinstaller fleet_simulator.spec")
    else:
        print(f"[Fleet] ERROR: Proto stubs not found in {_proto_dir}")
        print(f"[Fleet] Generate them with:")
        print(f"  python -m grpc_tools.protoc "
              f"-I{_proto_dir} "
              f"--python_out={_proto_dir} "
              f"--grpc_python_out={_proto_dir} "
              f"telemetry.proto")
    sys.exit(1)

from fleet_sim.config import load_config
from fleet_sim.fleet_manager import FleetManager
from fleet_sim.control_app import start_control_panel


def main():
    default_no_persist = bool(getattr(sys, 'frozen', False))
    parser = argparse.ArgumentParser(
        description="CDI Core Fleet Simulator — 6-device mTLS gRPC client"
    )
    parser.add_argument(
        "--config", "-c",
        default=os.path.join(_this_dir, "devices_config.yaml"),
        help="Path to YAML config (default: devices_config.yaml beside the executable)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Force insecure gRPC channel (ignore TLS config)",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=8090,
        help="Control panel HTTP port (default: 8090)",
    )
    parser.add_argument(
        "--no-control",
        action="store_true",
        help="Disable the control panel web UI",
    )
    parser.add_argument(
        "--state-file",
        default=os.path.join(_this_dir, ".fleet_runtime_state.json"),
        help="Runtime state JSON path (default: .fleet_runtime_state.json)",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        default=default_no_persist,
        help="Disable saving/loading runtime state (default: ON for executable, OFF for source)",
    )
    args = parser.parse_args()

    # ── Load config ──────────────────────────────────────────────────────
    config = load_config(args.config)

    if args.insecure:
        config["server"]["tls"]["enabled"] = False

    # ── Banner ───────────────────────────────────────────────────────────
    srv = config["server"]
    tls = srv["tls"]
    print()
    print("*" * 70)
    print("  CDI Core Fleet Simulator")
    print("*" * 70)
    print(f"  gRPC target   : {srv['host']}:{srv['port']}")
    print(f"  mTLS enabled  : {tls['enabled']}")
    if tls["enabled"]:
        print(f"  CA cert       : {tls.get('ca_cert_path', '—')}")
        print(f"  Client cert   : {tls.get('client_cert_path', '—')}")
    print(f"  Devices       : {len(config['devices'])}")
    print(f"  Profiles      : {', '.join(config['profiles'].keys())}")
    print(f"  Control panel : {'disabled' if args.no_control else f'http://localhost:{args.control_port}'}")
    print(f"  Persistence   : {'disabled' if args.no_persist else args.state_file}")
    print("*" * 70)
    print()

    # ── Start fleet ──────────────────────────────────────────────────────
    fleet = FleetManager(config)

    # ── Load previously saved runtime state (optional) ──────────────────
    if not args.no_persist:
        try:
            fleet.store.load_runtime_state(args.state_file)
            print(f"[Fleet] Loaded runtime state: {args.state_file}")
        except Exception as e:
            print(f"[Fleet] WARNING: Failed to load runtime state: {e}")

    # ── Start control panel ──────────────────────────────────────────────
    if not args.no_control:
        start_control_panel(fleet.store, config["profiles"], port=args.control_port)
        time.sleep(0.5)

    # ── Start device runners ─────────────────────────────────────────────
    fleet.start_all()

    # ── Wait for Ctrl+C ──────────────────────────────────────────────────
    print()
    print("Press Ctrl+C to stop all devices and exit.")
    print()
    try:
        fleet.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if not args.no_persist:
            try:
                fleet.store.save_runtime_state(args.state_file)
                print(f"[Fleet] Saved runtime state: {args.state_file}")
            except Exception as e:
                print(f"[Fleet] WARNING: Failed to save runtime state: {e}")
        fleet.stop_all()

    print("\n[Fleet] Bye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
