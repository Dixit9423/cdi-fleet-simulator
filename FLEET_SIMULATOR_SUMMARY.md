# CDI Core Fleet Simulator — Summary & Reference

## 1. Overview

The **Fleet Simulator** replaces the single-device `CoreClientReal.py` with a **config-driven, multi-device gRPC client** that simulates **6 CDI Core devices** connecting to a Device Manager server over **mTLS 1.3**.

Each device runs its own bidirectional `TelemetrySession` stream and follows the full telemetry flow defined in `Telemetry_Flow_And_Metadata.md`. A built-in **web control panel** lets you change device state, bind patients, switch profiles, and update measurement values at runtime — **without touching any code**.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Fleet Simulator Process (run_fleet.py)                                 │
│                                                                         │
│  ┌──────────────────┐                                                   │
│  │  devices_config   │ ← Edit this YAML to change everything            │
│  │     .yaml         │                                                   │
│  └────────┬─────────┘                                                   │
│           │ load                                                        │
│  ┌────────▼─────────┐    ┌────────────────────────────────────────┐     │
│  │  FleetManager     │───▶│  StateStore (thread-safe)              │     │
│  │                   │    │  - 6 × DeviceState                     │     │
│  └────────┬─────────┘    │  - command queues per device            │     │
│           │ start        │  - snapshots for API                    │     │
│  ┌────────▼───────────────┤                                        │     │
│  │                        └────────────────┬───────────────────────┘     │
│  │  6 × DeviceRunner threads               │ read/write                  │
│  │  ┌─────────────┐ ┌─────────────┐       │                             │
│  │  │ CDI-C1234567│ │ CDI-C2345678│ ...   │                             │
│  │  │  MEASURING  │ │  MEASURING  │       │                             │
│  │  └──────┬──────┘ └──────┬──────┘       │                             │
│  │         │ gRPC bidi     │ gRPC bidi    │                             │
│  └─────────┼───────────────┼──────────────┘                             │
│            │               │                                             │
│  ┌─────────▼───────────────▼──────────────┐                             │
│  │  mTLS Channel (~/Downloads/certs 1)     │                             │
│  │  ca.crt + client.crt + client.key       │                             │
│  └─────────┬───────────────┬──────────────┘                             │
│                                                                         │
│  ┌────────────────────────────────────────┐                             │
│  │  Control Panel (FastAPI :8090)          │◄── Browser (auto-refresh)   │
│  │  GET/POST /api/devices/{id}/state|...   │                             │
│  └────────────────────────────────────────┘                             │
└─────────────────────────────────────────────────────────────────────────┘
                   │ gRPC (TLS 1.3)
                   ▼
        ┌─────────────────────┐
        │  Device Manager     │
        │  (Java / gRPC)      │
        │  :5555               │
        └─────────────────────┘
```

---

## 3. Directory Structure

```
TelemetryGrpcClient/
├── run_fleet.py              ← Main entry point
├── devices_config.yaml       ← ALL device/param/profile config (edit this!)
├── generate_certs.py         ← Self-signed CA + client + server certs
├── requirements_fleet.txt    ← pip dependencies
│
├── fleet_sim/                ← Python package
│   ├── __init__.py
│   ├── config.py             ← YAML loader + validation
│   ├── mtls.py               ← mTLS / insecure channel factory
│   ├── state_store.py        ← Thread-safe runtime state
│   ├── device_runner.py      ← Per-device gRPC client thread
│   ├── fleet_manager.py      ← Orchestrates all runners
│   ├── control_app.py        ← FastAPI control panel API
│   └── templates/
│       └── control_panel.html ← Web UI
│
├── CoreClientReal.py         ← Original single-device client (preserved)
├── CoreClientDemo.py         ← Existing demo client (preserved)
└── README.md                 ← Existing C++ client docs
```

---

## 4. Quick Start

### Step 1: Install dependencies
```bash
pip install -r requirements_fleet.txt
```

### Step 2: Generate mTLS certificates
```bash
python generate_certs.py
# Creates ~/Downloads/certs 1/{ca.crt, client.crt, client-pkcs8.key, server.crt, server.key}
```

### Step 3: Start the simulator
```bash
# With mTLS (default — requires server to accept these certs)
python run_fleet.py

# Insecure mode (for testing against local Python/Java server)
python run_fleet.py --insecure

# Custom config / port
python run_fleet.py --config my_devices.yaml --control-port 9000

