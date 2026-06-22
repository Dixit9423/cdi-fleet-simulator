# CDI + EMR Simulator Implementation Status

Date: 2026-06-22

## 1) Objective

Track the current simulator-side implementation for dual-mode support:

- CDI gRPC simulator (existing fleet flow)
- EMR REST simulator (new modular extension)

This document reflects only features currently present in the repository.

## 2) Implemented (Code-Verified)

### 2.1 Package structure and entrypoints

Implemented modular EMR extension under `simulator/`:

- `simulator/api/`
- `simulator/config/`
- `simulator/core/`
- `simulator/payloads/`
- `simulator/simulator/`
- `simulator/main.py`

Current runnable entrypoints:

- `run_fleet.py` (CDI flow with optional EMR enablement)
- `run_emr_service.py` (standalone EMR service + EMR UI)
- `python -m simulator.main` (standalone EMR module)

### 2.2 Config model and loading

Implemented in:

- `simulator/core/models.py`
- `simulator/core/config_loader.py`
- `simulator/core/service.py`

Supported config files:

- `simulator/config/emr_config.yaml` (preferred single-file config)
- `simulator/config/emr_params.yaml`
- `simulator/config/emr_devices.yaml`
- `simulator/config/emr_profiles.yaml`
- `simulator/config/emr_oauth.yaml` (optional split-file OAuth source)

Loading behavior:

- If `emr_config.yaml` exists, it is used as the main source.
- If `emr_config.yaml` is absent, service falls back to split files.
- `api_base_url` can be defined in `emr_config.yaml` and is loaded by the service (CLI URL remains usable as fallback/override input).
- TLS verification can be configured in `emr_config.yaml`:
  - `tls.ca_cert_path` (PEM/CRT file path; relative or absolute)
  - `tls.verify_ssl` (boolean)
  - CLI `--emr-no-verify-ssl` still has precedence and disables verification.

Validation/model coverage includes:

- Device mode and simulation case validation
- Parameter/category mapping checks
- EMR profile-to-parameter consistency checks
- OAuth model (`EMROAuthConfig`) with required field validation

### 2.3 EMR HTTP client

Implemented in `simulator/api/emr_client.py`.

Current capabilities:

- Patient lookup: `GET /api/patient?Identifier={identifier}`
- Device assignment lookup: `GET /hemodynamics/device_id/{device_id}`
- Hemodynamic post: `POST /hemodynamics`
- Device disconnect status update: `PUT /hemodynamics/device_id/{device_id}/status`
- Bearer token support
- OAuth password-grant token retrieval and refresh-on-401 retry
- Retry/backoff and TLS verify toggle
- Optional CA-bundle path for TLS verification via config (`verify` can be bool or cert path)
- Dry-run mode support
- Detailed debug logging (when log level is debug): URL, status code, latency, device/pid context, short error/response snippet

Behavior enforced:

- Posting is gated until patient and device assignment conditions are met.
- Outbound `device_id` uses stable simulator/lookup device-id behavior.
- On stop/stop-all/shutdown paths, simulator attempts to send `Inactive` device status for the resolved `pid`.
- Device assignment lookup supports both:
  - assignment-by-device (latest row, no pid filter)
  - assignment-by-device+pid (filtered fallback path)

### 2.4 Generator, payload, and runtime service

Implemented in:

- `simulator/simulator/generator.py`
- `simulator/payloads/emr_payload.py`
- `simulator/simulator/emr_runner.py`
- `simulator/core/thread_manager.py`
- `simulator/core/service.py`

Current runtime behavior:

- One EMR worker thread per configured EMR device
- Start/stop per device
- Case-driven value generation (`within_range`, `outside_range`, `low_limit`, `high_limit`, `mixed`)
- Profile/phase-aware target selection
- Assignment-first patient mapping:
  - First resolves latest assignment row by device id and uses assignment pid.
  - Falls back to configured `patient_identifier` lookup + assignment check when needed.
