# Hemodynamics V2 Implementation Analysis

## Purpose

This document summarizes the current implementation status of the new Hemodynamics integration based on the code present in this workspace.

It explains:

- what is currently handled on the `gRPC server` side,
- what is currently handled on the `device manager backend` side,
- what the current `device / simulator` is expected to handle,
- what differences exist between the original plan and the actual code,
- and what still needs to be implemented on the device side now that backend and gRPC implementation are in place.

---

## Reference Files Reviewed

### Plan / Protocol
- `CDI_OneView_Simulator_V2_Hemodynamics_Implementation_Plan.md`
- `device-manager-grpc-server/src/main/resources/proto/core_v2.proto`

### gRPC Server
- `device-manager-grpc-server/src/main/java/com/terumo/cdi/devicemanager/grpc/service/TelemetryServiceV2Impl.java`
- `device-manager-grpc-server/src/main/java/com/terumo/cdi/devicemanager/service/HemodynamicsDataService.java`
- `device-manager-grpc-server/src/main/java/com/terumo/cdi/devicemanager/service/HemodynamicsParamMapper.java`
- `device-manager-grpc-server/src/main/java/com/terumo/cdi/devicemanager/client/HemodynamicsDeviceClient.java`

### Backend / EMR Ingestion / UI Projection
- `gateway-service/src/main/java/com/terumo/cdi/devicemanager/gatewayservice/service/HemodynamicsScheduler.java`
- `gateway-service/src/main/java/com/terumo/cdi/devicemanager/gatewayservice/service/OpenEmrGatewayService.java`
- `gateway-service/src/main/java/com/terumo/cdi/devicemanager/gatewayservice/service/FhirHemodynamicsMapper.java`
- `gateway-service/src/main/java/com/terumo/cdi/devicemanager/gatewayservice/service/HemodynamicsService.java`
- `device-manager-backend-service/src/main/java/com/terumo/cdi/devicemanager/backendservice/api/service/DeviceProfileService.java`
- `device-manager-backend-service/src/main/java/com/terumo/cdi/devicemanager/backendservice/api/service/DeviceEmrDataTickService.java`
- `device-manager-backend-service/src/main/java/com/terumo/cdi/devicemanager/backendservice/scheduling/DeviceEmrDataTickPollingScheduler.java`
- `device-manager-backend-service/src/main/java/com/terumo/cdi/devicemanager/backendservice/config/WebSocketSessionManager.java`

---

# 1. High-Level Architecture Observed in Code

The current implementation is split across three runtime responsibilities:

1. **Gateway / EMR ingestion layer**
   - Pulls Hemodynamics data from OpenEMR.
   - Maps FHIR payloads to internal DTO/entity models.
   - Stores Hemodynamics data in the local database.

2. **gRPC server layer**
   - Continues handling core device workflow.
   - Adds V2-based Hemodynamics metadata and data push toward the device.
   - Decides when Hemodynamics metadata/data should be sent based on device state, EMR mode, and patient association.

3. **Backend WebSocket/UI layer**
   - Publishes profile and EMR datatick snapshots for UI consumption.
   - Exposes combined CDI + EMR view to dashboard/live-monitor consumers.

The device/simulator is expected to remain responsible for normal CDI telemetry while newly receiving Hemodynamics metadata and Hemodynamics data over `TelemetryServiceV2`.

---

# 2. What Is Happening on the gRPC Server Side

## 2.1 Protocol Contract

From `core_v2.proto`:

### Device -> Manager
The V2 device stream may send:
- `DeviceAnnouncement`
- `CoreStateEvent`
- `ProfileMetadata`
- `DataTick`
- `DeviceAck`

### Manager -> Device
The manager may send:
- `StreamConfig`
- `ManagerAck`
- `PatientBind`
- `PatientRelease`
- `hemodynamics_profile_metadata`
- `hemodynamics_data_tick`

So the V2 contract is clearly designed to allow the manager to push Hemodynamics metadata and Hemodynamics live values to the device.

---

