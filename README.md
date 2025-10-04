# SpatialDDS v1.3 Demo Implementation

A complete reference implementation of the SpatialDDS v1.3 specification using Eclipse Cyclone DDS, demonstrating VPS (Visual Positioning Service) discovery, localization, and anchor management.

## Overview

This project provides:
- **Complete SpatialDDS v1.3 protocol implementation**
- VPS mock service with GeoPose-based localization
- HTTP REST API binding for content discovery
- Comprehensive validation utilities
- Full message logging and visualization
- Docker containerization for easy deployment

## Protocol Flow

### DDS-based Communication

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant DDS as DDS Network
    participant VPS as VPS Service

    %% Phase 1 — Service announcement
    Note over VPS,DDS: Phase 1 — ContentAnnounce (v1.3)
    VPS->>DDS: CONTENT_ANNOUNCE<br/>self_uri, rtype:"service"<br/>**bounds** (bbox EPSG:4979)<br/>endpoint{protocol:"dds", topic:"SpatialDDS/VPS/Request"}<br/>mime, title, summary, tags, class_id, manifest_uri<br/>stamp/timestamp, ttl_sec, _legacy_id

    %% Phase 2 — Discovery query → result (wrapper with announces[])
    Note over Client,DDS: Phase 2 — ContentQuery (v1.3)
    Client->>DDS: CONTENT_QUERY<br/>query_id, query_uri<br/>rtype:"service"<br/>**volume** (bbox EPSG:4979)<br/>tags, class_id, filter{min_accuracy, real_time, supported_formats}<br/>stamp/timestamp
    Note over DDS,VPS: Matching by INTERSECTS(volume, bounds)
    VPS-->>DDS: CONTENT_QUERY_RESULT (to requester)<br/>query_id, **announces[]** (slim ContentAnnounce objects)<br/>stamp/timestamp, count
    DDS-->>Client: CONTENT_QUERY_RESULT<br/>announces[0] → self_uri, rtype, **bounds**, endpoint, mime,<br/>(title/summary/tags/class_id/manifest_uri), stamp/timestamp

    %% Phase 3 — Service request (VPS API)
    Note over Client,VPS: Phase 3 — VPS_REQUEST
    Client->>VPS: VPS_REQUEST<br/>request_id, request_uri<br/>client_uri, _legacy_client_id<br/>approximate_location (LLA)<br/>image_data (JPEG), camera_intrinsics<br/>IMU, GNSS, desired_accuracy, requested_data_types<br/>stamp/timestamp

    %% Phase 4 — Processing
    Note over VPS: Phase 4 — feature extraction → map match → pose estimate

    %% Phase 5 — Service response with GeoPose
    Note over VPS,Client: Phase 5 — VPS_RESPONSE
    VPS-->>Client: VPS_RESPONSE<br/>request_uri, service_uri, _legacy_service_id<br/>success, **estimated_geopose {lat, lon, h, q_wxyz}**<br/>confidence, accuracy_estimate<br/>(optional) feature_points[], descriptor_data<br/>stamp/timestamp, error_code/message

    %% Phase 6 — Anchor update to DDS
    Note over Client,DDS: Phase 6 — ANCHOR_UPDATE
    Client->>DDS: ANCHOR_UPDATE<br/>self_uri (anchor), rtype:"anchor", anchor_type<br/>**geopose {lat, lon, h, q_wxyz}**<br/>metadata{created_by_uri, source, feature_count, _legacy_created_by}<br/>persistence_score<br/>created/last_seen timestamps + ISO stamps
