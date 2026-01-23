# Docker Quick Reference Guide

## Building the Image

### First Time Build
```bash
docker build -t cyclonedds-python .
```

This builds the image with:
- Cyclone DDS 0.10.5 from source
- Python bindings (cyclonedds==0.10.5)
- All SpatialDDS v1.4 files
- DDS performance tools

**Build time:** ~5-10 minutes (depends on your machine)

### Rebuild After Changes
```bash
docker build -t cyclonedds-python --no-cache .
```

Use `--no-cache` to force a complete rebuild if you've updated files.

## Running Tests

### Default: Comprehensive Test Suite
```bash
docker run --rm --network host cyclonedds-python
```

Runs `comprehensive_test.py` which includes basic DDS and SpatialDDS tests.

### SpatialDDS v1.4 Protocol Test
```bash
# Summary mode (no message content)
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py --summary-only

# Default mode (shows message content)
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py

# Detailed mode (includes full sensor data)
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py --detailed
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
```bash
# Compile to Python
docker run --rm -v $(pwd):/output cyclonedds-python idlc -l py -o /output /app/spatialdds.idl

# Compile to C
docker run --rm -v $(pwd):/output cyclonedds-python idlc -l c -o /output /app/spatialdds.idl

# Compile to C++
docker run --rm -v $(pwd):/output cyclonedds-python idlc -l cpp -o /output /app/spatialdds.idl
```

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

# Compile IDL
idlc -l py spatialdds.idl

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
docker run --rm -v $(pwd)/output:/output cyclonedds-python idlc -l py -o /output /app/spatialdds.idl
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
docker run --rm --network host -e CYCLONEDDS_URI='<General><NetworkInterfaceAddress>eth0</NetworkInterfaceAddress></General>' cyclonedds-python
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
  -e CYCLONEDDS_URI='<General><NetworkInterfaceAddress>auto</NetworkInterfaceAddress></General>' \
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
| Run v1.4 test | `docker run --rm --network host cyclonedds-python python3 spatialdds_test.py` |
| Validation test | `docker run --rm cyclonedds-python python3 spatialdds_validation.py` |
| HTTP server | `docker run --rm -p 8080:8080 cyclonedds-python python3 http_binding.py` |
| Interactive shell | `docker run --rm -it --network host cyclonedds-python bash` |
| Compile IDL | `docker run --rm -v $(pwd):/out cyclonedds-python idlc -l py -o /out /app/spatialdds.idl` |

## Notes

- **Always use `--network host`** for DDS communication tests
- **Use `-p` port mapping** for HTTP services
- **Mount volumes** with `-v` to access output files
- **Rebuild image** after updating Python or IDL files
- The container runs as non-root user `ddsuser` for security
