# CDI Core – Telemetry gRPC Client

Standalone C++ executable (`Core`) that connects to the Device Manager Java
gRPC server and runs the full telemetry flow defined in
`Telemetry_Flow_And_Metadata.md` (section 1 sequence diagram).

---

## Directory layout

```
TelemetryGrpcClient/
├── CMakeLists.txt      ← build definition; runs protoc at configure time
├── MockData.h          ← hardcoded device / param / tick data (no CDI SDK needed)
├── CoreClient.h        ← CoreClient class declaration
├── CoreClient.cpp      ← CoreClient implementation
├── main.cpp            ← 7-step telemetry flow + CLI args
└── README.md           ← this file

Proto source (read-only, shared):
  ../src/Telemetry/proto/telemetry.proto

Generated files (created in the build directory, not checked in):
  <build>/generated/telemetry.pb.h
  <build>/generated/telemetry.pb.cc
  <build>/generated/telemetry.grpc.pb.h
  <build>/generated/telemetry.grpc.pb.cc
```

---

## Prerequisites

| Tool | Version | Install (Ubuntu / Debian) |
|------|---------|---------------------------|
| CMake | ≥ 3.15 | `apt install cmake` |
| g++ | ≥ 9 (C++17) | `apt install g++` |
| protoc | ≥ 3.21 | `apt install protobuf-compiler` |
| grpc_cpp_plugin | matches protoc | `apt install protobuf-compiler-grpc` |
| libgrpc++-dev | ≥ 1.50 | `apt install libgrpc++-dev` |
| libprotobuf-dev | ≥ 3.21 | `apt install libprotobuf-dev` |

> **Note:** on some distros the packages are split differently.  
> A reliable cross-platform route is **vcpkg**:
> ```bash
> vcpkg install grpc protobuf
> # then add  -DCMAKE_TOOLCHAIN_FILE=<vcpkg>/scripts/buildsystems/vcpkg.cmake
> ```

---

## Build

```bash
# From the repo root (adjust the path as needed)
cd Linux/CdiCoreMain/TelemetryGrpcClient

mkdir build && cd build

# Standard system-installed gRPC / protobuf
cmake ..

# --- OR with vcpkg ---
# cmake .. -DCMAKE_TOOLCHAIN_FILE=/opt/vcpkg/scripts/buildsystems/vcpkg.cmake

cmake --build . -j$(nproc)
```

The CMake configure step automatically runs `protoc` to generate
`generated/telemetry.pb.*` and `generated/telemetry.grpc.pb.*` inside the
build directory.  The final binary is `build/Core`.

---

## Run

```bash
# Connect to the Java Device Manager on the default port (localhost:5555)
./build/Core

# Custom host / port / tick count
./build/Core --host 192.168.1.10 --port 5555 --ticks 10

# Show all options
./build/Core --help
```

### CLI flags

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--host` | `-h` | `localhost` | Device Manager hostname or IP |
| `--port` | `-p` | `5555` | Device Manager gRPC port |
| `--ticks` | `-n` | `5` | Number of DataTick messages to send |

---

## Telemetry flow

```
Core (C++)                                    Device Manager (Java)
────────────────────────────────────────────────────────────────────
Connect InsecureChannel
Open TelemetrySession (bidirectional stream)

→  DeviceAnnouncement                       ←  ManagerAck(DeviceAnnouncement)
→  ProfileMetadata (10 params)              ←  ManagerAck(ProfileMetadata)
                                            ←  [StreamConfig] (optional)
                                            ←  [PatientBind]  (optional, DM-initiated)
→  CoreStateEvent(MEASURING, "StartCase")   ←  ManagerAck(CoreStateEvent)
→  DataTick seq=3001  (1 s sleep)           ←  ManagerAck(DataTick)
→  DataTick seq=3002  (1 s sleep)           ←  ManagerAck(DataTick)
   … × N ticks …
→  CoreStateEvent(IDLE, "StopCase")         ←  ManagerAck(CoreStateEvent)
                                            ←  [PatientRelease] (optional)
WritesDone → Finish (stream closed)
```

---

## Mock data

All values come from the JSON samples in `Telemetry_Flow_And_Metadata.md`:

- **Device**: serial `C1234567`, device-id `CDI-C1234567`, sw `1.0.0`
- **Profile**: 10 parameters (param IDs 9, 42, 49, 50, 55, 60, 71, 73, 75, 89)  
  with alarm limits, ranges, and source device serials.
- **DataTick values**: 4 params (IDs 9, 42, 55, 71) with 5 sample rows;  
  rows cycle automatically when `--ticks` > 5.
- **Manual values**: HGB = 12.5 g/dL, SO2 = 65 %, DO2i threshold = 280 mL/min/m²

---

## Starting the Java Device Manager test server

The Python reference server at `telemetry_server.py` (repo root) can stand in
for the Java server during development:

```bash
# From the repo root
pip install grpcio grpcio-tools
python telemetry_server.py          # listens on 0.0.0.0:5555 by default
```

Then in a separate terminal:

```bash
./build/Core --ticks 5
```

---

## Security note

The client uses `grpc::InsecureChannelCredentials()` by default, which is
correct for local development against the Java/Python test server.

For production mTLS (matching `GrpcTelemetryTransport.cpp`), replace the
channel creation in `CoreClient::Connect()` with:

```cpp
grpc::SslCredentialsOptions opts;
opts.pem_root_certs   = ReadFile(std::getenv("CDI_TELEMETRY_CA_CERT"));
opts.pem_cert_chain   = ReadFile(std::getenv("CDI_TELEMETRY_CLIENT_CERT"));
opts.pem_private_key  = ReadFile(std::getenv("CDI_TELEMETRY_CLIENT_KEY"));
m_channel = grpc::CreateChannel(
    m_serverAddress,
    grpc::SslCredentials(opts));
```