# Disable runtime state persistence (always start from YAML defaults)
python run_fleet.py --no-persist
```

By default, runtime state changes from control panel are saved to:
`TelemetryGrpcClient/.fleet_runtime_state.json`

### Step 4: Open control panel
```
http://localhost:8090
```

---

## 5. The 6 Simulated Devices

| # | Device ID       | Serial     | Site  | Initial State | Profile           | Patient         |
|---|-----------------|------------|-------|---------------|-------------------|-----------------|
| 1 | CDI-C1234567    | C1234567   | OR-1  | **IDLE**      | —                 | —               |
| 2 | CDI-C2345678    | C2345678   | OR-2  | **IDLE**      | —                 | —               |
| 3 | CDI-C3456789    | C3456789   | OR-3  | **IDLE**      | —                 | —               |
| 4 | CDI-C4567890    | C4567890   | ICU-1 | IDLE          | —                 | —               |
| 5 | CDI-C5678901    | C5678901   | ICU-2 | IDLE          | —                 | —               |
| 6 | CDI-C6789012    | C6789012   | OR-4  | IDLE          | —                 | —               |

### Device startup behavior

| Initial State | gRPC Flow at Startup |
|---------------|---------------------|
| MEASURING     | Announce → ProfileMetadata → CoreStateEvent(MEASURING) → DataTick loop (1s) |
| IDLE          | Announce only (waits for commands) |
| STANDBY       | Announce → CoreStateEvent(STANDBY) |

---

## 6. Profiles & Parameters

### Available Profiles

| Profile Name         | DO2i Threshold | Flow Source | Key Parameters |
|----------------------|---------------|-------------|----------------|
| `full_bypass`        | 280           | Flow_Red    | HCO3, Hgb(A/V), SO2(V), Flow, RSO2, VO2, DO2, VO2I, O2ER, DO2I, NADIR |
| `arterial_venous`    | 300           | Flow_Red    | pH(A/V), PCO2(A/V), PO2, HCO3, Hgb(A/V), SO2(A/V), Flow |
| `oxygen_delivery`    | 260           | Flow_Blue   | Hgb(A/V), SO2(V), Flow, VO2, DO2, VO2I, O2ER, DO2I, NADIR |
| `cerebral_monitoring`| 280           | Flow_Red    | Hgb(A), SO2(V), Flow, RSO2(Ch1/2), DO2, DO2I, NADIR |
| `minimal`            | 300           | Flow_Red    | Hgb(A), SO2(V), Flow, DO2, DO2I |

### Key Oxygen Parameters (as requested)

| Param ID | Name | Unit | Description |
|----------|------|------|-------------|
| 70       | Consumed_Oxygen_VO2 | mL/min | Oxygen consumption rate |
| 71       | Delivered_Oxygen_DO2 | mL/min | Oxygen delivery rate |
| 72       | Consumed_Oxygen_Index_VO2I | mL/min/m² | BSA-indexed VO2 |
| 73       | Oxygen_Extraction_Ratio_O2ER | % | O2ER = VO2/DO2 |
| 75       | Delivered_Oxygen_Index_DO2I | mL/min/m² | BSA-indexed DO2 |
| 89       | DO2I_NADIR | mL/min/m² | Lowest DO2I observed |

---

## 7. Editing Data Without Code Changes

### Change tick values
Edit `devices_config.yaml` → `devices[n].tick_data`:
```yaml
devices:
  - serial: "C1234567"
    tick_data:
      71: ["560", "563", "565", "567", "569"]    # DO2 values cycle
      75: ["315", "318", "320", "322", "319"]    # DO2I values cycle
```

### Add a new profile
Add to `profiles:` section:
```yaml
profiles:
  my_custom_profile:
    do2i_threshold: 290
    manual_hgb: 13.5
    manual_so2: 68
    flow_source: "Flow_Red"
    param_ids: [42, 50, 55, 71, 75, 89]
```

### Add a new parameter
Add to `param_catalog:` section:
```yaml
param_catalog:
  77:
    name: "My_New_Param"
    unit: "units"
    source_personality: "Core Calculated"
    alarm_limit: { present: true, low: "10", high: "100" }
    range: { present: true, display_low: "0", display_high: "200",
             operating_low: "0", operating_high: "250" }
```

### Add a 7th device
Append to `devices:` list:
```yaml
  - serial: "C9999999"
    sw_version: "2.0.0"
    site: "OR-5"
    initial_state: "IDLE"
    patient_id: null
    profile: null
    probes:
      "Arterial H/SAT": "H9999999"
      "Core Calculated": "C9999999"
    tick_data: {}
```

### Runtime changes (via control panel)
Use the web UI at `http://localhost:8090` or call APIs directly:

