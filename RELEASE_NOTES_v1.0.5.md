# CDI Fleet Simulator v1.0.5

## Release Summary

This release packages the validated fleet metadata/reconnect fixes into a downloadable Windows executable zip and updates the release pipeline so future tags publish the same asset automatically.

## Included in this version

- Downloadable Windows package asset:
  - `CDI-Fleet-Simulator-v1.0.5-Windows.zip`
  - includes `fleet_simulator.exe`, `devices_config.yaml`, `README.md`, `FLEET_SIMULATOR_SUMMARY.md`, and release notes
- Capability-aligned parameter metadata in `devices_config.yaml`
  - BPM, H/SAT, and Core-calculated parameter names now follow capability labels used by runtime telemetry
- Corrected profile parameter mapping and sample tick IDs
  - `full_bypass` and `oxygen_delivery` now emit the intended metadata/tick parameter sets
- Improved reconnect behavior in `fleet_sim/device_runner.py`
  - devices resend `DeviceAnnouncement`
  - the current `CoreStateEvent` is replayed after reconnect
  - measuring sessions resume periodic `DataTick` traffic
- Automated release packaging
  - GitHub Actions now builds the Windows executable zip and attaches it to tagged releases

## Validation

- Source config loads successfully through `fleet_sim.config.load_config(...)`
- Release workflow is configured to build and upload the Windows zip asset on tag push

## Usage

1. Download `CDI-Fleet-Simulator-v1.0.5-Windows.zip` from the release assets
2. Extract the zip
3. Edit `devices_config.yaml` as needed
4. Run `fleet_simulator.exe`
5. Open `http://localhost:8090`
