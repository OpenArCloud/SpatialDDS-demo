# Docker Reference — AR v1.7 Demo

This is the Docker reference for the **SpatialDDS v1.7 AR protocol demo** (the
`spatialdds_*` scripts at the repo root, plus `http_binding.py`).

The other demos have their own Docker launchers — use those instead:

| Demo | Launcher |
|---|---|
| Multi-operator fusion | [`multi_operator_fusion/run_docker_demo.sh`](multi_operator_fusion/run_docker_demo.sh) |
| nuScenes → Rerun | [`nuscenes/run_docker_demo.sh`](nuscenes/run_docker_demo.sh) |
| DeepSense → Rerun | [`deepsense/run_docker_demo.sh`](deepsense/run_docker_demo.sh) |
| Web demo (DDS bridge) | [`run_bridge_server_docker.sh`](run_bridge_server_docker.sh) |

Each demo's `README.md` documents its own Docker workflow.

> **Note on `http_binding.py`** — this is the spec-compliance REST wrapper, **not** the
> HTTP-to-DDS bridge that powers the web UI. For the web bridge see
> `run_bridge_server_docker.sh` and `bridges/web_bridge/server.py`.

## Building the Image

### First Time Build
```bash
docker build -t cyclonedds-python .
```

This builds the image with:
- A prebuilt Cyclone DDS base image (0.10.5)
- Python bindings (cyclonedds==0.10.5)
- All SpatialDDS v1.7 files
- DDS performance tools

**Build time:** ~5-10 minutes (depends on your machine)

### Rebuild After Changes
```bash
docker build -t cyclonedds-python --no-cache .
```

Use `--no-cache` to force a complete rebuild if you've updated files.

### Base Image (Cyclone DDS + idlc + Python bindings)
The main Dockerfile uses:
`ghcr.io/openarcloud/cyclonedds-python-base:0.10.5-ubuntu22.04`

To rebuild/publish the base image:
```bash
docker build -f Dockerfile.base -t ghcr.io/openarcloud/cyclonedds-python-base:0.10.5-ubuntu22.04 .
docker push ghcr.io/openarcloud/cyclonedds-python-base:0.10.5-ubuntu22.04
```

## Running Tests

### Default: Comprehensive Test Suite
```bash
docker run --rm --network host cyclonedds-python
```

Runs `comprehensive_test.py` which includes basic DDS and SpatialDDS tests.

### SpatialDDS v1.7 Protocol Test
```bash
# Summary mode (no message content)
docker run --rm --network host \
  cyclonedds-python python3 spatialdds_test.py --summary-only

# Default mode (shows message content)
docker run --rm --network host \
  cyclonedds-python python3 spatialdds_test.py

# Detailed mode (includes full sensor data)
docker run --rm --network host \
  cyclonedds-python python3 spatialdds_test.py --detailed
```

### Validation Tests
```bash
docker run --rm cyclonedds-python python3 spatialdds_validation.py
```

### HTTP Binding Server
```bash
# Start server on port 8080
docker run --rm -p 8080:8080 cyclonedds-python python3 http_binding.py

# Custom port
docker run --rm -p 9000:9000 cyclonedds-python python3 http_binding.py 9000
```

### IDL Compilation

`/app/spatialdds.idl` is a convenience aggregator: it defines no types of its
own, it only `#include`s the profile files under `idl/v1.7/`. Nothing in the
demo consumes generated bindings — the wire type is the JSON envelope declared
in `spatialdds_demo/dds_transport.py` — so this is purely for downstream
consumers who want typed stubs.

**C** — the aggregator works, and `-o` is honoured:

```bash
docker run --rm -v $(pwd):/output cyclonedds-python \
  idlc -l c -o /output /app/spatialdds.idl
# -> spatialdds.c, spatialdds.h
```

**Python** — two gotchas, both verified against this image:

1. Point `idlc` at an **individual profile file, not the aggregator**. The
   Python backend generates from the declarations in the input file itself, and
   an include-only wrapper has none, so it exits 0 and writes nothing.
   (Reproducible with a one-line wrapper around any IDL — not specific to
   SpatialDDS.)
2. The Python backend **ignores `-o`**. It writes a package tree into the
   current working directory, so `cd` into the mounted directory first.

```bash
# One profile
docker run --rm -v $(pwd)/output:/output cyclonedds-python bash -lc \
  'cd /output && idlc -l py -I /app/idl/v1.7 /app/idl/v1.7/discovery.idl'
# -> output/spatial/disco/_discovery.py

# Every profile
docker run --rm -v $(pwd)/output:/output cyclonedds-python bash -lc \
  'cd /output && for f in /app/idl/v1.7/*.idl; do idlc -l py -I /app/idl/v1.7 "$f"; done'
```

