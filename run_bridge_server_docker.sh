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

# SPATIALDDS_DDS_DOMAIN and SPATIALDDS_DDS_CONFIG — so this stack can join a
# bus somebody else already owns.
#
# The domain was a fixed 1, which is right on a laptop and wrong beside
# another deployment: OpenVPS's AWS variant serves on domain 0 with multicast
# off and unicast peer discovery, so a demo hardcoded to domain 1 with
# multicast on shares a host with it and sees nothing. Not an error either ---
# Cyclone reports an absence of peers, so the symptom is an empty venue and a
# localization that never answers.
#
# `SPATIALDDS_DDS_CONFIG` names a file to mount *at* /etc/cyclonedds.xml
# rather than pointing CYCLONEDDS_URI elsewhere: a single config path shared by
# every process in the container. (Until the SDK adoption that was also forced,
# by the demo's `require_dds_env`, which refused any other URI and gave no
# reason. The SDK's participant checks only that a domain was chosen.) See
# deploy/aws/cyclonedds-colocated.xml.

# SPATIALDDS_MOCK_VPS — the demo's own mock VPS, on by default because it is
# what makes the AR demo self-contained. Set it to 0 to run against a real
# localization service instead: selection is by discovery, so the demo needs
# no other change — the page asks
# `/.well-known/spatialdds/search?kind=VPS` and takes what answers. The flag
# exists because "the mock never started" is a load-bearing condition rather
# than a tidy one: with two VPS services announced, `bridgeFindService` takes
# the first by service_id order, so which one serves the demo would be decided
# by a sort. See docs/vps-selection-finding.md.

# SPATIALDDS_ROBOT_SIM — the kinematic robot, for looking at the demo without
# the nav2 tier. Off when the tier is running: it owns the same key, and two
# writers would put one robot in two places. The bridge refuses rather than
# joins, but not starting it is better than being refused.
#
# This note lives here, above the invocation, and must stay here. It sat
# inside the `docker run` line-continuation below from 2026-09-07 to
# 2026-09-30, where a comment ends the command: docker received the flags
# above it, never the image, and this script could not start the demo at all
# for three weeks. Nothing caught it because the container it launches was
# already running from the day before, so every test reused that one.
echo "Starting bridge + VPS + catalog in Docker"
echo "Container name: ${bridge_name}"
echo "Service logs will be written under bridges/web_bridge/logs/"

