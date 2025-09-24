# Cyclone DDS Docker with SpatialDDS Implementation

A dockerized setup for Eclipse Cyclone DDS with SpatialDDS protocol implementation, including comprehensive test applications for both basic DDS functionality and SpatialDDS VPS (Visual Positioning Service) scenarios.

## Overview

This project provides:
- Docker container with Cyclone DDS built from source
- Complete SpatialDDS v1.2 protocol implementation
- VPS (Visual Positioning Service) mock implementation
- Comprehensive test suite with detailed message logging
- IDL definitions for SpatialDDS data structures
- Docker Compose configuration for distributed testing

## Prerequisites

- Docker (version 20.10 or later)
- Docker Compose (version 2.0 or later)

## Quick Start

### 1. Build the Docker Image

```bash
docker build -t cyclonedds-python .
```

### 2. Run Comprehensive Tests

Test both DDS and SpatialDDS functionality:

```bash
docker run --rm --network host cyclonedds-python
```

This runs the comprehensive test suite that validates:
- Cyclone DDS environment setup and basic functionality
- SpatialDDS IDL compilation
- VPS service announcement and discovery
- Client-server request/response flow with mock sensor data
- Detailed message logging and protocol visualization

### 3. Run Publisher/Subscriber Test

Using Docker Compose to run both publisher and subscriber:

```bash
# Start both services
docker-compose up

# Or run in detached mode
docker-compose up -d

# View logs
docker-compose logs -f
```

## Usage Options

### Built-in Tests

Run the comprehensive test suite (default):
```bash
docker run --rm --network host cyclonedds-python
```

Run specific test modes:
```bash
# Only basic DDS tests
docker run --rm --network host cyclonedds-python python3 comprehensive_test.py --mode basic

# Only SpatialDDS tests
docker run --rm --network host cyclonedds-python python3 comprehensive_test.py --mode spatial

# Only IDL compilation tests
docker run --rm --network host cyclonedds-python python3 comprehensive_test.py --mode idl

# Interactive SpatialDDS demo
docker run --rm --network host -it cyclonedds-python python3 comprehensive_test.py --mode demo
```

Run the SpatialDDS test directly:
```bash
# Default: Show message content (without large sensor data)
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py

# Show detailed content including full sensor data
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py --detailed

# Show only message headers (no content)
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py --summary-only
```

Run basic DDS diagnostic test:
```bash
docker run --rm --network host cyclonedds-python python3 simple_test.py
```

### DDS Performance Tools

Test throughput:
```bash
docker run --rm --network host cyclonedds-python ddsperf pub size 1k &
docker run --rm --network host cyclonedds-python ddsperf sub
```

Test latency:
```bash
docker run --rm --network host cyclonedds-python ddsperf ping &
docker run --rm --network host cyclonedds-python ddsperf pong
```

Run sanity check:
```bash
docker run --rm --network host cyclonedds-python ddsperf sanity
```

### IDL Compiler

Compile IDL files:
```bash
docker run --rm -v $(pwd):/data cyclonedds-python idlc /data/your_file.idl
```

### SpatialDDS Protocol Testing

Test VPS (Visual Positioning Service) workflow:
```bash
# Complete SpatialDDS VPS workflow test
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py
```

Compile SpatialDDS IDL definitions:
```bash
# Generate Python bindings from IDL
docker run --rm -v $(pwd):/output cyclonedds-python idlc -l py -o /output spatialdds.idl
```

The SpatialDDS test demonstrates:
1. **Service Discovery**: VPS announces capabilities, client discovers services
2. **Sensor Data Exchange**: Mock camera, IMU, and GPS data transmission
3. **Pose Estimation**: VPS processes sensor data and returns pose estimates
4. **Feature Extraction**: Visual feature points and descriptors
5. **Anchor Management**: Persistent world-anchored reference points
6. **Detailed Logging**: Complete message flow with JSON content visualization

#### Message Content Display Options:
- **Default Mode**: Shows all message content with large data fields truncated for readability
- **Detailed Mode**: Shows complete message content including full sensor data payloads
- **Summary Mode**: Shows only message headers, timing, and sizes without content
- **Custom Fields**: Key fields like request_id, success status, and confidence are always shown

## Command Line Options

The test application supports several command-line options:

```bash
python test_app.py --help
```

- `--mode`: Choose between `publisher`, `subscriber`, or `test` (default: test)
- `--duration`: Duration to run in seconds (default: 10)
- `--interval`: Publisher message interval in seconds (default: 1.0)

## Network Configuration

This setup uses `network_mode: host` for Docker containers to ensure proper DDS communication. This is required because:

- DDS uses UDP multicast for discovery
- Host networking allows containers to communicate using the host's network interface
- Proper IP addressing is maintained for DDS participants

**Note**: Host networking may not work on Docker Desktop for Windows/macOS. In these environments, you may need to adjust the network configuration.

## Project Structure

```
.
├── Dockerfile              # Multi-stage build for Cyclone DDS
├── docker-compose.yml      # Container orchestration
├── requirements.txt        # Python dependencies
├── test_app.py            # Test application
└── README.md              # This file
```

## Dockerfile Details

The Dockerfile uses a multi-stage build:

1. **Builder stage**: Compiles Cyclone DDS from source with required features
2. **Runtime stage**: Creates a lean production image with Python bindings

Key features:
- Based on Python 3.10 (3.11 has known installation issues)
- Cyclone DDS built with `ENABLE_TYPELIB=ON`
- Non-root user for security
- Proper environment variable setup

## Test Application Features

The `test_app.py` includes:

- **Publisher**: Sends test messages with timestamps and counters
- **Subscriber**: Listens for and displays received messages
- **Test Mode**: Validates DDS initialization and basic functionality
- **Error Handling**: Comprehensive error reporting and cleanup

## Troubleshooting

### Common Issues

1. **Import Error**: If you see `ImportError: cannot import name 'IdlStruct'`
   - This usually indicates cyclonedds installation issues
   - Rebuild the Docker image with `--no-cache` flag

2. **Network Issues**: If containers can't communicate
   - Ensure host networking is working on your platform
   - Check firewall settings for UDP ports 7400-7500

3. **Build Failures**: If Docker build fails
   - Check available disk space
   - Ensure internet connectivity for downloading dependencies

### Validation Commands

```bash
# Check if Cyclone DDS is properly installed
docker run --rm cyclonedds-python cyclonedds --version

# Check Python bindings
docker run --rm cyclonedds-python python -c "import cyclonedds; print('Success')"

# Run comprehensive test
docker run --rm --network host cyclonedds-python python test_app.py --mode test
```

### Debug Mode

Run with verbose output:

```bash
docker run --rm --network host -e CYCLONEDDS_URI='<General><NetworkInterfaceAddress>auto</NetworkInterfaceAddress></General>' cyclonedds-python
```

## Development

To modify the test application:

1. Edit `test_app.py` locally
2. Use volume mounts to test changes without rebuilding:

```bash
docker run --rm --network host -v $(pwd):/app cyclonedds-python python test_app.py
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Test your changes with the provided test suite
4. Submit a pull request

## License

This project follows the Eclipse Cyclone DDS licensing terms.

## References

- [SpatialDDS Specification](https://github.com/OpenArCloud/SpatialDDS-spec) - Official SpatialDDS protocol specification
- [Eclipse Cyclone DDS](https://github.com/eclipse-cyclonedds/cyclonedds)
- [Cyclone DDS Python Bindings](https://github.com/eclipse-cyclonedds/cyclonedds-python)
- [DDS Documentation](https://cyclonedds.io/docs/)