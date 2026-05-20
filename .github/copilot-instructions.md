# Copilot instructions for TelemetryGrpcClient

## Scope
- Applies to `Linux/CdiCoreMain/TelemetryGrpcClient` and its `fleet_sim` package.
- This module is a Python fleet simulator for CDI Core telemetry (gRPC bidi + FastAPI control panel).

## Architecture map
- `run_fleet.py`: entry point, CLI parsing, proto path setup, config load, banner, startup/shutdown orchestration.
- `fleet_sim/config.py`: YAML load/validation and normalization.
- `fleet_sim/fleet_manager.py`: creates/stops one `DeviceRunner` per device.
- `fleet_sim/device_runner.py`: per-device thread, stream lifecycle, message building, state transitions, tick cadence.
- `fleet_sim/state_store.py`: thread-safe runtime state + command queues + optional persistence.
- `fleet_sim/control_app.py`: FastAPI UI/API for runtime commands.
- `fleet_sim/mtls.py`: secure/insecure channel creation, SAN diagnostics, keepalive options.
- `devices_config.yaml`: source of truth for server, profiles, params, and simulated devices.

## Runtime flow to preserve
1. Start process and load YAML.
2. Optional control panel starts on `:8090`.
3. One runner thread per device opens bidi stream.
4. Runner sends `DeviceAnnouncement`.
5. Runner sends startup `CoreStateEvent` (including `IDLE` startup event).
6. For measuring path: `ProfileMetadata` then `DataTick` every ~1s.
7. Control panel commands enqueue transitions (`IDLE` ↔ `STANDBY` ↔ `MEASURING`) and runtime updates.

## State-machine rules (important)
- Enforce transition guard: `IDLE -> STANDBY -> MEASURING`.
- `MEASURING -> IDLE` clears patient/tick sequencing as implemented.
- `IDLE -> STANDBY` must send `ProfileMetadata` before standby state event.
- Keep command handling non-blocking and stream reader on background thread.

## Protocol and serialization constraints
- Do not change protobuf field names or semantic meaning in simulator messages without coordinated proto update.
- Keep `measurement_session_id`, `connection_id`, `profile_version`, and `seq_no` behavior consistent.
- Maintain param metadata mapping from `param_catalog` to `ProfileMetadata.params`.

## Concurrency and reliability expectations
- Preserve `DeviceState.lock` protection for mutable state.
- Do not introduce shared mutable state outside `StateStore` without synchronization.
- Keep queue-based command passing (`push_command`/`pop_command`).
- Prefer small, low-risk edits; avoid large refactors.

## TLS/mTLS expectations
- Respect YAML TLS settings (`cert_dir`, cert file names, optional `server_name_override`).
- Keep SAN diagnostics behavior in `mtls.py`.
- Keep keepalive disabled by default unless explicitly configured.

## File hygiene
- Prefer editing only source files:
  - `run_fleet.py`, `devices_config.yaml`, `fleet_sim/*.py`, docs in this folder.
- Avoid editing generated artifacts unless requested:
  - `telemetry_pb2.py`, `telemetry_pb2_grpc.py`, `dist/`, `build/`, release bundles.

## Logging and style
- Keep existing simple console log style (`[Fleet]`, `[ControlPanel]`, `[mTLS]`, `[<device>]`).
- Use clear, actionable error messages.
- Follow current code style and typing approach already used in module.

## Validation after changes
- Verify simulator starts from source:
  - `python run_fleet.py --help`
  - `python run_fleet.py --insecure`
- Validate control panel endpoints:
  - `/api/fleet/summary`, `/api/fleet`, `/api/devices/{id}`
- If changing state logic, test at least one full transition cycle per device path.
