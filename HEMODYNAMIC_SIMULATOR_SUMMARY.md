# CDICore Hemodynamic Simulator - Summary & Reference

## 1. Overview

The Hemodynamic Simulator provides a config-driven, multi-device EMR simulation path for CDICore testing. It generates realistic hemodynamic values, runs one worker per device, and uses live EMR API behavior in delivery builds.

This module is additive to the CDI Fleet simulator and can run in either of these patterns:

- integrated mode: CDI Fleet + Hemodynamic in one process
- standalone mode: Hemodynamic service only

---

## 2. Architecture

```text
                    +-----------------------------------+
                    | simulator/config/emr_config.yaml |
                    +----------------+------------------+
                                     |
                                     v
          +---------------------------------------------------------+
          | EMRSimulatorService (load -> start -> shutdown)         |
          +------------------------------+--------------------------+
                                         |
        +--------------------------------+--------------------------------+
        |                                                                 |
        v                                                                 v
+-------------------------------+                       +-------------------------------+
| Device runners (1 thread/dev) |                       | EMR REST client               |
| - case shaping                 |                       | - GET /api/patient            |
| - profile/phase progression    |                       | - POST /hemodynamics          |
| - payload generation           |                       +-------------------------------+
+---------------+---------------+
                |
                v
      +-------------------------------+
      | Optional EMR UI/API (:3001)   |
      | - GET /api/emr/devices        |
      | - POST /api/emr/{id}/start    |
      | - POST /api/emr/{id}/stop     |
      +-------------------------------+
```

---

## 3. Key Files

- run_emr_service.py: standalone hemodynamic service entrypoint
- run_fleet.py: CDI Fleet entrypoint with optional --enable-emr
- simulator/config/emr_config.yaml: preferred single-file EMR config
- simulator/core/service.py: lifecycle and device orchestration
- simulator/simulator/emr_runner.py: per-device run loop
- simulator/payloads/emr_payload.py: outbound payload builder
- fleet_sim/emr_control_app.py: standalone EMR control panel API/UI

---

## 4. Run Modes

### Command help

```bash
python run_emr_service.py --help
python run_fleet.py --help
```

### Standalone Hemodynamic service

```bash
python run_emr_service.py
```

### Standalone with auto-start devices

```bash
python run_emr_service.py --auto-start
```

### Integrated with CDI Fleet simulator

```bash
python run_fleet.py --enable-emr
```

### Integrated live mode

```bash
python run_fleet.py --enable-emr
```

### Log view and debug mode

```bash
# Start with verbose logs
python run_emr_service.py --log-level debug

# Or integrated mode with verbose logs
python run_fleet.py --enable-emr

# Windows PowerShell live log tail (if output is redirected to a file)
Get-Content .\emr_service.log -Wait
```

---

## 5. Configuration Model

Primary config file:

- simulator/config/emr_config.yaml

Main sections:

- api_base_url: EMR API base endpoint
- parameters: parameter catalog with unit/min/max metadata
- profiles: phase-driven targets for generated values
- oauth: token URL and credentials (use environment variables)
- devices: device list with case/profile/poll/send intervals

Supported device cases:

- within_range
- mixed
- low_limit
- high_limit
- outside_range

---

## 6. Runtime and Control

Default standalone UI endpoint:

- http://localhost:3001

Core control APIs:

- GET /api/emr/devices
- POST /api/emr/{device_id}/start
- POST /api/emr/{device_id}/stop

Status examples shown in UI/runtime state:

- waiting_for_patient
- waiting_for_device_assignment
- waiting_for_patient_and_device_assignment
- patient_and_device_mapped
- http_200 / http_201

---

## 7. OpenEMR Integration Flow

The simulator uses OpenEMR REST APIs in a gated mapping-first sequence per active device.

Flow per device:

1. Device runner starts and checks if patient/device mapping is already known.
2. Runner calls device assignment endpoint first:
   - GET /hemodynamics/device_id/{emr_lookup_device_id}
3. If assignment is missing, runner resolves patient identifier:
   - GET /api/patient?Identifier={patient_identifier}
4. Runner retries assignment check constrained by resolved patient id.
5. Only when both are available does sending begin:
   - patient_id
   - mapped_device_id
6. Runner posts hemodynamic payload every send_interval:
   - POST /hemodynamics

Authentication and security:

- OAuth token endpoint is configured under oauth.token_url.
- Access token can be supplied directly or refreshed from configured OAuth credentials.
- TLS certificate verification is enabled by default.
- Use --emr-no-verify-ssl only for lab/self-signed environments.

