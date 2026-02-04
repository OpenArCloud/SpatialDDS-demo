# Bridge HTTP Tests

This runs an end-to-end flow against the DDS-backed bridge:
- DDS CoverageQuery to discover VPS
- HTTP localize via bridge
- HTTP catalog query via bridge

## Run
```
export SPATIALDDS_TRANSPORT=dds
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
export SPATIALDDS_DDS_DOMAIN=1

python bridge/tests/run_bridge_http_tests.py
```

## Run in Docker (Recommended)
```
./run_bridge_http_tests_with_logs.sh
```

## Notes
- Starts `spatialdds_demo_server.py`, `spatialdds_catalog_server.py`, and `bridge/server.py`.
- Uses `bridge/tests/catalog_seed_austin.json` to return Austin-specific mock content.
