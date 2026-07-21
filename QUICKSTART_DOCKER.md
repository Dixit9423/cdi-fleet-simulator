# Docker Quickstart (CDI + EMR)

Use this when you want the fastest setup on Linux.

## Prerequisites

- Docker Engine with Docker Compose plugin
- Access to this repository

## 8 Commands

1. Go to repo

```bash
cd /path/to/cdi-fleet-simulator
```

2. Optional: set OpenEMR token (skip if not needed)

```bash
export OPENEMR_ACCESS_TOKEN="your_token"
```

3. Build and start both simulators

```bash
docker compose up -d --build
```

4. Verify containers are running

```bash
docker ps --filter name=cdi-fleet-sim
docker ps --filter name=emr-sim
```

5. Check logs

```bash
docker logs --tail 100 cdi-fleet-sim
docker logs --tail 100 emr-sim
```

6. Edit config files on host

```bash
nano devices_config.yaml
nano simulator/config/emr_config.yaml
```

7. Apply config changes

```bash
docker compose restart cdi-fleet emr-sim
```

8. Stop everything

```bash
docker compose down
```

## URLs

- CDI control panel: http://<host>:8090
- EMR UI: http://<host>:3001

## Notes

- Config is shared from host into containers via volumes.
- Code changes require rebuild: `docker compose up -d --build`.
- Full instructions: see DOCKER_SIMULATOR_DEPLOYMENT.md
