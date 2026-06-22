# Dual-Mode Simulator Module

This module adds a production-oriented EMR simulation path without changing the existing CDI gRPC runner behavior.

## What is implemented

- Modular package structure:
  - `simulator/config`: YAML config files
  - `simulator/core`: models, validation, threading, service lifecycle
  - `simulator/api`: EMR REST client
  - `simulator/simulator`: EMR loop + value generation + CDI placeholder
  - `simulator/payloads`: EMR payload builder
  - `simulator/main.py`: standalone EMR entrypoint
- Multi-device EMR support with one thread per device
- Device-level start/stop control
- Parameter generation every 1 second with gradual variation
- Case support: `within_range`, `outside_range`, `low_limit`, `high_limit`, `mixed`
- Profile/phase support via `phase_sequence`, `phase_seconds`, and phase targets
- Dry-run mode (default) for backend-in-progress integration

## EMR APIs expected by this module

- `GET /api/patient?Identifier={identifier}`
- `POST /hemodynamics`

Required headers for live mode:

- `Authorization: Bearer <access_token>`
- `Accept: application/fhir+json` for patient lookup
- `Content-Type: application/json` for hemodynamics POST

## Control panel integration

When `run_fleet.py` is started with `--enable-emr`, the existing FastAPI app exposes:

- `GET /api/emr/devices`
- `POST /api/emr/{device_id}/start`
- `POST /api/emr/{device_id}/stop`

This is additive and does not replace existing CDI endpoints.

## Run modes

1. Existing CDI only:
   - `python run_fleet.py`

2. CDI + EMR (dry-run, no backend required):
   - `python run_fleet.py --enable-emr`

3. CDI + EMR live API:
   - `python run_fleet.py --enable-emr --emr-live --emr-api-base-url https://192.168.1.9/apis/default --emr-access-token <token>`

   Optional for lab/self-signed certs:
   - add `--emr-no-verify-ssl`

4. Standalone EMR module:
   - `python -m simulator.main`

Verbose API debug logs (URL, status, latency, device/pid context):

- `python run_emr_service.py --live --auto-start --log-level debug`

5. Two independent services (recommended when you want separate stop/start control):
   - CDI service only (port 8090):
     - `python run_fleet.py`
   - EMR service only (port 3001):
     - `python run_emr_service.py`

   By default, the standalone EMR service starts with devices stopped.
   Pass `--auto-start` only when you want the devices to begin sending immediately on launch.

   This way, stopping CDI does not stop EMR, and stopping EMR does not stop CDI.

## Config files

- Preferred single-file config: `simulator/config/emr_config.yaml`
- Backward-compatible split files:
  - `simulator/config/emr_params.yaml`
  - `simulator/config/emr_devices.yaml`
  - `simulator/config/emr_profiles.yaml`
  - `simulator/config/emr_oauth.yaml` (optional)

When `emr_config.yaml` exists, it is used automatically. Otherwise split files are used.

The single-file config can also define `api_base_url`, so you only need to update that file if the OpenEMR endpoint changes.

For live TLS setups with self-signed/private CA certs, you can point to a CA cert file from the same config:

```yaml
api_base_url: "https://emr-host.example/apis/default"
tls:
  ca_cert_path: "certs/emr-ca.crt" # relative to simulator/config/ or absolute path
  verify_ssl: true # optional, defaults to true
```

Notes:

- `tls.ca_cert_path` enables certificate verification using that CA file.
- `--emr-no-verify-ssl` still overrides config and disables verification.
- If `ca_cert_path` is invalid/missing, startup fails fast with a clear error.

You can edit device list, polling/sending intervals, case values, profiles, and OAuth settings from YAML.

## EMR UI behavior notes

The standalone EMR UI (`/` on port 3001) auto-refreshes every 5 seconds and has a manual Refresh button.

- Auto-refresh: periodically fetches latest runtime state from `GET /api/emr/devices`
- Refresh button: immediate fetch now; it does not start/stop devices

### Status meanings

- `waiting_for_patient`: patient lookup by Identifier has not resolved
- `waiting_for_device_assignment`: patient is found but assignment row is unresolved
- `patient_and_device_mapped`: patient + device mapping is ready
- `dry_run_ok`: simulated POST success (no real DB write)
- `http_200` / `http_201`: live-mode POST success to EMR

### Dry-run vs live mode

- Dry-run (default): no real POST to OpenEMR DB; send loop behavior is simulated for safe integration testing
- Live mode (`--live` / `--emr-live`): real patient lookup, device assignment lookup, and hemodynamics POST

### Logging

- `--log-level debug|info|warning|error` is supported by `run_emr_service.py` and `python -m simulator.main`.
- `debug` level includes per-API attempt logs with URL, status code, latency, device id, pid, and short response/error snippets.

### Case, profile, phase

- Case: value-shaping behavior mode (for example `mixed` or `low_limit`)
- Profile: target template defining phase sequence and targets
- Phase: active step in the profile timeline (for example `baseline`, `stress`, `recovery`)
