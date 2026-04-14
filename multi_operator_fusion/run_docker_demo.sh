#!/usr/bin/env bash
# Launch the multi-operator fusion demo in Docker (cyclonedds-python image).
# Starts a host-side Rerun web viewer, then runs multi_operator_fusion/run_demo.py
# inside the container which spawns 6 subprocesses sharing the DDS domain.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RERUN_PID_FILE="${SCRIPT_DIR}/.rerun_demo.pid"

# If the pruned subset from scripts/make_demo_subset.py (or download_demo_data.sh)
# is present under data/, default to it. Otherwise fall back to the full
# datasets used by nuscenes/ and deepsense/ demos.
_MOF_DATA="${SCRIPT_DIR}/data"
if [[ -d "${_MOF_DATA}/nuscenes_scene" ]]; then
  NUSCENES_DATAROOT="${NUSCENES_DATAROOT:-${_MOF_DATA}/nuscenes_scene}"
else
  NUSCENES_DATAROOT="${NUSCENES_DATAROOT:-${REPO_ROOT}/nuscenes/data/v1.0-mini}"
fi
if [[ -d "${_MOF_DATA}/deepsense_seq" ]]; then
  DEEPSENSE_DATAROOT="${DEEPSENSE_DATAROOT:-${_MOF_DATA}/deepsense_seq}"
else
  DEEPSENSE_DATAROOT="${DEEPSENSE_DATAROOT:-${REPO_ROOT}/nuscenes/scenario9_dev}"
fi
SCENE="${SCENE:-scene-0061}"
VERSION="${VERSION:-v1.0-mini}"
SEQUENCE="${SEQUENCE:-1}"
DOMAIN="${DOMAIN:-1}"
RATE_HZ="${RATE_HZ:-2.0}"
TICK_HZ="${TICK_HZ:-2.0}"
DEEPSENSE_SPEED="${DEEPSENSE_SPEED:-1.0}"
MAX_SAMPLES="${MAX_SAMPLES:-20}"
# AV publishers run at 2 Hz, infra at 10 Hz — match runtimes so the
# launcher's "wait for all publishers" logic tears down roughly together.
INFRA_MAX_SAMPLES="${INFRA_MAX_SAMPLES:-$(( MAX_SAMPLES > 0 ? MAX_SAMPLES * 5 : 0 ))}"
SKIP_INFRA="${SKIP_INFRA:-0}"
INFRA_OFFSET="${INFRA_OFFSET:--30 30 0}"

RERUN_GRPC_PORT="${RERUN_GRPC_PORT:-9876}"
RERUN_WEB_PORT="${RERUN_WEB_PORT:-9090}"
RERUN_WEB_URL="${RERUN_WEB_URL:-http://127.0.0.1:${RERUN_WEB_PORT}}"
RERUN_CONNECT_HOST="${RERUN_CONNECT_HOST:-host.docker.internal}"
RERUN_ADDR="${RERUN_ADDR:-rerun+http://${RERUN_CONNECT_HOST}:${RERUN_GRPC_PORT}/proxy}"
SPAWN_VIEWER="${SPAWN_VIEWER:-1}"
RERUN_OPEN_URL="${RERUN_WEB_URL}?url=rerun%2Bhttp%3A%2F%2Flocalhost%3A${RERUN_GRPC_PORT}%2Fproxy"

for path in "${NUSCENES_DATAROOT}" "${DEEPSENSE_DATAROOT}"; do
  if [[ ! -d "${path}" ]]; then
    echo "[multi_op] dataroot not found: ${path}" >&2
    exit 1
  fi
  if [[ "${path}" != "${REPO_ROOT}"* ]]; then
    echo "[multi_op] dataroot must be inside repo: ${path}" >&2
    exit 1
  fi
done

CONTAINER_NUSCENES="/app${NUSCENES_DATAROOT#${REPO_ROOT}}"
CONTAINER_DEEPSENSE="/app${DEEPSENSE_DATAROOT#${REPO_ROOT}}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[multi_op] docker is required" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "[multi_op] docker daemon not reachable" >&2
  exit 1
fi
if ! docker image inspect cyclonedds-python:latest >/dev/null 2>&1; then
  echo "[multi_op] building cyclonedds-python image" >&2
  (cd "${REPO_ROOT}" && docker build -t cyclonedds-python .)
