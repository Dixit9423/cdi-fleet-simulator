# CDI Fleet Simulator v1.0.6

## Release Summary

This release introduces comprehensive patient lifecycle management and measurement session tracking for the CDI Core telemetry simulator, enabling realistic device-server patient binding workflows and session retention.

## New Features

### Patient Lifecycle Management
- **Patient Accept/Reject Workflow**: Devices receive `PatientBind` from server and present pending decision state
  - User can accept or reject patient via control panel buttons
  - Device responds with `CoreStateEvent` reason: "PatientID Accepted" or "Rejected"
- **Patient Retention**: Patient ID retained throughout measurement cycle (STANDBY → MEASURING → STANDBY)
  - Released only when `CoreStateEvent` with reason "StopCase" is sent
- **Post-Release Cooldown**: 60-second waiting period before accepting new patient IDs
  - Defers incoming `PatientBind` messages during cooldown
  - Automatically activates deferred patients when cooldown expires
- **Patient State Indicators** in control panel:
  - 🟢 Green: Patient Bound (accepted and retained)
  - 🟡 Yellow: Patient Decision Required (awaiting accept/reject)
  - ⏳ Cyan: Cooldown timer with animated spinner showing seconds until next patient

### Measurement Session Tracking
- **Stable Session IDs**: Measurement session ID created when transitioning IDLE → STANDBY
  - Retained throughout measurement cycle until return to IDLE
  - Ensures consistent `measurement_session_id` across ProfileMetadata and DataTick messages
- **Session Lifecycle**: Only resets when creating new session or returning to IDLE state

### State Machine Enhancements
- **StandByCase Reason**: CoreStateEvent sent with reason "StandByCase" when MEASURING → STANDBY
- **ResumeCase Reason**: CoreStateEvent sent with reason "ResumeCase" when resuming STANDBY → MEASURING
- **Tick Sequence Reset**: DataTick sequence number now starts at 1 (previously 3001)

### API Endpoints
- New `POST /api/devices/{id}/patient-decision` endpoint
  - Accepts `decision` parameter: "accept" or "reject"
  - Sends appropriate CoreStateEvent with patient decision reason

## Implementation Details

### Files Modified
- `fleet_sim/state_store.py`
  - Added: `pending_patient_id`, `deferred_patient_id`, `patient_cooldown_until_ms`, `case_paused` state fields
  - Updated snapshots to include `cooldown_remaining_sec` for real-time UI updates
  - Changed `DEFAULT_TICK_SEQ_NO` from 3001 to 1

- `fleet_sim/device_runner.py`
  - Implemented patient binding decision workflow in command handler
  - Added `_activate_deferred_patient_if_ready()` for cooldown expiration handling
  - Enhanced `_process_response()` to handle PatientBind/PatientRelease with cooldown logic
  - Modified `_build_profile_metadata()` with `force_new_session` parameter for session stability
  - Updated state transitions with `case_paused` tracking for ResumeCase detection
  - Preserved patient_id during StopCase transition to signal release to server

- `fleet_sim/control_app.py`
  - Added `PatientDecisionRequest` model and `/patient-decision` endpoint
  - Enhanced standby reason logic: "StandByCase" when from MEASURING, "Standby" when from IDLE

- `fleet_sim/templates/control_panel.html`
  - Added `.patient-state` CSS styling for bound/pending/cooldown indicators
  - Added `.spinner` animation for cooldown timer display
  - Enhanced device card rendering with patient state indicator at top
  - Added Accept/Reject buttons that appear when patient decision is pending
  - Updated `toStandby()` function to detect source state and set appropriate reason
  - Added `patientDecision()` function for accept/reject handling

## Behavioral Changes

- **No Backward Compatibility Breaking**: Existing control flow (IDLE → STANDBY → MEASURING) unchanged
- **Protocol Alignment**: CoreStateEvent `reason` field now semantically meaningful for patient workflows
- **Session Tracking**: Callers can now correlate ProfileMetadata and DataTick by stable `measurement_session_id`
- **Tick Counting**: Measurement cycles now start counting from seq_no=1 instead of 3001

## Validation

- All modified Python files pass syntax validation
- State machine guards preserved: IDLE → STANDBY → MEASURING flow enforced
- Thread-safe implementation: all mutable state protected by `DeviceState.lock`
- Control panel UI responsive to patient state changes with 5s refresh cycle

## Usage

1. Download `CDI-Fleet-Simulator-v1.0.6-Windows.zip` from release assets
2. Extract and run `fleet_simulator.exe`
3. Open `http://localhost:8090`
4. When a device receives a patient ID:
   - Yellow indicator appears: "🟡 Patient Decision Required"
   - Click **Accept** to bind patient or **Reject** to dismiss
5. Accepted patients show: "🟢 Patient Bound"
6. After StopCase, device waits 60s before accepting next patient
   - Cyan indicator shows: "⏳ Next patient ID in XXs"

## Known Limitations

- Patient cooldown timer is 60 seconds (hardcoded; configurable via future enhancement)
- Deferred patients during cooldown are held in memory (no persistence across simulator restart)