## 2.2 Main gRPC V2 Service

The main implementation is in:

- `TelemetryServiceV2Impl`

This class acts as the runtime orchestrator for the new Hemodynamics flow.

### It currently handles:
- V2 device connection/session creation
- core state tracking
- EMR enabled/disabled state tracking
- patient bind / patient release interactions
- profile metadata validation
- Hemodynamics metadata push
- Hemodynamics data tick push
- acknowledgements and stream config
- stop-case cleanup and release handling

---

## 2.3 Step-by-Step gRPC Behavior

### Step 1 - Device Announcement
Handled in `handleStep1DeviceAnnouncement(...)`

What happens:
- validates the V2 device announcement,
- resolves or creates the telemetry session,
- associates device and connection context,
- logs audit data,
- sends a `ManagerAck` back to the device.

**Conclusion:**
The existing device announcement workflow is preserved, but the V2 session is also initialized for Hemodynamics support.

---

### Step 2 - Core State Event
Handled in `handleStep2CoreStateEvent(...)`

This is the most important control point for Hemodynamics behavior.

#### When state is `CORE_STATE_IDLE` and reason is `Startup`
The server:
- processes the normal state event using existing device connectivity services,
- resolves patient context,
- determines whether a patient mapping exists,
- sends `PatientBind` if patient context is available,
- loads Hemodynamics metadata from `HemodynamicsDataService`,
- sends `hemodynamics_profile_metadata` to the device via V2.

#### EMR ON / OFF state detection
The same handler also interprets:
- `SetProfile, EMR ON`
- `SetProfile, EMR OFF`

and updates internal flags:
- `isEmrEnabled`
- `hemodynamicsStreamingEnabled`

**Conclusion:**
The gRPC server is not just relaying state. It is using state transitions to control whether Hemodynamics metadata/data should be pushed.

---

### Step 3 - Patient Binding
Still handled from the gRPC server flow.

When patient data is resolved, the server:
- sends `PatientBind` to the device,
- checks if the patient exists in Hemodynamics records,
- optionally resolves `emrDeviceId`,
- persists patient binding information.

It supports both scenarios:
- core device only,
- core + Hemodynamics/EMR association.

**Conclusion:**
Patient binding remains part of existing workflow, but the V2 service extends it with EMR-aware patient association handling.

---

### Step 4 - Profile Metadata
Handled in `handleStep4ProfileMetadata(...)`

The server requires:
- device/session already established,
- patient bind already completed.

What it does:
- stores normal profile metadata through the existing service path,
- checks whether EMR was enabled,
- verifies whether the patient is associated with Hemodynamics,
- persists `is_emr_enabled` onto the profile metadata record,
- enables/disables hemodynamics streaming based on association,
- creates and sends `StreamConfig`,
- sends `ManagerAck`.

Special acknowledgement behavior:
- if EMR is enabled but the patient is not associated with Hemodynamics,
  the server sends:
  - `Profile accepted, but the patient is not associated with Hemodynamics.`

**Conclusion:**
This matches the workflow requirement that profile acceptance can succeed while still warning that Hemodynamics association is missing.

---

### Step 5 - Data Tick
Handled in `handleStep5DataTick(...)`

What happens first:
- validates that profile metadata was already received,
- validates device/session fields,
- persists the normal CDI/Core device tick through the existing path.

#### Additional Hemodynamics processing
If all conditions are true:
- current state is `CORE_STATE_MEASURING`,
- EMR is enabled,
- Hemodynamics streaming is enabled,
- patient is resolved,

then the server:
- loads latest Hemodynamics values using `HemodynamicsDataService`,
- maps them to V2 `ParamValue`s,
- creates `hemodynamics_data_tick`,
- sends it to the device over V2.

**Important note:**
The gRPC server is **not** pulling OpenEMR directly during this step. It is reading already-ingested Hemodynamics data from the local database.

**Conclusion:**
The gRPC server acts as the runtime bridge between stored EMR data and the core device.

---