- Stop lifecycle emits device disconnect status to EMR using `send_device_status()` before runtime mapping is cleared
- If runtime `patient_id` is empty during stop, service performs fallback patient lookup using configured `patient_identifier`
- Runtime status tracking per device:
  - `active`
  - `patient_id`
  - `lookup_device_id`
  - `mapped_device_id`
  - `profile`
  - `phase`
  - `last_payload_status`
  - `last_sent`
  - `last_error`

Clear runtime support:

- `clear_runtime_data()` implemented in service
- Clears transient runtime fields while preserving configured devices

### 2.5 CLI integration

`run_fleet.py` supports optional EMR flags:

- `--enable-emr`
- `--emr-config-dir`
- `--emr-api-base-url`
- `--emr-access-token`
- `--emr-no-verify-ssl`
- `--emr-live`
- `--enable-emr-ui`
- `--emr-ui-port`

`run_emr_service.py` behavior:

- Supports `--auto-start`
- Supports `--log-level` (`debug|info|warning|error`)
- Default startup keeps devices stopped unless `--auto-start` is passed

`python -m simulator.main` behavior:

- Supports `--log-level` (`debug|info|warning|error`)

### 2.6 Control APIs

Implemented in `fleet_sim/control_app.py`:

- `GET /api/emr/devices`
- `POST /api/emr/{device_id}/start`
- `POST /api/emr/{device_id}/stop`
- `POST /api/emr/clear-data`

Implemented in `fleet_sim/emr_control_app.py`:

- `GET /api/emr/profiles`
- `POST /api/emr/{device_id}/check-patient`
- `POST /api/emr/stop-all`
- `POST /api/emr/clear-data`
- `POST /api/emr/shutdown`

### 2.7 UI status

`fleet_sim/templates/control_panel.html` currently includes:

- Existing fleet UI behavior
- EMR card rendering for EMR-mode devices from fleet summary payload
- Case selector and EMR start/stop actions
- EMR patient/mapping/status fields shown per device

`fleet_sim/templates/emr_control_panel.html` currently includes:

- Dedicated EMR dashboard with KPI cards
- Flow Overview strip
- Helper panel with status definitions
- Check Patient, Start/Stop, Stop All, Clear Runtime Data, Shutdown actions
- Mapped vs waiting status visibility and relative last-sent display
- Waiting counters derived from `last_payload_status` (`waiting_for_patient`, `waiting_for_device_assignment`)
- API status shown as plain text (no oval badge), including full detailed error text when failures occur

### 2.8 Dependencies and docs

- `requirements_fleet.txt` includes `requests>=2.32.0`
- `simulator/README.md` documents single-file config, dry-run/live, TLS CA cert-path usage, CLI usage, and log-level/debug logging semantics

### 2.9 Release packaging status

Current release workflow behavior (`.github/workflows/release.yml`):

- Windows artifact includes simulator external config as single-file source of truth:
  - `simulator/config/emr_config.yaml`
- Includes EMR simulator docs in package root:
  - `simulator/README.md`
  - `IMPLEMENTATION_STATUS.md`
- PyInstaller spec includes both control panel templates (fleet + EMR UI) in bundled assets.

## 3) Pending / Not Yet Implemented

### 3.1 CDI modular runner adapter

- `simulator/simulator/cdi_runner.py` remains a placeholder.
- Full CDI behavior is still handled by the existing fleet path (`fleet_sim/*`).

### 3.2 Automated test suite

- No dedicated simulator/API automated tests are currently committed.

### 3.3 Live OpenEMR sign-off

- Client logic is implemented.
- Full production-environment E2E verification and sign-off are still pending.

## 4) Compatibility status

- Existing CDI simulator path is preserved by default.
- EMR functionality is additive and optional.
- Dry-run mode supports backend-in-progress environments.

## 5) Recommended next step

Add automated tests for:

- Config loading and validation
- TLS config loading (`tls.ca_cert_path` and `tls.verify_ssl`) including invalid-path handling
- Generator case/profile behavior
- Payload mapping
- Service lifecycle/status transitions
- Device disconnect status PUT flow (`Inactive`) for stop/stop-all/shutdown, including fallback PID lookup path
- Assignment-first PID resolution and fallback behavior
- EMR control endpoints
