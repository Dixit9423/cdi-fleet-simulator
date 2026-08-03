# CDI Fleet + EMR Simulator Docker Deployment Guide

This document provides complete Docker configuration, deployment, and verification steps for both simulators:

- CDI Fleet Simulator (gRPC + control panel)
- EMR Simulator (REST + EMR UI)

## 1) Prerequisites

- Docker Engine installed on target host
- Docker service running
- Repository root available at:
  - `/path/to/cdi-fleet-simulator`

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

- `Dockerfile`

Key behavior:

- Uses `python:3.10-slim`
- Installs `requirements_fleet.txt`
- Copies simulator sources
- Runs as non-root user `simuser`
- Uses checked-in Python gRPC stubs (`telemetry_pb2.py` and `telemetry_pb2_grpc.py`)
- Exposes control panel port `8090`
- Starts simulator with default args:
  - `--insecure --control-port 8090 --no-persist`

### 2.2 .dockerignore

Path:

- `.dockerignore`

Purpose:

- Excludes Windows artifacts and build folders to reduce image size and speed up build.

### 2.3 docker-compose.yml

Path:

- `docker-compose.yml`

Purpose:

- Starts both services from one source tree:
  - `cdi-fleet` on port `8090`
  - `emr-sim` on port `3001`
- Includes service healthchecks for both APIs:
  - CDI: `/api/fleet/summary`
  - EMR: `/api/emr/devices`

## 3) Build image (from repo root)

```bash
cd /path/to/cdi-fleet-simulator

docker build \
  -t cdi-fleet-simulator:latest \
  -f Dockerfile \
  .
```

## 4) Run containers

### 4.1 Insecure mode (default / quick validation)

CDI Fleet only:

```bash
docker rm -f cdi-fleet-sim 2>/dev/null || true

docker run -d \
  --name cdi-fleet-sim \
  -p 8090:8090 \
  --restart unless-stopped \
  cdi-fleet-simulator:latest
```

### 4.2 EMR simulator only (standalone)

```bash
docker rm -f emr-sim 2>/dev/null || true

docker run -d \
  --name emr-sim \
  -p 3001:3001 \
  --restart unless-stopped \
  -e OPENEMR_ACCESS_TOKEN="<token-if-required>" \
  -v /path/to/emr_config_dir:/app/simulator/config:ro \
  cdi-fleet-simulator:latest \
  python run_emr_service.py --config-dir /app/simulator/config --ui-port 3001 --auto-start
```

### 4.3 CDI mTLS mode (optional)

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

### 4.4 Recommended CDI mode (mTLS + YAML bind mount)

Use this mode so config changes in `devices_config.yaml` apply without rebuilding image.

```bash
docker rm -f cdi-fleet-sim 2>/dev/null || true

docker run -d \
  --name cdi-fleet-sim \
  -p 8090:8090 \
  --restart unless-stopped \
  -v /path/to/devices_config.yaml:/app/devices_config.yaml \
  -v /path/to/certs:/path/to/certs:ro \
  cdi-fleet-simulator:latest \
  --control-port 8090 --no-persist
```

For IP/DNS mismatch in development, set `server.tls.server_name_override: "localhost"` in `devices_config.yaml`.

After YAML edits, just restart container:

```bash
docker restart cdi-fleet-sim
```

### 4.5 Run both services together (recommended)

```bash
cd /path/to/cdi-fleet-simulator

# Optional: export token before compose up
export OPENEMR_ACCESS_TOKEN="<token-if-required>"

docker compose up -d --build
```

Start only one service from compose:

```bash
docker compose up -d --build cdi-fleet
docker compose up -d --build emr-sim
```

Stop both:

```bash
docker compose down
```

## 5) Verify deployment

## 5.1 Container status

```bash
docker ps --filter name=cdi-fleet-sim
docker ps --filter name=emr-sim
```

Expected:

- CDI container is `Up` with `0.0.0.0:8090->8090/tcp`
- EMR container is `Up` with `0.0.0.0:3001->3001/tcp`
- Health status should become `healthy` after startup warmup

## 5.2 Service logs

```bash
docker logs --tail 100 cdi-fleet-sim
docker logs --tail 100 emr-sim
```

Expected indicators:

- `CDI Core Fleet Simulator`
- `Control panel : http://localhost:8090`
- fleet/device startup logs
- `[EMRService] Started`
- `UI: http://localhost:3001`

## 5.3 API health checks

```bash
curl -s http://127.0.0.1:8090/api/fleet/summary
curl -s http://127.0.0.1:8090/api/fleet
curl -s http://127.0.0.1:3001/api/emr/devices
```

Expected: valid JSON responses.

## 5.4 Browser check

Open:

- `http://<remote-host>:8090`
- `http://<remote-host>:3001`

Expected:

- CDI control panel loads with fleet cards
- EMR UI loads with EMR device list

## 6) Operations

### Stop (individual)

```bash
docker stop cdi-fleet-sim
docker stop emr-sim
```

### Start (individual)

```bash
docker start cdi-fleet-sim
docker start emr-sim
```

### Restart (individual)

```bash
docker restart cdi-fleet-sim
docker restart emr-sim
```

### Remove (individual)

```bash
docker rm -f cdi-fleet-sim
docker rm -f emr-sim
```

### Compose lifecycle (both)

```bash
docker compose up -d --build
docker compose restart
docker compose down
```

## 7) Troubleshooting

- If API not reachable, confirm port mapping:
  - `docker port cdi-fleet-sim`
- If container exits immediately, inspect logs:
  - `docker logs cdi-fleet-sim`
- If mTLS fails, verify cert files and YAML TLS paths.
- If gRPC server is unavailable, simulator still exposes control panel in insecure test mode.

## 8) Sharing with Other Teams

Your Docker setup is ready to share. You can distribute it in one of these ways.

### 8.1 Share source + compose (simplest)

- Share this repository (Git branch/tag).
- User instructions are in this file: `DOCKER_SIMULATOR_DEPLOYMENT.md`.
- Team runs:

```bash
git clone <repo-url>
cd cdi-fleet-simulator
docker compose up -d --build
```

Best when teams can access the source repository.

### 8.2 Share prebuilt image via registry (recommended for operations)

Build and push once:

```bash
docker build -t <registry>/cdi-fleet-simulator:1.0.0 .
docker push <registry>/cdi-fleet-simulator:1.0.0
```

Then consumers pull and run (or reference it from compose):

```bash
docker pull <registry>/cdi-fleet-simulator:1.0.0
```

Best when teams should not build locally.

### 8.3 Share image as tar file (offline/air-gapped)

Export image:

```bash
docker save -o cdi-fleet-simulator_1.0.0.tar cdi-fleet-simulator:latest
```

On target host:

```bash
docker load -i cdi-fleet-simulator_1.0.0.tar
docker compose up -d
```

Best for restricted environments.

### 8.4 What users must edit before running

- CDI config: `devices_config.yaml`
- EMR config: `simulator/config/emr_config.yaml` (or split files in `simulator/config`)
- Optional token env: `OPENEMR_ACCESS_TOKEN`

After config updates, restart affected service:

```bash
docker compose restart cdi-fleet
docker compose restart emr-sim
```
