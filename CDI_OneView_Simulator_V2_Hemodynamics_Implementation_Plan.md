CDI OneView Simulator V2 + Hemodynamics Integration Implementation Plan

1. Objective
   Implement support for the new TelemetryServiceV2 contract in the CDI OneView Simulator and add External Hemodynamics Device Integration capabilities while:
   • Keeping the existing V1 simulator unchanged.
   • Keeping existing fleet/device simulation behavior unchanged.
   • Implementing all future CDI OneView device functionality in the simulator.
   • Supporting Hemodynamics metadata and data streaming through Device Manager.
   • Maintaining backward compatibility with all existing CDI telemetry workflows.
   • Allowing Device Manager UI to visualize both CDI telemetry data and Hemodynamics telemetry data.

2. Current Architecture
   Existing CDI OneView Flow (V1)
   1 CDI OneView Simulator
   2 ↓ gRPC (V1)
   3 Device Manager Backend
   4 ↓
   5 PostgreSQL
   6 ↓
   7 Backend Services
   8 ↓
   9 REST API / WebSocket
   10 ↓
   11 Device Manager UI
   Existing Data
   Simulator generates CDI parameters such as:
   • SpO₂
   • Temperature
   • Flow
   • Pressure
   • Pulse
   • NIBP
   • Other CDI parameters
   These parameters are streamed to Device Manager and displayed in:
   • Dashboard
   • Live Monitor Screen
   within Device Manager UI.

3. Enhanced Architecture
   CDI DATA FLOW (Existing - V1)
   1 CDI OneView Simulator
   2 ↓ gRPC (V1)
   3 Device Manager Backend
   4 ↓
   5 PostgreSQL
   6 ↓
   7 Backend Services
   8 ↓
   9 REST API / WebSocket
   10 ↓
   11 Device Manager UI

HEMODYNAMICS DATA FLOW (New - V2)
1 Hemodynamics Simulator
2 ↓
3 EMR MySQL
4 ↓
5 Gateway Pull Service
6 ↓
7 Device Manager Backend
8 ↓ TelemetryServiceV2
9 CDI OneView Simulator
10 ↓
11 Display EMR Parameters
12  
13 CDI OneView Simulator
14 ↓ DeviceAck (V2)
15 TelemetryServiceV2
16 ↓
17 Device Manager Backend

Combined Runtime Architecture
1 DEVICE MANAGER
2  
3 ┌──────────────────────────────────┐
4 │ Device Manager Backend │
5 └───────────────┬──────────────────┘
6 │
7 ┌────────────┴────────────┐
8 │ │
9  
10 │ V1 │ V2
11  
12 ▼ ▼
13  
14 CDI OneView Simulator Hemodynamics Integration
15  
16 DeviceAnnouncement hemodynamics_profile_metadata
17  
18 CoreStateEvent hemodynamics_data_tick
19  
20 ProfileMetadata
21  
22 MeasurementDataTick
23  
24 PatientBind
25  
26 PatientRelease
27  
28 StreamConfig
29  
30 ManagerAck
31  
32 ▲
33 │
34 DeviceAck

4. V2 Protocol Usage
   Existing V1 Protocol (Unchanged)
   The following messages continue using the existing V1 telemetry service:
   1 DeviceAnnouncement
   2 CoreStateEvent
   3 ProfileMetadata
   4 MeasurementDataTick
   5 PatientBind
   6 PatientRelease
   7 StreamConfig
   8 ManagerAck
   No changes are required for the existing CDI implementation.

TelemetryServiceV2 Usage
Device Manager → Simulator
1 hemodynamics_profile_metadata
2  
3 hemodynamics_data_tick
Simulator → Device Manager
1 DeviceAck

Current Device-Side Status in This Repository
• Implemented: V2 stream selection by `proto_version`, receive handling for `hemodynamics_profile_metadata` and `hemodynamics_data_tick`, and `DeviceAck` sending for both message types.
• Pending: persistent Hemodynamics cache/state manager, EMR enabled/disabled runtime gating, combined CDI+Hemodynamics local UI view, and StopCase cache-clear behavior.
• Important runtime note: current simulator uses one protocol path per device (`v1` or `v2`) based on config; same-device dual-stream (`v1` + `v2` in parallel) is still planned work.

5. Simulator Functional Requirements
   Existing Features to Remain Unchanged
   Device Announcement
   1 DeviceAnnouncement
   No changes.

Patient Binding
1 PatientBind
2 PatientRelease
No changes.

