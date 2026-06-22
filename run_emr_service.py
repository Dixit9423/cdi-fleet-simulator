from __future__ import annotations

import argparse
import logging
import os
import time

from fleet_sim.emr_control_app import start_emr_control_panel
from simulator.core.service import EMRSimulatorService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone EMR simulator service with optional EMR UI")
    parser.add_argument(
        "--config-dir",
        default="simulator/config",
        help="Directory containing emr_config.yaml (single-file) or split files: emr_params.yaml, emr_devices.yaml, emr_profiles.yaml, and optional emr_oauth.yaml",
    )
    parser.add_argument(
        "--emr-api-base-url",
        default="https://192.168.1.9/apis/default",
        help="OpenEMR API base URL (example: https://192.168.1.9/apis/default)",
    )
    parser.add_argument(
        "--emr-access-token",
        default=os.getenv("OPENEMR_ACCESS_TOKEN", ""),
        help="OpenEMR bearer access token (or set OPENEMR_ACCESS_TOKEN)",
    )
    parser.add_argument(
        "--emr-no-verify-ssl",
        action="store_true",
        help="Disable TLS certificate verification (useful for self-signed lab certs)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live OpenEMR API calls. Default is dry-run mode.",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Run EMR simulator without EMR web UI.",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=3001,
        help="EMR UI port (default: 3001)",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Auto-start devices on simulator startup. Default is OFF so devices stay stopped until started from UI/API.",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Application log verbosity (default: info)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    emr_service = EMRSimulatorService(
        config_dir=args.config_dir,
        api_base_url=args.emr_api_base_url,
        access_token=args.emr_access_token or None,
        verify_ssl=not args.emr_no_verify_ssl,
        dry_run=not args.live,
    )
    emr_service.load()
    emr_service.start()

    if args.auto_start:
        for device in emr_service.list_devices()["devices"]:
            emr_service.start_device(device["device_id"])

    if not args.no_ui:
        start_emr_control_panel(emr_service, port=args.ui_port)

    print("[EMRService] Started")
    print(f"[EMRService] Devices: {emr_service.device_count}")
    print(f"[EMRService] Dry-run mode: {emr_service.dry_run_mode}")
    if not args.no_ui:
        print(f"[EMRService] UI: http://localhost:{args.ui_port}")
    print("[EMRService] Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            emr_service.shutdown()
        except KeyboardInterrupt:
            # If Ctrl+C is pressed again during thread joins, exit quietly.
            pass

    print("[EMRService] Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