### Step 6 - Manager Responses
Handled across the V2 service by:
- `sendAck(...)`
- stream config creation inside `handleStep4ProfileMetadata(...)`

Current responses sent by server:
- `ManagerAck`
- `StreamConfig`
- `PatientBind`
- `PatientRelease`
- `hemodynamics_profile_metadata`
- `hemodynamics_data_tick`

---

### Step 7 - Stop Case / Patient Release
Handled in `handleStep2CoreStateEvent(...)` and `sendAndPersistPatientRelease(...)`

When stop/shutdown-like reasons are received, the server:
- disables Hemodynamics streaming,
- persists patient release,
- sends `PatientRelease` to the device,
- clears session-level state:
  - `patientBound`
  - `patientDataFetched`
  - `profileMetadataReceived`
  - `hemodynamicsStreamingEnabled`
  - `isEmrEnabled`
  - `resolvedPatientId`

**Conclusion:**
The gRPC server owns the stop-case Hemodynamics shutdown behavior.

---

## 2.4 Hemodynamics Metadata and Value Mapping on gRPC Side

### `HemodynamicsDataService`
Responsibilities observed in code:
- get Hemodynamics parameter metadata,
- determine if patient has Hemodynamics association,
- fetch latest Hemodynamics row for patient,
- convert latest row to param values.

### `HemodynamicsParamMapper`
Maps DB/entity fields such as:
- MAP / mean arterial pressure,
- pulse rate,
- systolic BP,
- diastolic BP,
- EtCO2,
- arterial flow,
- sweep,
- FiO2,
- cerebral oxygen saturation,
- ACT,
- lactate,
- glucose.

**Conclusion:**
The gRPC server performs application-level parameter mapping and does not simply forward raw DB rows.

---

# 3. What Is Happening on the Device Manager Backend Side

Backend responsibilities are split into two major parts:

1. **EMR ingestion and storage**
2. **WebSocket/UI projection**

---

## 3.1 EMR Ingestion and Storage

### `HemodynamicsScheduler`
Responsibilities:
- polls OpenEMR on a schedule,
- obtains access token,
- fetches Hemodynamics payloads,
- maps payloads into DTOs,
- deduplicates records,
- enriches missing device type data,
- triggers ingestion into local DB.

**Conclusion:**
This is the entry point for getting Hemodynamics data from OpenEMR into the Device Manager system.

---

### `OpenEmrGatewayService`
Responsibilities:
- fetch Hemodynamics feed,
- fetch patient data,
- fetch encounter data,
- fetch device parameter data,
- handle retries and communication errors,
- build polling URIs with correct query behavior.

**Conclusion:**
This is the backend HTTP integration layer for OpenEMR.

---

### `FhirHemodynamicsMapper`
Responsibilities:
- parse FHIR bundle entries,
- extract `Observation` and `Device` resources,
- map observation codes into Hemodynamics DTO fields,
- normalize device/patient/encounter identifiers,
- group related measurements into one logical Hemodynamics record.

Mapped fields include:
- pulse rate,
- systolic / diastolic BP,
- mean arterial pressure,
- EtCO2,
- arterial flow,
- sweep,
- FiO2,
- cerebral oxygen saturation,
- ACT,
- lactate,
- glucose.

**Conclusion:**
The backend owns the FHIR-to-domain translation logic.

---

### `HemodynamicsService`
Responsibilities:
- idempotent upsert of Hemodynamics rows,
- persistence using `source_system + source_record_id`,
- updating existing rows or inserting new rows,
- storing normalized Hemodynamics values for later retrieval.

**Conclusion:**
The backend owns Hemodynamics persistence.

---

## 3.2 Backend/UI Projection Responsibilities

### `DeviceProfileService`
This service publishes profile-level data to the UI.

EMR-specific behavior:
- builds an `EmrSection` for the profile,
- includes EMR enabled flag,
- includes patient mapping status,
- includes mapped EMR device ID,
- includes EMR parameter list and modality summary,
- publishes profile websocket payloads.

