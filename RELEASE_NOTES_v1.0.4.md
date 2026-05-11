# CDI Fleet Simulator v1.0.4

## Release Summary

This release corrects Fleet Simulator profile metadata so the published parameter names and IDs match CDI Core capability messages, and it improves stream recovery so devices resume cleanly after a gRPC reconnect.

## Included in this version

- Capability-aligned parameter metadata in `devices_config.yaml`
  - BPM parameter names updated to capability labels such as `pH`, `pCO2`, `pO2`, `HCO3`, `K+`, `SO2`, and `S. Temp`
  - H/SAT parameter names updated to `HCT`, `Hgb`, and `SO2`
  - Core calculated parameter names aligned to capability output such as `BE`, `VO2`, `VO2I`, `DO2`, `DO2I`, `O2ER`, `CI`, `AUC`, and `PO2`
- Corrected profile-to-parameter mapping
  - `full_bypass` now sends the relevant arterial BPM, arterial H/SAT, and core-calculated values
  - `oxygen_delivery` now uses `Flow_Blue` and includes `CI`/`VO2I` instead of irrelevant nadir data
- Updated sample tick data
  - Device sample values now use the corrected parameter IDs for profile metadata and DataTick payloads
- Improved reconnect behavior in `fleet_sim/device_runner.py`
  - After reconnect, the simulator resends `DeviceAnnouncement`
  - The current `CoreStateEvent` is replayed automatically
  - Measuring devices continue sending `DataTick` values using the active profile/session state

## Key fixes

- Removed irrelevant parameters from ProfileMetadata for the main fleet scenarios
- Stopped relying on enum-style names from `CdiParameterIds.h` when capability labels differ from runtime expectations
- Preserved reconnect recovery without forcing the user to restart a device thread manually

## Validation

- `devices_config.yaml` loads successfully through `fleet_sim.config.load_config(...)`
- Edited simulator files report no workspace errors after the change

## Recommended verification

```bash
python run_fleet.py
```

Then verify:

- ProfileMetadata shows capability-aligned parameter names and IDs
- Reconnecting the gRPC server causes devices to re-announce and replay current state
- Measuring devices resume periodic DataTick traffic after reconnect
