#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but not installed or not on PATH."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running or not accessible."
  exit 1
fi

if ! docker image inspect cyclonedds-python:latest >/dev/null 2>&1; then
  echo "Docker image cyclonedds-python:latest not found. Building from Dockerfile..."
  docker build -t cyclonedds-python .
fi

bts="$(date +%Y%m%d_%H%M%S)"
bridge_name="dds_bridge_${bts}"
docker_pid=""

cleanup() {
  if [[ -n "${docker_pid}" ]]; then
    kill "${docker_pid}" >/dev/null 2>&1 || true
  fi
  docker stop "${bridge_name}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "Starting bridge + VPS + catalog in Docker"
echo "Container name: ${bridge_name}"
echo "Service logs will be written under bridge/logs/"

docker run --rm -p 8088:8088 --name "${bridge_name}" \
  -w /app \
  -e PYTHONUNBUFFERED=1 \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=1 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  -e PYTHONPATH=/app \
  -e BRIDGE_LOG_BTS="${bts}" \
  -e BRIDGE_LOG_DIR="/app/bridge/logs" \
  -e SPATIALDDS_VPS_COVERAGE_BBOX="-97.75,30.27,-97.72,30.29" \
  -e SPATIALDDS_VPS_MAP_FQN="map/austin" \
  -e SPATIALDDS_VPS_MAP_ID="austin-map" \
  -e SPATIALDDS_VPS_SERVICE_ID="svc:vps:demo/austin-downtown" \
  -e SPATIALDDS_VPS_SERVICE_NAME="MockVPS-Austin" \
  -e SPATIALDDS_DEMO_MANIFEST_URI="spatialdds://vps.example.com/zone:austin-downtown/manifest:vps" \
  -e SPATIALDDS_CATALOG_SEED="/app/bridge/tests/catalog_seed_austin.json" \
  -v "${PWD}:/app" \
  cyclonedds-python bash -lc "\
    set -e; \
    mkdir -p \"\$BRIDGE_LOG_DIR\"; \
    vps_log=\"\$BRIDGE_LOG_DIR/vps_server_\$BRIDGE_LOG_BTS.log\"; \
    catalog_log=\"\$BRIDGE_LOG_DIR/catalog_server_\$BRIDGE_LOG_BTS.log\"; \
    bridge_log=\"\$BRIDGE_LOG_DIR/bridge_server_\$BRIDGE_LOG_BTS.log\"; \
    python3 -m pip install -r /app/requirements.txt -r /app/bridge/requirements.txt; \
    python3 spatialdds_demo_server.py --detailed >\"\$vps_log\" 2>&1 &\
    python3 spatialdds_catalog_server.py --detailed >\"\$catalog_log\" 2>&1 &\
    python3 bridge/server.py >\"\$bridge_log\" 2>&1\
  " >/dev/null 2>&1 &

docker_pid=$!
wait "${docker_pid}"
