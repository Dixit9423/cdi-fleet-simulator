# Configuration Guide for CDI & EMR Fleet Simulators

## Quick Start

### For CDI Fleet Simulator (gRPC)

Edit: `devices_config.yaml`

- Change server host/port
- Modify device profiles and tick data
- Update TLS certificate paths

### For EMR Service Simulator (REST API)

Edit: `simulator/config/emr_config.yaml`

- Recommended: Use the documented version `emr_config.yaml.documented`
- Set OAuth credentials via environment variables (see `.env.example`)
- Adjust profiles and device parameters

---

## Configuration File Comparison

| Aspect               | devices_config.yaml             | emr_config.yaml                         |
| -------------------- | ------------------------------- | --------------------------------------- |
| **Purpose**          | CDI Core gRPC device simulation | EMR REST API device simulation          |
| **Documentation**    | ✅ Excellent                    | ⚠️ Minimal (improved version available) |
| **Secrets**          | 🟢 None (cert paths only)       | 🔴 Real credentials exposed             |
| **Profiles**         | Named parameter sets            | Multi-phase scenarios                   |
| **Device Count**     | 11 devices                      | 5 devices                               |
| **Update Frequency** | Tick interval (~1s)             | Poll interval (5s)                      |

---

## How to Update Each File

### devices_config.yaml

**When to edit:**

- Change gRPC server address (default: `10.124.212.175:9090`)
- Adjust certificate paths for mTLS
- Add/remove simulated devices
- Modify parameter definitions or profiles
- Change data tick values (simulated measurements)

**Example: Change gRPC Server**

```yaml
server:
  host: "your.server.address" # ← Change this
  port: 9090
  tls:
    enabled: true
    cert_dir: "~/your/cert/path" # ← And this
```

**Example: Add a New Device**

```yaml
devices:
  - serial: "C1000000"
    sw_version: "1.0.0"
    site: "New-Location"
    initial_state: "IDLE"
    profile: "full_bypass"
    probes:
      "Arterial BPM": "B1000000"
      # ... add required probe personalities
```

### emr_config.yaml (or emr_config.yaml.documented)

**When to edit:**

- Change EMR API endpoint
- Add/remove simulated devices
- Adjust parameter ranges
- Modify simulation profiles
- Add new phases to profiles

**NEVER edit directly:**

- OAuth credentials (use `.env` instead)
- Actual usernames/passwords
- Client secrets

**Example: Change API Endpoint**

```yaml
api_base_url: "http://your.emr.server:port/apis/default"
```

**Example: Add a New Parameter**

```yaml
parameters:
  # ... existing params ...
  new_param:
    display_name: "New Parameter"
    unit: "units"
    category: "Category"
    min: 0
    max: 100
```

---

## OAuth Credentials Management

### Solution 1: Use Environment Variables

**Step 1:** Copy `.env.example` to `.env`

```bash
cp .env.example .env
```

**Step 2:** Edit `.env` with actual values

```bash
EMR_OAUTH_CLIENT_ID="your-real-id"
EMR_OAUTH_CLIENT_SECRET="your-real-secret"
EMR_OAUTH_USERNAME="admin"
EMR_OAUTH_PASSWORD="your-real-password"
```

**Step 3:** Load before running simulator

```powershell
# PowerShell — load .env and run
Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim('"')) } }
.\hemodynamic_simulator.exe
```

```bash
# Bash/Zsh
export $(cat .env | xargs)
./hemodynamic_simulator.exe
```

**Step 4:** Verify `.gitignore` includes `.env`

```bash
echo ".env" >> .gitignore
```

### Solution 2: Use Docker Secrets

```bash
docker run \
  -e EMR_OAUTH_CLIENT_ID="$OAUTH_CLIENT_ID" \
  -e EMR_OAUTH_CLIENT_SECRET="$OAUTH_CLIENT_SECRET" \
  cdi-emr-simulator:latest
```

---

## Parameter Definition Guide

Each parameter in both configs represents a measurable medical value:

### Structure (emr_config.yaml)