Topic used:
- `/topic/device/{deviceId}/profile`

**Conclusion:**
The backend profile layer already supports EMR-aware profile visualization.

---

### `DeviceEmrDataTickService`
This service builds EMR datatick messages for the UI.

Responsibilities:
- resolve active device-to-patient mapping,
- get latest Hemodynamics row,
- ensure latest profile has `emrEnabled = true`,
- resolve selected EMR params from profile,
- convert Hemodynamics values into UI metric items,
- publish the message to websocket.

Topic used:
- `/topic/device/{deviceId}/emr/datatick`

**Conclusion:**
The backend already supports live EMR/Hemodynamics data delivery to the UI.

---

### `DeviceEmrDataTickPollingScheduler`
Responsibilities:
- poll the Hemodynamics table for new records,
- detect new rows since last seen ID,
- resolve active device-patient mappings,
- publish new EMR dataticks to mapped devices.

**Conclusion:**
This is the real-time backend broadcaster for new EMR/Hemodynamics data.

---

### `WebSocketSessionManager`
Responsibilities:
- manages websocket subscriptions,
- detects subscriptions to device-specific routes,
- publishes initial snapshots when a client subscribes.

Relevant EMR-aware routes:
- `/topic/device/{deviceId}/profile`
- `/topic/device/{deviceId}/emr/datatick`

**Conclusion:**
The backend already supports initial EMR profile/datatick snapshot behavior for UI consumers.

---

# 4. What the Device / Simulator Is Expected to Handle

Based on the plan and current code, the device/simulator must continue handling all normal CDI/core functionality and additionally support the new Hemodynamics V2 stream.

## 4.1 Existing behavior that remains unchanged
The device must continue to support the existing CDI/V1 workflow:
- `DeviceAnnouncement`
- `CoreStateEvent`
- `ProfileMetadata`
- `MeasurementDataTick`
- receiving `PatientBind`
- receiving `PatientRelease`
- receiving `StreamConfig`
- receiving `ManagerAck`

---

## 4.2 New behavior expected on the device side
The device/simulator must additionally:

### Receive via `TelemetryServiceV2`
- `hemodynamics_profile_metadata`
- `hemodynamics_data_tick`

### Maintain local V2/EMR runtime state
- EMR enabled / disabled flag,
- metadata received flag,
- latest Hemodynamics value cache,
- EMR display state,
- stream active/stopped state.

### Use received metadata
- store Hemodynamics parameter definitions,
- merge CDI and Hemodynamics metadata for local visualization,
- display supported external parameter categories.

### Use received data ticks
- update EMR/Hemodynamics live values,
- refresh EMR display panel,
- keep CDI data transmission running on V1 in parallel.

### On StopCase
- stop accepting/processing Hemodynamics ticks,
- clear EMR live cache,
- update UI state to show EMR stopped,
- keep normal CDI workflow unchanged.

---

## 4.3 Device-side code currently visible in this repo
The actual planned Python simulator components are **not present** in this workspace.

Planned but not found here:
- `runner_v2.py`
- `telemetry_v2_service.py`
- `hemodynamics_manager.py`
- `hemodynamics_parameter_mapper.py`

What **is** present is:
- `device-manager-grpc-server/src/main/java/com/terumo/cdi/devicemanager/client/HemodynamicsDeviceClient.java`

This Java client acts as an example/test client and demonstrates the intended flow:
- open V1 stream,
- open V2 stream,
- send device announcement on both,
- send `IDLE/Startup`,
- receive `PatientBind`,
- receive `hemodynamics_profile_metadata`,
- send `STANDBY/SetProfile, EMR ON`,
- send profile metadata,
- send `MEASURING/StartCase`,
- send normal core dataticks,
- receive `hemodynamics_data_tick`,
- send `StopCase`,
- disconnect.

**Conclusion:**
The device-side implementation pattern is clear from the example client, but the actual simulator implementation described in the plan is still missing from this repository.

---

# 5. Plan vs Current Code Observations