docker run --rm -p 8088:8088 -p 8089:8089 --name "${bridge_name}" \
  -w /app \
  -e PYTHONUNBUFFERED=1 \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN="${SPATIALDDS_DDS_DOMAIN:-1}" \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  -e PYTHONPATH=/app \
  -e BRIDGE_LOG_BTS="${bts}" \
  -e BRIDGE_LOG_DIR="/app/bridges/web_bridge/logs" \
  -e SPATIALDDS_VPS_COVERAGE_BBOX="${SPATIALDDS_VPS_COVERAGE_BBOX:--97.75,30.27,-97.72,30.29}" \
  -e SPATIALDDS_VPS_MAP_FQN="${SPATIALDDS_VPS_MAP_FQN:-map/austin}" \
  -e SPATIALDDS_VPS_MAP_ID="${SPATIALDDS_VPS_MAP_ID:-austin-map}" \
  -e SPATIALDDS_ROBOT_SIM="${SPATIALDDS_ROBOT_SIM:-0}" \
  -e SPATIALDDS_POND_WATCH="${SPATIALDDS_POND_WATCH:-0}" \
  -e SPATIALDDS_DUCK_MOVER="${SPATIALDDS_DUCK_MOVER:-1}" \
  -e SPATIALDDS_MOCK_VPS="${SPATIALDDS_MOCK_VPS:-1}" \
  -e SPATIALDDS_VPS_SERVICE_ID="${SPATIALDDS_VPS_SERVICE_ID:-svc:vps:demo/austin-downtown}" \
  -e SPATIALDDS_VPS_SERVICE_NAME="${SPATIALDDS_VPS_SERVICE_NAME:-MockVPS-Austin}" \
  -e SPATIALDDS_DEMO_MANIFEST_URI="spatialdds://vps.example.com/austin-downtown/service/vps" \
  -e SPATIALDDS_CATALOG_SEED="${SPATIALDDS_CATALOG_SEED:-/app/bridges/web_bridge/tests/catalog_seed_fountain.json}" \
  -e SPATIALDDS_FRAME_ANCHORS="${SPATIALDDS_FRAME_ANCHORS:-/app/bridges/web_bridge/tests/frame_anchors_fountain.json}" \
  -e SPATIALDDS_MODEL_LAYER="${SPATIALDDS_MODEL_LAYER:-0}" \
  -e SPATIALDDS_BINDING_TOKEN="${SPATIALDDS_BINDING_TOKEN:-bridge-session}" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "${PWD}:/app" \
  ${SPATIALDDS_DDS_CONFIG:+-v "${SPATIALDDS_DDS_CONFIG}:/etc/cyclonedds.xml:ro"} \
  cyclonedds-python bash -lc "\
    set -e; \
    mkdir -p \"\$BRIDGE_LOG_DIR\"; \
    vps_log=\"\$BRIDGE_LOG_DIR/vps_server_\$BRIDGE_LOG_BTS.log\"; \
    catalog_log=\"\$BRIDGE_LOG_DIR/catalog_server_\$BRIDGE_LOG_BTS.log\"; \
    model_log=\"\$BRIDGE_LOG_DIR/model_server_\$BRIDGE_LOG_BTS.log\"; \
    binding_log=\"\$BRIDGE_LOG_DIR/binding_\$BRIDGE_LOG_BTS.log\"; \
    bridge_log=\"\$BRIDGE_LOG_DIR/bridge_server_\$BRIDGE_LOG_BTS.log\"; \
    python3 -m pip install -U pip certifi; \
    python3 -m pip install -r /app/requirements.txt -r /app/bridges/web_bridge/requirements.txt; \
    if [ \"\$SPATIALDDS_MOCK_VPS\" = \"1\" ]; then \
      python3 ar_demo/spatialdds_demo_server.py --detailed >\"\$vps_log\" 2>&1 &\
    fi; \
    python3 ar_demo/spatialdds_catalog_server.py --detailed >\"\$catalog_log\" 2>&1 &\
    if [ \"\$SPATIALDDS_MODEL_LAYER\" = \"1\" ]; then \
      python3 -m spatialdds_demo.model_service --quiet >\"\$model_log\" 2>&1 &\
      python3 scripts/gnome_publisher.py >\"\$BRIDGE_LOG_DIR/gnome_\$BRIDGE_LOG_BTS.log\" 2>&1 &\
      if [ \"\$SPATIALDDS_ROBOT_SIM\" = \"1\" ]; then \
        python3 -m spatialdds_demo.robot_bridge --source kinematic >\"\$BRIDGE_LOG_DIR/robot_\$BRIDGE_LOG_BTS.log\" 2>&1 &\
      fi; \
      if [ \"\$SPATIALDDS_POND_WATCH\" = \"1\" ]; then \
        python3 -m spatialdds_demo.pond_watch >\"\$BRIDGE_LOG_DIR/pondwatch_\$BRIDGE_LOG_BTS.log\" 2>&1 &\
      fi; \
      if [ \"\$SPATIALDDS_DUCK_MOVER\" = \"1\" ]; then \
        python3 -m spatialdds_demo.duck_mover >\"\$BRIDGE_LOG_DIR/mover_\$BRIDGE_LOG_BTS.log\" 2>&1 &\
      fi; \
      python3 -m bridges.web_bridge.binding --host 0.0.0.0 --port 8089 \
        --domain \"\$SPATIALDDS_DDS_DOMAIN\" --token \"\$SPATIALDDS_BINDING_TOKEN\" \
        >\"\$binding_log\" 2>&1 &\
    fi; \
    python3 bridges/web_bridge/server.py >\"\$bridge_log\" 2>&1\
  " >/dev/null 2>&1 &

docker_pid=$!
wait "${docker_pid}"