Data Tick Streaming
1 MeasurementDataTick
Existing CDI parameter streaming remains unchanged.

Stop Case Processing
Existing behavior remains unchanged.

6. New V2 Components
   Component 1: V2 Runner
   Target Module (Planned)
   1 simulator/
   2 ├─ runner_v1.py
   3 └─ runner_v2.py
   Responsibilities
   • Connect using TelemetryServiceV2.
   • Receive Hemodynamics metadata.
   • Receive Hemodynamics data ticks.
   • Send DeviceAck responses.
   • Maintain V2 state.

Current Code Reality
• This repository currently uses a unified device runner with conditional v1/v2 handling rather than separate `runner_v1.py`/`runner_v2.py` files.

Component 2: V2 Stream Handler
Target Module (Planned)
1 services/
2 └─ telemetry_v2_service.py
Responsibilities
Handle:
1 hemodynamics_profile_metadata
2  
3 hemodynamics_data_tick
Send:
1 DeviceAck

Current Code Reality
• Stream handling is currently implemented inside the existing runner flow; extracting to `telemetry_v2_service.py` remains a refactor task.

Component 3: Hemodynamics Manager
Target Module (Planned)
1 services/
2 └─ hemodynamics_manager.py
Responsibilities
Store:
• Hemodynamics Metadata
• Hemodynamics Parameter Values
• EMR Connection State
• EMR Display State
• Cache of Latest Hemodynamics Values

Current Code Reality
• A dedicated Hemodynamics manager/cache is not yet implemented in simulator runtime state.

7. Configuration Changes
   Add New Configuration
   1 {
   2 "proto_version": "v2",
   3 "emr_enabled": true,
   4 "supports_hemodynamics": true
   5 }

Current Code Reality
• Implemented: `proto_version` (`v1`/`v2`) selection is supported.
• Pending: `emr_enabled` and `supports_hemodynamics` as fleet device config controls for V2 processing/display behavior.

Supported Modes
Existing
1 {
2 "proto_version": "v1"
3 }
New
1 {
2 "proto_version": "v2"
3 }

8. Step 1: Device Announcement
   Existing Flow
   1 Simulator
   2 → DeviceAnnouncement
   3  
   4 Device Manager
   5 → Ack
   Changes
   None.

9. Step 2: Startup Metadata Retrieval
   Trigger
   1 CoreStateEvent
   2  
   3 State = CORE_STATE_IDLE
   4 Reason = Startup

Device Manager Action
Device Manager retrieves Hemodynamics metadata and sends:
1 ManagerToDevice
2  
3 hemodynamics_profile_metadata
via TelemetryServiceV2.

Simulator Action
Receive:
1 hemodynamics_profile_metadata
Store:
1 External Device Metadata

Supported Simulated Device Types
Hemodynamic Monitor
1 MAP
2 Pulse Rate
3 Blood Pressure
4 EtCO₂
HLM / ECMO
1 Arterial Flow
2 Sweep
3 FiO₂
Cerebral Oximeter
1 rSO₂
Point-of-Care / Lab
1 ACT
2 Lactate
3 Glucose

UI Update
Display:
1 EMR Metadata Available

10. Step 3: Patient Binding
    No changes.
    Current V1 implementation remains unchanged.

11. Step 4: EMR Compatibility Feature
    New Simulator UI Control
    1 External Device Integration
    2  
    3 ☐ Disabled
    4  
    5 ☑ Enabled

New Internal State
1 emr_enabled = True | False

Current Device-Side Status
• Pending in fleet device runtime: explicit `emr_enabled` gating for accepting/ignoring Hemodynamics messages and UI display behavior.

Enable Flow
When enabled:
1 Use Hemodynamics Metadata
2  
3 Receive Hemodynamics Data
4  
5 Display Hemodynamics Values
6  
7 Display CDI Values
8  
9 Display Combined View

Disable Flow
When disabled:
1 Ignore Hemodynamics Data
2  
3 Display CDI Parameters Only

12. Profile Metadata Handling
    When EMR compatibility is enabled:
    The simulator shall merge:
    1 CDI Parameter Metadata
    2  
    3 +
    4  
    5 Hemodynamics Parameter Metadata
    for local display purposes.

CDI Parameters
1 SpO₂
2 Temperature
3 Pressure
4 Flow

Hemodynamics Parameters
1 MAP
2 Pulse Rate
3 Blood Pressure
4 EtCO₂
5  
6 Arterial Flow
7 Sweep
8 FiO₂
9  
10 rSO₂
11  
12 ACT
13 Lactate
14 Glucose

