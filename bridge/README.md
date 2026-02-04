# SpatialDDS Web Bridge

This service bridges the SpatialDDS DDS envelope to HTTP for the `web/` demo.

## Requirements
- DDS environment configured (Cyclone DDS)
- Python 3.10+

## Install
```
pip install -r bridge/requirements.txt
```

## Run
```
export SPATIALDDS_TRANSPORT=dds
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
export SPATIALDDS_DDS_DOMAIN=1

python bridge/server.py
```

Server default: http://localhost:8088

## Run in Docker (Recommended)
```
./run_bridge_server_docker.sh
```

Logs are written to `bridge/logs/`:
- `bridge/logs/vps_server_<timestamp>.log`
- `bridge/logs/catalog_server_<timestamp>.log`
- `bridge/logs/bridge_server_<timestamp>.log`

## Stop Docker Bridge
```
./stop_bridge_server_docker.sh
```

## Endpoints
- `GET /health` -> bridge status + last announce
- `POST /v1/localize` -> SpatialDDS LOCALIZE_RESPONSE
- `POST /v1/catalog/query` -> SpatialDDS CATALOG_RESPONSE

## Notes
- `POST /v1/localize` accepts `prior_geopose` and optional `service_id`.
- `POST /v1/catalog/query` accepts a `geopose` and optional `expr`, `limit`.