fi

rerun_pid=""
cleanup() {
  if [[ -n "${rerun_pid}" ]] && kill -0 "${rerun_pid}" >/dev/null 2>&1; then
    kill "${rerun_pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${RERUN_PID_FILE}"
}
trap cleanup EXIT INT TERM

if [[ "${SPAWN_VIEWER}" == "1" ]]; then
  if ! command -v rerun >/dev/null 2>&1; then
    echo "[multi_op] rerun CLI not found on host. Install rerun or set SPAWN_VIEWER=0." >&2
    exit 1
  fi
  if command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"${RERUN_GRPC_PORT}" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "[multi_op] rerun gRPC port ${RERUN_GRPC_PORT} busy. Run stop_docker_demo.sh." >&2
      exit 1
    fi
    if lsof -iTCP:"${RERUN_WEB_PORT}" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "[multi_op] rerun web port ${RERUN_WEB_PORT} busy. Run stop_docker_demo.sh." >&2
      exit 1
    fi
  fi
  if [[ -f "${RERUN_PID_FILE}" ]]; then
    old_pid="$(cat "${RERUN_PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" >/dev/null 2>&1; then
      echo "[multi_op] demo already running (pid ${old_pid}). Run stop_docker_demo.sh." >&2
      exit 1
    fi
    rm -f "${RERUN_PID_FILE}"
  fi
  echo "[multi_op] starting rerun web viewer at ${RERUN_WEB_URL}" >&2
  rerun --serve-web --web-viewer --port "${RERUN_GRPC_PORT}" --web-viewer-port "${RERUN_WEB_PORT}" >/tmp/multi_op_rerun.log 2>&1 &
  rerun_pid=$!
  echo "${rerun_pid}" > "${RERUN_PID_FILE}"
  sleep 1
  if ! kill -0 "${rerun_pid}" >/dev/null 2>&1; then
    echo "[multi_op] rerun failed to start. See /tmp/multi_op_rerun.log" >&2
    exit 1
  fi
  echo "[multi_op] open ${RERUN_OPEN_URL}" >&2
  echo "[multi_op] container will connect to ${RERUN_ADDR}" >&2
fi

RERUN_CONNECT_ARGS=""
# Pass connect args when either (a) we spawned the viewer or (b) USE_EXTERNAL_VIEWER=1
# tells us the user is running rerun out-of-band (e.g. to keep the viewer alive
# after the demo run finishes).
if [[ -n "${RERUN_ADDR}" && ( "${SPAWN_VIEWER}" == "1" || "${USE_EXTERNAL_VIEWER:-0}" == "1" ) ]]; then
  RERUN_CONNECT_ARGS="--rerun-connect ${RERUN_ADDR}"
fi
SKIP_INFRA_ARGS=""
if [[ "${SKIP_INFRA}" == "1" ]]; then
  SKIP_INFRA_ARGS="--skip-infra"
fi

echo "[multi_op] starting docker demo (6 processes)" >&2

docker run --rm --network host \
  -v "${REPO_ROOT}:/app" \
  -w /app \
  -e PYTHONUNBUFFERED=1 \
  -e PYTHONPATH=/app \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN="${DOMAIN}" \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python bash -lc "
    python3 -m pip install -r /app/multi_operator_fusion/requirements.txt >/tmp/multi_op_pip.log 2>&1 &&
    python3 /app/multi_operator_fusion/run_demo.py \
      --nuscenes-dataroot ${CONTAINER_NUSCENES} \
      --nuscenes-scene ${SCENE} \
      --nuscenes-version ${VERSION} \
      --deepsense-dataroot ${CONTAINER_DEEPSENSE} \
      --deepsense-sequence ${SEQUENCE} \
      --deepsense-speed ${DEEPSENSE_SPEED} \
      --domain ${DOMAIN} \
      --rate-hz ${RATE_HZ} \
      --tick-hz ${TICK_HZ} \
      --max-samples ${MAX_SAMPLES} \
      --infra-max-samples ${INFRA_MAX_SAMPLES} \
      --infra-offset ${INFRA_OFFSET} \
      ${SKIP_INFRA_ARGS} \
      ${RERUN_CONNECT_ARGS}
  "
