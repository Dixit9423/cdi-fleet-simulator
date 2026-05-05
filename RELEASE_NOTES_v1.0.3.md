# CDI Fleet Simulator v1.0.3

## Release Summary

This release promotes the Docker-first deployment workflow as the recommended way to run the simulator in development and test environments.

## Included in this version

- Docker image configuration:
  - `Dockerfile`
  - `.dockerignore`
- Deployment documentation:
  - `DOCKER_SIMULATOR_DEPLOYMENT.md`
- TLS hostname override support for development:
  - `server.tls.server_name_override` in `devices_config.yaml`
- YAML bind-mount runtime workflow:
  - update config in `devices_config.yaml`
  - restart container without rebuilding image

## Docker usage

Build:

```bash
docker build -t cdi-fleet-simulator:latest -f Linux/CdiCoreMain/TelemetryGrpcClient/Dockerfile .
```

Run (recommended):

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

Verify:

```bash
docker ps --filter name=cdi-fleet-sim
curl -s http://127.0.0.1:8090/api/fleet/summary
docker logs --tail 100 cdi-fleet-sim
```
