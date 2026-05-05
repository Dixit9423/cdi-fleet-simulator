# CDI Fleet Simulator Docker Deployment Guide

This document provides complete Docker configuration, deployment, and verification steps for the Fleet Simulator.

## 1) Prerequisites

- Docker Engine installed on target host
- Docker service running
- Repository root available at:
  - `/home/abc/Repo/Development_Workspaces/Branches/Dixit/Core`

## 1.1) Docker daemon registry configuration (required in this network)

On this environment, Docker pull may fail with:

- `tls: failed to verify certificate: x509: certificate signed by unknown authority`

Apply this daemon configuration on the host:

`/etc/docker/daemon.json`

```json
{
  "insecure-registries": [
    "registry-1.docker.io",
    "docker-images-prod.6aa30f8b08e16409b46e0173d6de2f56.r2.cloudflarestorage.com"
  ]
}
```

Then restart Docker:

```bash
sudo systemctl restart docker
```

Validation:

```bash
docker pull hello-world
```

## 2) Container configuration files

### 2.1 Dockerfile

Path:
- `Linux/CdiCoreMain/TelemetryGrpcClient/Dockerfile`

Key behavior:
- Uses `python:3.10-slim`
- Installs `requirements_fleet.txt`
- Copies simulator sources
- Copies `telemetry.proto` and generates Python gRPC stubs inside image via `grpc_tools.protoc`
- Exposes control panel port `8090`
- Starts simulator with default args:
  - `--insecure --control-port 8090 --no-persist`

### 2.2 .dockerignore

Path:
- `Linux/CdiCoreMain/TelemetryGrpcClient/.dockerignore`

Purpose:
- Excludes Windows artifacts and build folders to reduce image size and speed up build.

## 3) Build image (from repo root)

```bash
cd /home/abc/Repo/Development_Workspaces/Branches/Dixit/Core

docker build \
  -t cdi-fleet-simulator:latest \
  -f Linux/CdiCoreMain/TelemetryGrpcClient/Dockerfile \
  .
```

## 4) Run container

### 4.1 Insecure mode (default / quick validation)

```bash
docker rm -f cdi-fleet-sim 2>/dev/null || true

docker run -d \
  --name cdi-fleet-sim \
  -p 8090:8090 \
  --restart unless-stopped \
  cdi-fleet-simulator:latest
```

### 4.2 mTLS mode (optional)

If using mTLS, ensure certs are mounted and `devices_config.yaml` points to mounted cert paths.

Example:

```bash
docker rm -f cdi-fleet-sim 2>/dev/null || true

docker run -d \
  --name cdi-fleet-sim \
  -p 8090:8090 \
  -v /path/to/certs:/path/to/certs:ro \
  --restart unless-stopped \
  cdi-fleet-simulator:latest \
  --control-port 8090
```

### 4.3 Recommended mode (mTLS + YAML bind mount)

Use this mode so config changes in `devices_config.yaml` apply without rebuilding image.

```bash
docker rm -f cdi-fleet-sim 2>/dev/null || true

docker run -d \
  --name cdi-fleet-sim \
  -p 8090:8090 \
  --restart unless-stopped \
  -v /path/to/devices_config.yaml:/app/Linux/CdiCoreMain/TelemetryGrpcClient/devices_config.yaml \
  -v /path/to/certs:/path/to/certs:ro \
  cdi-fleet-simulator:latest \
  --control-port 8090 --no-persist
```

For IP/DNS mismatch in development, set `server.tls.server_name_override: "localhost"` in `devices_config.yaml`.

After YAML edits, just restart container:

```bash
docker restart cdi-fleet-sim
```

## 5) Verify deployment

## 5.1 Container status

```bash
docker ps --filter name=cdi-fleet-sim
```

Expected: container is `Up` and port mapping includes `0.0.0.0:8090->8090/tcp`.

## 5.2 Service logs

```bash
docker logs --tail 100 cdi-fleet-sim
```

Expected indicators:
- `CDI Core Fleet Simulator`
- `Control panel : http://localhost:8090`
- fleet/device startup logs

## 5.3 API health checks

```bash
curl -s http://127.0.0.1:8090/api/fleet/summary
curl -s http://127.0.0.1:8090/api/fleet
```

Expected: valid JSON responses.

## 5.4 Browser check

Open:
- `http://<remote-host>:8090`

Expected: control panel loads with fleet cards.

## 6) Operations

### Stop

```bash
docker stop cdi-fleet-sim
```

### Start

```bash
docker start cdi-fleet-sim
```

### Restart

```bash
docker restart cdi-fleet-sim
```

### Remove

```bash
docker rm -f cdi-fleet-sim
```

## 7) Troubleshooting

- If API not reachable, confirm port mapping:
  - `docker port cdi-fleet-sim`
- If container exits immediately, inspect logs:
  - `docker logs cdi-fleet-sim`
- If mTLS fails, verify cert files and YAML TLS paths.
- If gRPC server is unavailable, simulator still exposes control panel in insecure test mode.
