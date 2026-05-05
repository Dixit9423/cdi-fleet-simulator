# CDI Fleet Simulator v1.0.2

## Highlights

This release adds first-class Docker deployment so users can run the simulator safely and quickly without unzipping Windows bundles.

## What is included

- Dockerized simulator runtime
  - [Dockerfile](Dockerfile)
  - [.dockerignore](.dockerignore)
- Deployment and operations guide
  - [DOCKER_SIMULATOR_DEPLOYMENT.md](DOCKER_SIMULATOR_DEPLOYMENT.md)
- YAML bind-mount support for fast config updates
  - Update [devices_config.yaml](devices_config.yaml)
  - Restart container (no image rebuild required)
- TLS hostname override support for dev environments
  - Set `server.tls.server_name_override: "localhost"` in [devices_config.yaml](devices_config.yaml)

## Why Docker over zip bundles

- Faster startup and repeatable environment
- Safer dependency/runtime isolation
- No manual extraction of executable packages
- Simple upgrade/rollback via image tags

## Recommended run mode (mTLS + YAML bind mount)

```bash
docker run -d \
  --name cdi-fleet-sim \
  -p 8090:8090 \
  --restart unless-stopped \
  -v /path/to/devices_config.yaml:/app/Linux/CdiCoreMain/TelemetryGrpcClient/devices_config.yaml \
  -v /path/to/certs:/path/to/certs:ro \
  cdi-fleet-simulator:latest \
  --control-port 8090 --no-persist
```

## Verify

```bash
docker ps --filter name=cdi-fleet-sim
curl -s http://127.0.0.1:8090/api/fleet/summary
docker logs --tail 100 cdi-fleet-sim
```
