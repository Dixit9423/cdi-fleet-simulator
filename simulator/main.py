from __future__ import annotations

import argparse
import logging
import os
import time

from simulator.core.service import EMRSimulatorService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual-mode simulator entrypoint (EMR mode ready)")
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
        dry_run=False,
    )
    emr_service.load()
    emr_service.start()

    for device in emr_service.list_devices()["devices"]:
        emr_service.start_device(device["device_id"])

    print("[DualMode] EMR simulator started")
    print(f"[DualMode] Devices: {emr_service.device_count}")
    print("[DualMode] Live API mode: enabled")
    print("[DualMode] Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        emr_service.shutdown()

    print("[DualMode] Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