```yaml
parameters:
  map:
    display_name: "MAP" # How it appears in reports/UI
    unit: "mmHg" # Unit of measurement
    category: "Arterial" # Classification
    min: 60 # Minimum for random generation
    max: 110 # Maximum for random generation
```

### Structure (devices_config.yaml)

```yaml
param_catalog:
  70: # param_id (must match CDI proto)
    name: "VO2" # Parameter name
    unit: "mL/min" # Unit
    source_personality: "Core Calculated" # Device that provides this
    alarm_limit: # Alarm thresholds
      present: true
      low: "150"
      high: "400"
    range: # Operational ranges
      present: true
      display_low: "150"
      display_high: "400"
      operating_low: "100"
      operating_high: "800"
```

### Common Parameters

| Parameter       | Display Name           | Unit   | Category | Use Case                   |
| --------------- | ---------------------- | ------ | -------- | -------------------------- |
| `map`           | Mean Arterial Pressure | mmHg   | Arterial | Perfusion adequacy         |
| `heart_rate`    | Heart Rate             | bpm    | Other    | Cardiovascular status      |
| `etco2`         | End-tidal CO2          | mmHg   | Venous   | Ventilation/perfusion      |
| `arterial_flow` | Arterial Flow          | L/min  | Other    | Bypass flow rate           |
| `rso2`          | Cerebral O2 Saturation | %      | Other    | Brain perfusion            |
| `lactate`       | Lactate                | mmol/L | Other    | Tissue perfusion indicator |
| `glucose`       | Blood Glucose          | mg/dL  | Other    | Metabolic status           |

---

## Profile Definition Guide

### Profiles for CDI (devices_config.yaml)

Profiles specify which parameters to report:

```yaml
profiles:
  full_bypass:
    do2i_threshold: 280 # Critical threshold for alerts
    manual_hgb: 12.5 # Manual hemoglobin input
    manual_so2: 65 # Manual saturation input
    flow_source: "Flow_Red" # Which flow sensor to use
    param_ids: [76, 70, 74, ...] # Parameters to include
    selected_param_ids: [76, 70, ...] # Prioritized parameters
```

### Profiles for EMR (emr_config.yaml)

Profiles define multi-phase simulation scenarios:

```yaml
profiles:
  hemo_monitor_baseline:
    description: "High support bypass-like flow..."
    phase_seconds: 45 # Each phase lasts 45 seconds
    volatility: 0.9 # High variability (0.0-1.0)
    phase_sequence: [baseline, stress, recovery] # Cycle pattern
    targets:
      baseline: # Phase 1: normal operation
        map: 82
        heart_rate: 78
        # ... other parameters ...
      stress: # Phase 2: stress/alert condition
        map: 72
        heart_rate: 94
        # ... other parameters ...
      recovery: # Phase 3: recovery
        map: 88
        heart_rate: 82
        # ... other parameters ...
```

---

## Device Configuration Guide

### Devices in devices_config.yaml (CDI)

```yaml
devices:
  - serial: "C1234567" # Device serial (unique ID)
    sw_version: "1.0.0" # Software version
    site: "OR-1" # Physical location
    initial_state: "IDLE" # Starting state: IDLE, STANDBY, MEASURING
    patient_id: null # Patient binding (null = unbound)
    profile: null # Profile name (null = no profile)
    probes: # Device probe identifiers
      "Arterial BPM": "B0050034"
      "Venous BPM": "V1234567"
      # ... other personalities ...
    tick_data: # Cyclic data values (for MEASURING)
      76: ["89", "88", "90", ...]
      70: ["0", "0", "1", ...]
      # ... other param IDs ...
```

### Devices in emr_config.yaml (EMR)

```yaml
devices:
  - device_id: "Hemo-C1234567" # Internal device ID
    mode: "emr" # "emr" or "grpc"
    emr_lookup_device_id: "Hemo-C1234567" # EMR system ID
    device_type: "hemodynamic" # Device classification
    patient_identifier: "1" # Link to patient record
    notes: "Patient stable" # Descriptive notes
    enabled: true # Active flag
    poll_interval: 5 # EMR status poll (seconds)
    send_interval: 1 # Telemetry send interval (seconds)
    case: "within_range" # Scenario type
    profile: "hemo_monitor_baseline" # Profile name
```