Observed runtime statuses in this flow:

- waiting_for_patient_and_device_assignment: neither patient nor assignment is ready
- waiting_for_device_assignment: patient exists but device assignment row is not ready
- patient_and_device_mapped: both keys resolved and sending can proceed
- http_200 / http_201: payload POST accepted by OpenEMR

---

## 8. Profile Catalog (Configured Defaults)

Profiles are phase-driven targets defined in simulator/config/emr_config.yaml and reused per device.

1. hemo_monitor_baseline

- description: High support bypass-like flow with tight oxygen delivery tracking
- phase_seconds: 45
- volatility: 0.9
- phase_sequence: baseline -> stress -> recovery
- baseline focus: stable pressure/flow with moderate FiO2 and lactate
- stress focus: lower MAP/BP, higher HR/FiO2/ACT/lactate
- recovery focus: BP/MAP rebound, lower FiO2 and lactate than stress

2. hemo_perfusion_balance

- description: Balanced perfusion with arterial and venous focus
- phase_seconds: 50
- volatility: 0.8
- phase_sequence: baseline -> venous_shift -> baseline
- baseline focus: balanced MAP/BP/flow values
- venous_shift focus: higher EtCO2/HR, slight flow reduction, mild lactate rise

3. hemo_oxygen_delivery

- description: DO2-centric profile with flow and FiO2 optimization
- phase_seconds: 40
- volatility: 1.0
- phase_sequence: baseline -> low_delivery -> correction
- baseline focus: higher arterial flow with oxygen support
- low_delivery focus: lower MAP/BP/flow with compensatory higher FiO2/sweep and lactate rise
- correction focus: pressure/flow restoration and partial metabolic recovery

4. hemo_cerebral_watch

- description: Cerebral oxygenation sensitive profile
- phase_seconds: 35
- volatility: 0.85
- phase_sequence: baseline -> cerebral_drop -> stabilization
- baseline focus: strong rSO2 with stable pressure
- cerebral_drop focus: rSO2 drop with concurrent perfusion stress
- stabilization focus: rSO2 and hemodynamics return toward baseline

---

## 9. Parameter Details (Range, Unit, Category)

The simulator parameter catalog defines payload fields, units, and expected operating bands.

| Parameter     | Display Name               | Unit   | Category | Min | Max |
| ------------- | -------------------------- | ------ | -------- | --: | --: |
| map           | MAP                        | mmHg   | Arterial |  60 | 110 |
| systolic_bp   | Systolic BP                | mmHg   | Arterial |  90 | 160 |
| diastolic_bp  | Diastolic BP               | mmHg   | Arterial |  50 | 100 |
| heart_rate    | Heart Rate                 | bpm    | Other    |  50 | 120 |
| etco2         | EtCO2                      | mmHg   | Venous   |  30 |  50 |
| arterial_flow | Arterial Flow              | L/min  | Other    | 2.0 | 6.0 |
| sweep         | Sweep                      | L/min  | Other    | 0.5 | 3.0 |
| fio2          | FiO2                       | %      | Other    |  21 | 100 |
| rso2          | Cerebral Oxygen Saturation | %      | Other    |  45 |  80 |
| act           | ACT                        | sec    | Other    |  80 | 240 |
| lactate       | Lactate                    | mmol/L | Other    | 0.5 | 5.0 |
| glucose       | Glucose                    | mg/dL  | Other    |  70 | 180 |

---

## 10. Live EMR Behavior

- real OpenEMR patient lookup, assignment checks, and hemodynamics POST requests
- requires valid OAuth/token and reachable OpenEMR endpoint

---

## 11. Release Package Expectations

The release ZIP ships an editable runtime EMR config at:

- simulator/config/emr_config.yaml

It also ships a root .env file generated from .env.example to help users fill secrets locally.

---

## 12. Recommended Operations Practice

- keep real credentials out of version control
- use .env and environment injection for OAuth values
- validate connectivity and credentials before starting devices
- verify mappings (patient and device assignment) before expecting successful POST flow

---

## 13. Quick Troubleshooting

- No EMR updates visible: confirm device is started from EMR UI/API
- waiting_for_patient persists: verify patient identifier exists in EMR
- waiting_for_device_assignment persists: verify assignment row exists for mapped patient/device
- TLS errors in live mode: use valid CA or test with --emr-no-verify-ssl in lab only
- No UI: confirm port 3001 is free or change with --ui-port
