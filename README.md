# CDI Core Fleet Simulator

This directory contains a Python-based, config-driven fleet simulator for CDI Core telemetry.

It simulates multiple CDI Core devices, each with its own gRPC bidirectional `TelemetrySession`, and includes a FastAPI control panel for runtime operations.

---

## Overview

- Simulates 6 CDI Core devices by default
- Supports mTLS 1.3 and insecure mode
- Uses YAML config for devices, profiles, and parameter metadata
- Includes runtime control panel (state changes, patient bind/release, profile switch, tick updates)
- Preserves legacy clients (`CoreClientReal.py`, `CoreClientDemo.py`) for reference

---

## Directory structure

```text
TelemetryGrpcClient/
├── run_fleet.py               # Entry point
├── devices_config.yaml        # Main simulator config
├── generate_certs.py          # Self-signed cert generator
├── requirements_fleet.txt     # Python dependencies
├── fleet_sim/
│   ├── config.py
│   ├── mtls.py
│   ├── state_store.py
│   ├── device_runner.py
│   ├── fleet_manager.py
│   ├── control_app.py
│   └── templates/control_panel.html
├── CoreClientReal.py          # Legacy single-device client
├── CoreClientDemo.py          # Legacy demo client
└── README.md
```

Proto stubs are imported from:

- `../src/Telemetry/proto/telemetry.proto`

---

## Prerequisites

- Python 3.10+
- `pip`
- Access to telemetry proto stubs (`telemetry_pb2.py`, `telemetry_pb2_grpc.py`)

Install dependencies:

```bash
pip install -r requirements_fleet.txt
```

---

## Quick start

From `Linux/CdiCoreMain/TelemetryGrpcClient`:

```bash
# 1) Install dependencies
pip install -r requirements_fleet.txt

# 2) Generate mTLS certificates (optional, for secure mode)
python generate_certs.py

# 3) Run simulator
python run_fleet.py
```

Open control panel:

```text
http://localhost:8090
```

---

## Run options

```bash
python run_fleet.py --help
python run_fleet.py --config my_config.yaml
python run_fleet.py --control-port 9000
python run_fleet.py --insecure
python run_fleet.py --no-control
python run_fleet.py --no-persist
```

### Main flags

| Flag | Default | Description |
|------|---------|-------------|
| `--config`, `-c` | `devices_config.yaml` | YAML configuration path |
| `--insecure` | `False` | Disable TLS and use insecure channel |
| `--control-port` | `8090` | Control panel HTTP port |
| `--no-control` | `False` | Disable control panel |
| `--state-file` | `.fleet_runtime_state.json` | Runtime state file |
| `--no-persist` | Source: OFF, EXE: ON | Disable runtime state persistence |

---

## Control panel API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | Web UI |
| GET | `/api/fleet` | All devices |
| GET | `/api/fleet/summary` | Fleet KPIs |
| GET | `/api/profiles` | Profiles |
| GET | `/api/devices/{id}` | Single device |
| POST | `/api/devices/{id}/state` | Change state |
| POST | `/api/devices/{id}/patient` | Bind/release patient |
| POST | `/api/devices/{id}/profile` | Switch profile |
| POST | `/api/devices/{id}/tick` | Update tick values |

---

## Telemetry behavior per device

Each device runner follows this sequence:

1. Connect and open stream
2. Send `DeviceAnnouncement`
3. Send startup `CoreStateEvent` (including `IDLE` startup event)
4. If measuring: send `ProfileMetadata`, then `DataTick` at 1-second cadence
5. Process runtime commands from control panel
6. On shutdown: send `CoreStateEvent(IDLE)` and close stream

---

## Configuration notes

Configure everything in `devices_config.yaml`:

- Server host/port
- TLS cert paths
- Device list and startup states
- Profiles and parameter IDs
- Parameter metadata (units, limits, ranges)
- Per-device tick value sequences

This enables adding profiles, parameters, or devices without code changes.

---

## mTLS notes

Example cert bundle location used by tooling:

- `~/Downloads/certs 1`

Typical files:

- `ca.crt`
- `client.crt`
- `client-pkcs8.key`
- `server.crt`
- `server.key`

If connecting by IP address, ensure the server certificate SAN includes that IP, or set `tls.server_name_override` to a SAN DNS name.

---

## Troubleshooting

| Issue | Action |
|------|--------|
| Proto stubs not found | Generate with `python -m grpc_tools.protoc ...` as shown by runtime error |
| Cert file not found | Run `python generate_certs.py` or fix `cert_dir` in YAML |
| Channel not ready | Confirm server is running; try `--insecure` for local tests |
| `too_many_pings` GOAWAY | Keepalive is disabled by default; restart old clients |
| Control panel port busy | Use `--control-port` |
| `Import error: fleet_sim` | Run from `TelemetryGrpcClient/` directory |

---

## Related docs

- `FLEET_SIMULATOR_SUMMARY.md` (detailed architecture and roadmap)
- `FIXES_APPLIED.md` (recent UI and reliability fixes)