```bash
# Start measuring on an idle device
curl -X POST http://localhost:8090/api/devices/CDI-C4567890/state \
  -H "Content-Type: application/json" \
  -d '{"state": "MEASURING", "profile": "oxygen_delivery", "patient_id": "PAT-NEW-001"}'

# Stop measuring
curl -X POST http://localhost:8090/api/devices/CDI-C1234567/state \
  -d '{"state": "IDLE", "reason": "StopCase"}'

# Bind patient
curl -X POST http://localhost:8090/api/devices/CDI-C1234567/patient \
  -d '{"action": "bind", "patient_id": "PAT-UPDATED-002"}'

# Hot-update tick values
curl -X POST http://localhost:8090/api/devices/CDI-C1234567/tick \
  -d '{"param_id": 71, "values": ["600", "610", "620", "630"]}'
```

---

## 8. mTLS 1.3 Configuration

### Certificate files (~/Downloads/certs 1/)

| File        | Purpose |
|-------------|---------|
| `ca.crt`    | Root CA certificate — verifies the server's cert |
| `ca.key`    | CA private key (for signing — not sent to server) |
| `client.crt`| Client certificate — sent to server for mutual auth |
| `client.key`| Client private key |
| `server.crt`| Server certificate (for test gRPC server) |
| `server.key`| Server private key (for test gRPC server) |

### Config in devices_config.yaml
```yaml
server:
  host: "dm-server.hospital.local"
  port: 5555
  tls:
    enabled: true
    cert_dir: "~/Downloads/certs 1"
    ca_cert: "ca.crt"
    client_cert: "client.crt"
    client_key: "client-pkcs8.key"
    server_cert: "server.crt"
    # server_name_override: "localhost"   # optional

  ### IP address verification rule

  - If you connect using an IP (for example `10.124.204.104`), that IP must exist in `server.crt` SAN IP entries.
  - If the IP is not in SAN but DNS name is present, set `tls.server_name_override` to that DNS name.
  - The simulator now prints SAN preflight diagnostics before opening the secure channel.
```

### Insecure override
```bash
python run_fleet.py --insecure    # ignores TLS config
```

---

## 9. Control Panel API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Web UI (HTML) |
| GET | `/api/fleet` | All device states |
| GET | `/api/fleet/summary` | Dashboard KPIs (total, connected, measuring, idle, standby) |
| GET | `/api/profiles` | Available profile definitions |
| GET | `/api/devices/{id}` | Single device state |
| POST | `/api/devices/{id}/state` | Change state (MEASURING/IDLE/STANDBY) |
| POST | `/api/devices/{id}/patient` | Bind or release patient |
| POST | `/api/devices/{id}/profile` | Send new ProfileMetadata |
| POST | `/api/devices/{id}/tick` | Hot-update tick values for a parameter |

---

## 10. gRPC Message Flow Per Device

```
DeviceRunner thread
────────────────────────────────────────────────────────
1. connect()     → create mTLS channel + open bidi stream
2. announce()    → DeviceAnnouncement        ← ManagerAck
3. (if MEASURING):
   a. profile()  → ProfileMetadata           ← ManagerAck
   b. bind()     → (local state update)
   c. state()    → CoreStateEvent(MEASURING) ← ManagerAck
4. loop:
   a. check command queue (from control panel)
   b. if MEASURING → DataTick (1s cadence)   ← ManagerAck
   c. if command → execute state transition
5. on shutdown:
   → CoreStateEvent(IDLE)
   → close stream
```

---

## 11. Identified Improvements & Roadmap

### ✅ Implemented in This Version
- [x] mTLS 1.3 with configurable certificates
- [x] 6 concurrent device streams (independent threads)
- [x] YAML-driven config (zero code changes for data updates)
- [x] 5 named profiles with full param metadata (alarm limits, ranges, source devices)
- [x] DO2, DO2I, VO2, VO2I, O2ER parameters with sample tick values
- [x] Web control panel with device cards, state badges, action buttons
- [x] Runtime state changes (MEASURING ↔ IDLE ↔ STANDBY)
- [x] Runtime patient bind/release
- [x] Hot-update tick values via API
- [x] Self-signed certificate generator

### 🔜 Recommended Next Improvements

