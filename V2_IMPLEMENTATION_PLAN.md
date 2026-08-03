# Core V2 Implementation Plan

## Goal

Add support for the new `CdiTelemetryV2.TelemetryServiceV2` contract while keeping the existing fleet simulator, v1 protobuf contract, and current runtime behavior unchanged.

## Current State

- `core_v2.proto` compiles successfully into `core_v2_pb2.py` and `core_v2_pb2_grpc.py`.
- The existing simulator still imports and uses `telemetry_pb2.py` and `telemetry_pb2_grpc.py` for the v1 service.
- No v1 files need to change to introduce v2 support.

## Implementation Steps

1. Keep v1 and v2 protobufs separate.

- Leave the current checked-in v1 stubs in place.
- Treat `core_v2.proto` as an additive contract only.
- Do not rename or replace the existing `telemetry_pb2*` modules.

2. Add a dedicated v2 runtime path.

- Create a new v2 service wrapper and/or runner module that imports `core_v2_pb2` and `core_v2_pb2_grpc`.
- Register `TelemetryServiceV2` separately from the current Telemetry service.
- Keep the existing fleet `DeviceRunner` on the v1 stream path unless a device is explicitly configured for v2.

3. Route new behavior through configuration.

- Add an explicit device or mode flag for `proto_version = v2` or equivalent.
- Use that flag to select the v2 runner/service path at startup.
- Keep the default mode on v1 so existing deployments behave exactly as before.

4. Map message handling by contract, not by reuse of v1 types.

- Implement v2 handlers for `ManagerToDevice` and `DeviceToManager` using the v2 generated classes.
- Preserve v1 state-machine logic separately so a v2 change cannot regress the old stream.
- Only share pure helpers where the payload semantics are identical and the helper has no protobuf-specific assumptions.

5. Update packaging and startup wiring.

- Add `core_v2_pb2.py` and `core_v2_pb2_grpc.py` to any bundle, PyInstaller, or Docker packaging that currently ships the v1 stubs.
- Ensure the entrypoint can import both versions without ambiguity.
- Keep the v1 import path as the default resolution path for existing code.

6. Add verification for both contracts.

- Run the existing fleet startup path to confirm v1 still works unchanged.
- Add a minimal v2 smoke test that imports the new stubs and starts a v2 servicer or client stub.
- Verify the generated service name is `/CdiTelemetryV2.TelemetryServiceV2/TelemetrySession` and does not overlap with v1.

## Safety Boundaries

- Do not edit `telemetry_pb2.py` or `telemetry_pb2_grpc.py` as part of the v2 rollout.
- Do not change the current fleet control panel behavior unless it needs an explicit v2 entry point.
- Do not reuse v1 message names as aliases for v2 behavior.

## Recommended Order

1. Add a v2 service module and wire it in behind a config flag.
2. Add packaging updates for the new generated files.
3. Add a smoke test or startup validation for the v2 path.
4. Keep v1 as the default and confirm the current simulator still starts unchanged.