```

### HTTP-based Communication

```mermaid
sequenceDiagram
    participant Client
    participant Resolver as HTTP Resolver
    participant VPS as VPS Service

    Note over VPS,Resolver: Providers may pre-register or Resolver proxies DDS announces
    Client->>Resolver: POST /.well-known/spatialdds/search<br/>Body: ContentQuery { rtype, volume bbox EPSG:4979, tags, class_id, filter, stamp }
    Resolver-->>Client: 200 OK<br/>Body: [ ContentAnnounce ... ]  // slim objects: self_uri, rtype, bounds, endpoint, mime, etc.

    Note over Client,VPS: Use endpoint from announce
    Client->>VPS: VPS_REQUEST (as in DDS demo)<br/>request_uri, client_uri, sensor payloads, desired_accuracy
    VPS-->>Client: VPS_RESPONSE<br/>estimated_geopose {lat, lon, h, q_wxyz}, confidence, accuracy_estimate, stamp
    Client->>Resolver: optional POST anchor (out of scope) or
    Client->>DDS: ANCHOR_UPDATE (as in DDS demo)
```

## Quick Start

### Build and Run

```bash
# Build the Docker image
docker build -t cyclonedds-python .

# Run the SpatialDDS v1.3 demo
docker run --rm --network host cyclonedds-python
```

### Run SpatialDDS Tests

```bash
# Default: Show full message content
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py

# Summary only (no message content)
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py --summary-only

# Detailed mode (includes full sensor data)
docker run --rm --network host cyclonedds-python python3 spatialdds_test.py --detailed
```

### HTTP Binding Server

```bash
# Start HTTP REST API server
docker run --rm -p 8080:8080 cyclonedds-python python3 http_binding.py

# Test the endpoints
curl http://localhost:8080/

# Search for content
curl -X POST http://localhost:8080/.well-known/spatialdds/search \
  -H "Content-Type: application/json" \
  -d '{
    "rtype": "service",
    "volume": {
      "type": "bbox",
      "frame": "earth-fixed",
      "crs": "EPSG:4979",
      "bbox": [-122.45, 37.75, -122.35, 37.85]
    }
  }'
```

## v1.3 Specification Compliance

This implementation is **fully compliant** with SpatialDDS v1.3:

- ✅ URI-based identification with `spatialdds://` scheme
- ✅ Earth-fixed bbox as 2D `[west, south, east, north]`
- ✅ GeoPose format `{lat, lon, h, q_wxyz}` for earth-fixed frames
- ✅ Single canonical quaternion format `q_wxyz` [w,x,y,z]
- ✅ Slim announce payloads with `bounds` (single CoverageElement)
- ✅ Query `volume` as single CoverageElement
- ✅ ISO8601 timestamps alongside epoch milliseconds
- ✅ Omitted redundant fields (pose_frame, empty arrays)

See [SPEC_COMPLIANCE.md](SPEC_COMPLIANCE.md) for detailed before/after examples and all 11 refinements.

## Project Structure

```
.
├── Dockerfile                  # Cyclone DDS + Python environment
├── docker-compose.yml          # Container orchestration
├── spatialdds.idl             # v1.3 IDL definitions
├── spatialdds_test.py         # v1.3 protocol demo
├── spatialdds_validation.py   # Validation utilities
├── http_binding.py            # HTTP REST API server
├── comprehensive_test.py      # Full test suite
├── SPEC_COMPLIANCE.md         # Compliance documentation
├── DOCKER_GUIDE.md            # Docker reference
└── README.md                  # This file
```

## Development

Test changes without rebuilding:

```bash
# Test SpatialDDS protocol
docker run --rm --network host -v $(pwd):/app cyclonedds-python python3 spatialdds_test.py

# Test HTTP binding
docker run --rm -p 8080:8080 -v $(pwd):/app cyclonedds-python python3 http_binding.py

# Run validation tests
docker run --rm -v $(pwd):/app cyclonedds-python python3 spatialdds_validation.py
```

## References

- [SpatialDDS Specification v1.3](https://github.com/OpenArCloud/SpatialDDS-spec/blob/main/SpatialDDS-1.3-full.md)
- [SpatialDDS v1.3 IDL](https://github.com/OpenArCloud/SpatialDDS-spec/tree/main/idl/v1.3)
- [Eclipse Cyclone DDS](https://github.com/eclipse-cyclonedds/cyclonedds)

## License

See the [LICENSE](LICENSE) file for details.