Important
ProfileMetadata transmission to Device Manager continues through the existing V1 telemetry workflow.
1 TelemetryServiceV2
2 shall NOT be used
3 for ProfileMetadata transmission.

13. Step 5: Measuring State
    Trigger
    1 CoreStateEvent
    2  
    3 State = CORE_STATE_MEASURING

Device Manager Sends
1 ManagerToDevice
2  
3 hemodynamics_data_tick
using TelemetryServiceV2.

Simulator Receives
1 hemodynamics_data_tick
Containing values such as:
1 MAP
2 Pulse Rate
3 Blood Pressure
4 EtCO₂
5  
6 Arterial Flow
7 Sweep
8 FiO₂
9  
10 rSO₂
11  
12 ACT
13 Lactate
14 Glucose

Internal Processing
1 Store latest values
2  
3 Update EMR cache
4  
5 Refresh EMR display

Current Device-Side Status
• Partial: message receipt + `DeviceAck` are implemented.
• Pending: storing latest Hemodynamics values in persistent per-device state and surfacing them in combined local display.

Existing CDI Data Streaming Continues
While receiving:
1 hemodynamics_data_tick
through TelemetryServiceV2,
the target architecture expects CDI telemetry to continue through the V1 channel.
Existing V1 Stream
1 MeasurementDataTick
Examples:
1 SpO₂
2 Temperature
3 Pressure
4 Flow

Result
1 Receive EMR Data through V2
2  
3 AND
4  
5 Transmit CDI Data through V1
simultaneously.

Current Device-Side Status
• This same-device simultaneous V1+V2 behavior is not yet implemented in the current simulator runtime.
• Current implementation runs one protocol path per device (`v1` or `v2`) selected via config.

Display Logic
CDI Section
1 SpO₂
2 Temperature
3 Pressure
4 Flow
EMR Section
1 MAP
2 Pulse Rate
3 Blood Pressure
4 EtCO₂
5  
6 Arterial Flow
7 Sweep
8 FiO₂
9  
10 rSO₂
11  
12 ACT
13 Lactate
14 Glucose

14. Data Mapping Layer
    New Module
    1 hemodynamics_parameter_mapper.py
    Purpose
    1 param_id
    2 ↓
    3 Display Name
    4 ↓
    5 Unit
    6 ↓
    7 Display Category
    Example
    1 1001 → MAP
    2  
    3 1002 → Pulse Rate
    4  
    5 1003 → Blood Pressure

15. V2 Message Handling
    V2 Handlers
    1 handle_hemodynamics_profile_metadata()
    2  
    3 handle_hemodynamics_data_tick()
    4  
    5 send_device_ack()

Existing V1 Handlers Remain Unchanged
1 DeviceAnnouncement
2  
3 CoreStateEvent
4  
5 ProfileMetadata
6  
7 MeasurementDataTick
8  
9 PatientBind
10  
11 PatientRelease
12  
13 StreamConfig
14  
15 ManagerAck

16. Existing V1 Communication
    The following workflows continue using the existing V1 telemetry implementation:
    1 PatientBind
    2  
    3 PatientRelease
    4  
    5 StreamConfig
    6  
    7 ManagerAck
    8  
    9 ProfileMetadata
    10  
    11 MeasurementDataTick
    No changes are required as part of TelemetryServiceV2 rollout.

17. Step 7: Stop Case
    Trigger
    1 CoreStateEvent
    2  
    3 Reason = StopCase

Simulator Actions
Stop EMR Processing
1 Stop accepting Hemodynamics DataTicks

Clear Live EMR Values
1 Hemodynamics Values Cache

UI Update
1 EMR Stream Stopped

Current Device-Side Status
• Pending in fleet simulator runtime: dedicated Hemodynamics cache clear and stream-status state update on StopCase.

Existing CDI Workflow
Continue unchanged.

18. Step 8: Disconnection
    No changes.
    Current disconnect logic remains unchanged.

19. Simulator UI Enhancements
    New Panel
    External Device Status
    1 Connection Status
    2  
    3 EMR Enabled: Yes
    4  
    5 Metadata Received: Yes
    6  
    7 Data Streaming: Active

New Parameter Viewer
1 Hemodynamic Monitor
2  
3 MAP
4 Pulse Rate
5 Blood Pressure
6 EtCO₂
7  
8  
9 HLM / ECMO
10  
11 Arterial Flow
12 Sweep
13 FiO₂
14  
15  
16 Cerebral Oximeter
17  
18 rSO₂
19  
20  
21 POC / Lab
22  
23 ACT
24 Lactate
25 Glucose