| Priority | Feature | Description |
|----------|---------|-------------|
| **P0** | **WebSocket live feed** | Add `/ws/ticks` WebSocket endpoint that broadcasts DataTick values to the browser in real-time (useful for testing the monitoring UI) |
| **P0** | **Reconnection logic** | Auto-reconnect device runners on gRPC stream failure with exponential backoff |
| **P1** | **Scenario playback** | Load a JSON/CSV "scenario file" that replays a recorded measurement session (e.g., 60 minutes of real patient data) |
| **P1** | **Alarm simulation** | Add "trigger alarm" button that pushes values outside alarm limits for a specific parameter |
| **P1** | **Bulk operations** | "Start All", "Stop All", "Reset All" buttons on the control panel |
| **P2** | **Profile editor in UI** | In-browser form to create/edit profiles (currently requires YAML edit) |
| **P2** | **Data trend chart** | Live line chart in control panel showing tick values per device over time |
| **P2** | **Logging dashboard** | Display gRPC message log per device in the UI (sent/received/errors) |
| **P3** | **Docker compose** | Containerize the fleet simulator + a test gRPC server for one-command demo |
| **P3** | **Swagger docs** | FastAPI auto-generates OpenAPI docs at `/docs` — just enable it |
| **P3** | **Persistent state** | Save/restore simulator state to disk for session continuity |

### 🎯 Control Panel UI Enhancements (as discussed)
1. **Device state dashboard** — ✅ Implemented (cards with KPIs)
2. **Start/Stop measuring** — ✅ Implemented (per-device buttons)
3. **Patient binding** — ✅ Implemented (bind/release actions)
4. **Profile selection** — ✅ Implemented (dropdown per device)
5. **Tick value editor** — Available via API; UI form TBD
6. **Message log viewer** — Future improvement
7. **Multi-device scenario orchestrator** — Future improvement

---

## 12. Relation to Existing Files

| File | Status | Notes |
|------|--------|-------|
| `CoreClientReal.py` | **Preserved** | Original single-device client; still works independently |
| `CoreClientDemo.py` | **Preserved** | Offline demo client; no changes |
| `CoreClient.cpp/h` | **Preserved** | C++ client; no changes |
| `telemetry.proto` | **Not modified** | Fleet simulator uses existing proto stubs |
| `telemetry_pb2.py` | **Used as-is** | Imported by device_runner.py |
| `devices_config.yaml` | **New** | Central config for all 6 devices |
| `fleet_sim/` | **New** | Python package with all simulator modules |
| `run_fleet.py` | **New** | Entry point |
| `generate_certs.py` | **New** | Certificate generator |

---

## 13. Troubleshooting

| Issue | Solution |
|-------|----------|
| `Proto stubs not found` | Run `python -m grpc_tools.protoc` as shown in the error message |
| `cert file not found` | Run `python generate_certs.py` or check `cert_dir` in YAML |
| `Channel not ready` | Server may not be running; use `--insecure` for testing |
| `GOAWAY ENHANCE_YOUR_CALM too_many_pings` | Keepalive is now OFF by default. Stop old running clients and restart. If needed, configure conservative keepalive in YAML (`keepalive_time_ms: 300000`). |
| `Stream ended` | Server closed the connection; check server logs |
| `Control panel won't start` | Check port 8090 is available; use `--control-port` to change |
| `Import error: fleet_sim` | Run from the `TelemetryGrpcClient/` directory |
| `Patient ID won't update from UI` | Ensure you wait 300ms after clicking bind (auto-delay built-in). Check browser console (F12) for errors. |
| `UI feels laggy / inputs lose focus` | Should be resolved - refresh is now every 3s with smart caching that skips DOM updates if data hasn't changed |
| `Constant console errors about refresh` | Check server is running at the configured address. Monitor `/api/fleet/summary` endpoint availability. |

---

## 14. Recent Updates (Latest Session)

### CoreStateEvent Sent During Startup
✅ **Fixed**: Devices now send `CoreStateEvent(IDLE, "Startup")` immediately after announcement, even for IDLE state.
- **Before**: Only MEASURING and STANDBY states sent CoreStateEvent; IDLE was skipped
- **After**: All states follow consistent pattern: Announcement → CoreStateEvent → Main loop
- **Impact**: Better server synchronization and lifecycle tracking

### Patient Binding from Control Panel  
✅ **Fixed**: Patient ID binding now works reliably from the web UI.
- **Changes**: Improved error handling, 300ms post-API delay, better validation
- **Result**: Patient IDs persist correctly and appear in device cards

### UI Performance Optimized
✅ **Fixed**: Control panel no longer feels heavy or constantly reloads.
- **Changes**: Refresh interval 2s → 3s, added smart data caching, skip DOM updates if data unchanged
- **Result**: Smooth typing in input fields, lower CPU usage, better UX

### Console Errors Resolved
✅ **Fixed**: Browser console errors reduced with proper error logging and validation.
- **Changes**: Better error messages, HTTP status checks, optional chaining for DOM elements
- **Result**: Easier debugging in browser DevTools; see `FIXES_APPLIED.md` for details