## 5.1 V2 is not implemented as a tiny side-channel only
The plan suggests V2 is primarily for:
- manager -> device Hemodynamics metadata/data,
- device -> manager `DeviceAck`.

However, the actual V2 server implementation also processes device-originated:
- `DeviceAnnouncement`
- `CoreStateEvent`
- `ProfileMetadata`
- `MeasurementDataTick`

**Observation:**
In code, V2 currently mirrors part of the normal core workflow and is not only a minimal Hemodynamics side-stream.

---

## 5.2 `DeviceAck` is defined in proto but not fully handled in server logic
In `core_v2.proto`, `DeviceAck` is supported in `DeviceToManager`.

But in `TelemetryServiceV2Impl.onNext(...)`, the current dispatch handles only:
- `device_announcement`
- `core_state_event`
- `profile_metadata`
- `measurement_data_tick`

There is no visible branch for:
- `msg.hasDeviceAck()`

**Observation:**
The protocol and plan expect `DeviceAck`, but the current gRPC server logic does not appear to consume it yet.

---

## 5.3 Example device client does not appear to send `DeviceAck`
`HemodynamicsDeviceClient.java` logs received V2 messages, but does not appear to actively send `DeviceAck` back for:
- `hemodynamics_profile_metadata`
- `hemodynamics_data_tick`

**Observation:**
`DeviceAck` support appears planned but not fully completed end-to-end.

---

## 5.4 Parameter ID examples in the plan do not match current backend mapping examples
The plan gives examples such as:
- `1001 -> MAP`
- `1002 -> Pulse Rate`

But backend/UI code currently uses IDs like:
- `101 -> MAP`
- `102 -> Pulse Rate`
- `103 -> Systolic BP`
- ...
- `112 -> Glucose`

**Observation:**
The final param ID contract must be aligned between:
- backend DB / parameter definition mapping,
- gRPC messages,
- simulator/device-side mapper.

---

# 6. What Still Needs to Be Implemented on Device Side

Now that gRPC server logic and backend logic are already implemented, the main remaining work is on the device/simulator side.

## 6.1 Add full `TelemetryServiceV2` client/session support
The simulator/device needs a production V2 stream implementation that can:
- connect to `TelemetryServiceV2`,
- keep the session alive,
- receive manager-to-device Hemodynamics messages,
- maintain stream lifecycle and reconnect behavior if needed.

### Needed device-side components
Suggested from the original plan:
- `runner_v2.py`
- `telemetry_v2_service.py`

---

## 6.2 Implement `hemodynamics_profile_metadata` handling
The device must be able to:
- detect incoming `hemodynamics_profile_metadata`,
- parse all incoming params,
- store metadata locally,
- map param IDs to display labels/units/categories,
- expose metadata to simulator UI.

### Device-side state to store
- metadata received flag,
- list of Hemodynamics params,
- source device id,
- EMR-supported sections/categories.

---

## 6.3 Implement `hemodynamics_data_tick` handling
The device must be able to:
- detect incoming `hemodynamics_data_tick`,
- parse incoming `ParamValue`s,
- update local EMR/Hemodynamics cache,
- refresh live display values,
- keep CDI streaming active independently on V1.

### Expected behavior
- V1 CDI telemetry continues unchanged,
- V2 Hemodynamics values are received and displayed in parallel.

---

## 6.4 Implement `DeviceAck` sending on V2
Even though server-side handling appears incomplete today, device-side implementation should still be prepared because the protocol and plan require it.

The device should send `DeviceAck` at least for:
- `hemodynamics_profile_metadata`
- `hemodynamics_data_tick`

Recommended ack content:
- `ref_seq`
- `ack_for_message_type`
- `message`

Example ack types:
- `HEMODYNAMICS_PROFILE_METADATA`
- `HEMODYNAMICS_DATA_TICK`

---

## 6.5 Implement EMR toggle behavior in device UI/runtime
The simulator/device still needs explicit EMR mode behavior:
- EMR enabled
- EMR disabled