20. Device Manager UI Visualization
    Overview
    The Hemodynamics enhancement is additive to the existing CDI workflow.
    Device Manager UI shall display both CDI telemetry and Hemodynamics telemetry.

Dashboard
Display:
1 CDI Parameters
2  
3 +
4  
5 Hemodynamics Parameters
Example:
1 SpO₂
2 Temperature
3 Pressure
4 Flow
5  
6 MAP
7 Blood Pressure
8 EtCO₂
9 ACT
10 Lactate
11 Glucose

Live Monitor Screen
Display:
1 CDI Telemetry Data
2  
3 +
4  
5 Hemodynamics Telemetry Data
Example:
1 CDI DATA
2 --------
3 SpO₂
4 Temperature
5 Pressure
6 Flow
7  
8 EMR DATA
9 --------
10 MAP
11 Blood Pressure
12 EtCO₂
13  
14 Arterial Flow
15 Sweep
16 FiO₂
17  
18 rSO₂
19  
20 ACT
21 Lactate
22 Glucose

Data Sources
CDI Data
1 Source:
2 MeasurementDataTick
3  
4 Transport:
5 Existing V1 Telemetry Service
Hemodynamics Data
1 Source:
2 hemodynamics_data_tick
3  
4 Transport:
5 TelemetryServiceV2

21. Testing Plan
    V1 Regression
    Verify:
    1 Device Announcement
    2  
    3 Patient Binding
    4  
    5 Profile Metadata
    6  
    7 Data Tick Streaming
    8  
    9 Stop Case
    10  
    11 Disconnect
    All unchanged.

V2 Connectivity Test
Verify:
1 TelemetryServiceV2 Startup
2  
3 Bidirectional Stream Creation
4  
5 DeviceAck Processing

Metadata Test
Verify receipt of:
1 hemodynamics_profile_metadata

Data Tick Test
Verify receipt of:
1 hemodynamics_data_tick

EMR Toggle Test
Enabled
1 EMR data visible
Disabled
1 EMR data hidden

Dashboard Validation
Verify:
1 CDI values visible
2  
3 Hemodynamics values visible
4  
5 Combined view displayed correctly

Live Monitor Validation
Verify:
1 Native CDI telemetry
2  
3 +
4  
5 Hemodynamics telemetry
6  
7 display together

Stop Case Test
Verify:
1 Stop Hemodynamics data processing
2  
3 Clear EMR value cache
4  
5 Retain CDI workflow

22. Final Deliverables
    New Runtime Components (Target)
    1 runner_v2.py
    2  
    3 telemetry_v2_service.py
    4  
    5 hemodynamics_manager.py
    6  
    7 hemodynamics_parameter_mapper.py

New Simulator Features
• TelemetryServiceV2 support
• Hemodynamics metadata reception
• Hemodynamics data reception
• DeviceAck support
• EMR compatibility toggle
• CDI + EMR combined visualization
• Dashboard and Live Monitor integration
• StopCase EMR shutdown handling

Current Delivery Status in This Repository
✅ TelemetryServiceV2 protocol stubs integrated and selectable by config (`proto_version`).
✅ Hemodynamics metadata receive path implemented.
✅ Hemodynamics data tick receive path implemented.
✅ DeviceAck send path implemented for hemodynamics metadata/data tick.
⚠️ Dedicated Hemodynamics cache/state manager pending.
⚠️ EMR enabled/disabled runtime toggle behavior (device-side gating) pending.
⚠️ Combined CDI + Hemodynamics local fleet UI view pending.
⚠️ StopCase Hemodynamics cache clear/status lifecycle pending.
⚠️ Same-device dual stream (V1 send + V2 receive simultaneously) pending.

Backward Compatibility
✅ Existing V1 simulator unchanged
✅ Existing fleet simulation unchanged
✅ Existing Device Manager integration unchanged
✅ Existing CDI telemetry workflow unchanged
✅ Existing Device Manager UI workflow unchanged
⚠️ V2 used only for Hemodynamics integration (target behavior; current runtime is per-device single-protocol mode)
✅ DeviceAck transmitted through V2
✅ Hemodynamics metadata and data received through V2
⚠️ Simulator acts as the future CDI OneView device implementation until actual device implementation is available (partially complete on device-side state/UI lifecycle)