**C++** — not available in this image. `idlc -l cpp` fails with
`Cannot load generator libcycloneddsidlcpp.so`; the C++ backend ships with
`cyclonedds-cxx`, which `Dockerfile.base` does not build. Add it there if you
need C++ stubs.

## Interactive Shell

### Enter Container Shell
```bash
docker run --rm -it --network host cyclonedds-python bash
```

Once inside:
```bash
# Run tests
python3 spatialdds_test.py

# Check DDS tools
ddsperf --help
idlc --help

# Compile IDL (C from the aggregator; Python from a profile file — see above)
idlc -l c spatialdds.idl
idlc -l py -I idl/v1.7 idl/v1.7/discovery.idl   # writes ./spatial/... (ignores -o)

# Check Python packages
pip3 list | grep cyclone
```

## Network Modes

### Host Network (Recommended for DDS)
```bash
docker run --rm --network host cyclonedds-python
```

**Why?** DDS uses UDP multicast for discovery, which works best with host networking.

### Bridge Network (Alternative)
```bash
docker run --rm -p 8080:8080 cyclonedds-python python3 http_binding.py
```

Use for HTTP services or when host networking isn't available.

## Volume Mounts

### Mount Current Directory
```bash
docker run --rm -v $(pwd):/data cyclonedds-python python3 /data/my_test.py
```

### Mount Output Directory
```bash
docker run --rm -v $(pwd)/output:/output cyclonedds-python bash -lc \
  'cd /output && idlc -l py -I /app/idl/v1.7 /app/idl/v1.7/discovery.idl'
```

## Docker Compose

### Start All Services
```bash
docker-compose up
```

### Run in Background
```bash
docker-compose up -d
```

### View Logs
```bash
docker-compose logs -f
```

### Stop Services
```bash
docker-compose down
```

## Troubleshooting

### Container Won't Start
```bash
# Check if port is already in use
lsof -i :7400

# Check container logs
docker logs <container_id>

# Try without host networking
docker run --rm cyclonedds-python
```

### Module Not Found
```bash
# Rebuild with no cache (bindings must install successfully)
docker build -t cyclonedds-python --no-cache .

# Verify files are copied
docker run --rm cyclonedds-python ls -la /app/
```

### DDS Discovery Issues
```bash
# Check network interface
docker run --rm --network host cyclonedds-python ip addr

# Test with explicit interface
docker run --rm --network host -e CYCLONEDDS_URI='<General><Interfaces><NetworkInterface address="eth0"/></Interfaces></General>' cyclonedds-python
```

## Performance Testing

### Throughput Test
```bash
# Publisher
docker run --rm --network host cyclonedds-python ddsperf pub size 1k &

# Subscriber
docker run --rm --network host cyclonedds-python ddsperf sub
```

### Latency Test
```bash
# Ping
docker run --rm --network host cyclonedds-python ddsperf ping &

# Pong
docker run --rm --network host cyclonedds-python ddsperf pong
```

### Sanity Check
```bash
docker run --rm --network host cyclonedds-python ddsperf sanity
```

## Cleanup

### Remove Container
```bash
docker rm <container_id>
```

### Remove Image
```bash
docker rmi cyclonedds-python
```

### Remove All Stopped Containers
```bash
docker container prune
```

### Full Cleanup
```bash
docker system prune -a
```

## Environment Variables

### Set Cyclone DDS Config
```bash
docker run --rm --network host \
  -e CYCLONEDDS_URI='<General><Interfaces><NetworkInterface autodetermine="true"/></Interfaces></General>' \
  cyclonedds-python
```

### Set Log Level
```bash
docker run --rm --network host \
  -e CYCLONEDDS_LOG_LEVEL=debug \
  cyclonedds-python
```

## Common Commands Summary

| Task | Command |
|------|---------|
| Build | `docker build -t cyclonedds-python .` |
| Run default test | `docker run --rm --network host cyclonedds-python` |
| Run v1.7 test | `docker run --rm --network host cyclonedds-python python3 spatialdds_test.py` |
| Validation test | `docker run --rm cyclonedds-python python3 spatialdds_validation.py` |
| HTTP server | `docker run --rm -p 8080:8080 cyclonedds-python python3 http_binding.py` |
| Interactive shell | `docker run --rm -it --network host cyclonedds-python bash` |
| Compile IDL (C) | `docker run --rm -v $(pwd):/out cyclonedds-python idlc -l c -o /out /app/spatialdds.idl` |
| Compile IDL (Python) | `docker run --rm -v $(pwd):/out cyclonedds-python bash -lc 'cd /out && idlc -l py -I /app/idl/v1.7 /app/idl/v1.7/discovery.idl'` |

## Notes

- **Always use `--network host`** for DDS communication tests
- **Use `-p` port mapping** for HTTP services
- **Mount volumes** with `-v` to access output files
- **Rebuild image** after updating Python or IDL files
- The container runs as non-root user `ddsuser` for security