### When enabled
- receive and display Hemodynamics metadata,
- receive and display Hemodynamics dataticks,
- show combined CDI + EMR view.

### When disabled
- ignore or suppress Hemodynamics display behavior,
- continue CDI-only display.

---

## 6.6 Implement local Hemodynamics manager/cache
A dedicated device-side Hemodynamics manager should be added to hold:
- latest Hemodynamics metadata,
- latest Hemodynamics values,
- EMR enabled state,
- data streaming state,
- status strings for UI.

Suggested planned component:
- `hemodynamics_manager.py`

---

## 6.7 Implement device-side parameter mapping layer
The simulator/device should have a mapper that converts:
- param ID -> display name,
- param ID -> unit,
- param ID -> category / modality.

Suggested planned component:
- `hemodynamics_parameter_mapper.py`

This mapping must be aligned with actual backend param IDs currently used in code.

---

## 6.8 Implement combined simulator visualization
The device/simulator UI still needs to show:
- CDI values,
- EMR/Hemodynamics values,
- external device status,
- metadata received state,
- stream active/stopped state.

Suggested UI sections from plan:
- External Device Status
- Hemodynamic Monitor
- HLM / ECMO
- Cerebral Oximeter
- POC / Lab

---

## 6.9 Implement StopCase behavior locally on device
On stop case, the device/simulator should:
- stop processing incoming Hemodynamics values,
- clear EMR live cache,
- update UI to show EMR stream stopped,
- preserve CDI behavior.

---

# 7. Recommended Device-Side Implementation Checklist

## Must-have
- [ ] Create V2 runner/session handler
- [ ] Receive and parse `hemodynamics_profile_metadata`
- [ ] Receive and parse `hemodynamics_data_tick`
- [ ] Maintain Hemodynamics cache/state
- [ ] Show combined CDI + EMR display
- [ ] Stop EMR processing on `StopCase`
- [ ] Keep CDI V1 behavior unchanged

## Strongly recommended
- [ ] Implement `DeviceAck` sending for V2 messages
- [ ] Add param ID mapping layer aligned with backend IDs
- [ ] Add EMR enabled/disabled toggle behavior
- [ ] Add status panel for metadata received / streaming active / stopped

## Validation needed after implementation
- [ ] verify metadata arrives after `IDLE + Startup`
- [ ] verify profile flow still works unchanged
- [ ] verify non-associated patient produces warning ack
- [ ] verify Hemodynamics values arrive during `MEASURING`
- [ ] verify CDI V1 telemetry continues in parallel
- [ ] verify StopCase clears EMR cache and stops updates
- [ ] verify UI shows both CDI and EMR values correctly

---

# 8. Final Summary

## Already implemented in backend/gRPC side
- OpenEMR Hemodynamics polling
- FHIR Hemodynamics mapping
- Hemodynamics DB persistence
- Hemodynamics metadata retrieval
- patient association validation
- EMR enable/disable gating
- Hemodynamics V2 push from manager to device
- backend websocket publication for EMR profile and dataticks
- stop-case release and stream shutdown logic

## Still primarily pending on device/simulator side
- true simulator-side `TelemetryServiceV2` runtime implementation
- Hemodynamics metadata handling
- Hemodynamics datatick handling
- local Hemodynamics cache/state manager
- combined CDI + EMR simulator display
- device-side `DeviceAck` support
- EMR toggle behavior
- StopCase local cleanup behavior

---

# 9. Practical Conclusion

From the current codebase, the **backend side and gRPC manager side are largely implemented for Hemodynamics support**.

The main remaining work is to complete the **device/simulator-side V2 implementation** so that the core device can:
- receive Hemodynamics metadata,
- receive Hemodynamics values,
- manage local EMR state,
- display combined CDI + EMR telemetry,
- and send V2 acknowledgements as required by the final protocol design.

---

# 10. Next Options

If you want, next I can also create a shorter executive-summary MD, or a step-by-step workflow matrix table (`Step | Trigger | gRPC server | Backend | Device`) for easier sharing with the team.