### Device Case Types (EMR)

| Case           | Meaning                      | Use                    |
| -------------- | ---------------------------- | ---------------------- |
| `within_range` | All parameters normal        | Baseline testing       |
| `mixed`        | Some parameters out-of-range | Alert testing          |
| `low_limit`    | Parameters near low alarms   | Low condition testing  |
| `high_limit`   | Parameters near high alarms  | High condition testing |

---

## Troubleshooting

### Issue: "Cannot connect to server"

**Cause:** Wrong host/port in config  
**Solution:** Update `server.host` and `server.port` in `devices_config.yaml`

### Issue: "TLS certificate verification failed"

**Cause:** Missing certificate files or wrong path  
**Solution:** Check `cert_dir` points to correct location; verify file names

### Issue: "OAuth authentication failed"

**Cause:** Missing or wrong credentials  
**Solution:** Set environment variables from `.env.example`

### Issue: "Device profile not found"

**Cause:** Profile name typo  
**Solution:** Match profile name exactly (case-sensitive) to `profiles:` section

### Issue: "Parameter ID mismatch"

**Cause:** param_id doesn't exist in `param_catalog`  
**Solution:** Add missing parameter to `param_catalog` or use existing ID

---

## Best Practices

### ✅ DO:

- Edit config files for testing scenarios
- Use environment variables for secrets
- Add comments explaining custom parameters
- Version control configurations (without secrets)
- Test after major config changes
- Keep backups of working configs

### ❌ DON'T:

- Commit OAuth credentials to Git
- Share `.env` files via email/chat
- Hardcode secrets in Python code
- Modify generated protobuf files
- Change parameter IDs without coordination
- Leave debug logging enabled in production

---

## Release Package Layout (ZIP)

The release ZIP contains pre-built Windows executables. No Python installation is required.

```
CDICore-Simulator-Suite-<version>-Windows/
├── fleet_simulator/                   ← CDI gRPC simulator folder
│   ├── fleet_simulator.exe            ← Run this
│   └── ...
├── hemodynamic_simulator/             ← EMR hemodynamic simulator folder
│   ├── hemodynamic_simulator.exe      ← Run this
│   └── ...
├── devices_config.yaml                ← CDI gRPC config (edit before running)
├── .env                               ← Environment variables template (fill in secrets)
├── README.md
└── simulator/
    └── config/
        └── emr_config.yaml            ← EMR runtime config (edit before running)
```

### Running from the release package

**CDI Fleet Simulator:**

```powershell
# From the extracted ZIP directory
.\fleet_simulator\fleet_simulator.exe --insecure
.\fleet_simulator\fleet_simulator.exe --help
.\fleet_simulator\fleet_simulator.exe --config ..\devices_config.yaml --control-port 8090
```

**Hemodynamic (EMR) Simulator:**

```powershell
# Normal mode
.\hemodynamic_simulator\hemodynamic_simulator.exe

# With UI on custom port
.\hemodynamic_simulator\hemodynamic_simulator.exe --ui-port 3001

# Live mode with EMR backend with different access tokens and keys.
.\hemodynamic_simulator\hemodynamic_simulator.exe --config-dir simulator\config --emr-api-base-url https://your-emr-host/apis/default --emr-access-token <token>
```

> **Note:** Each `.exe` must remain inside its own folder. Do not move it out.

---

## Next Steps

1. **Setup Secrets:** Create `.env` file

   ```bash
   cp .env.example .env
   # Edit .env with real credentials
   ```

   Ensure `.env` is in `.gitignore` so credentials are never committed.

2. **Configure EMR:** Edit `simulator/config/emr_config.yaml` directly
   - Set `api_base_url` to your EMR endpoint
   - Adjust device list, profiles, and parameter ranges
   - Do **not** put OAuth secrets here — use `.env` or environment variables instead

   > **Note:** `emr_config.yaml.documented` is a fully commented reference template
   > used by the CI release pipeline. It is not a replacement for the runtime config.

3. **Store credentials securely**
   - Use environment variables or a secrets vault
   - Never commit secrets to Git
