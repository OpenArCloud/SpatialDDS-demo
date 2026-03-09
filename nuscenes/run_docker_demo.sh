#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RERUN_PID_FILE="${SCRIPT_DIR}/.rerun_demo.pid"

DATAROOT="${DATAROOT:-${REPO_ROOT}/nuscenes/data/v1.0-mini}"
SCENE="${SCENE:-scene-0061}"
VERSION="${VERSION:-v1.0-mini}"
DOMAIN="${DOMAIN:-1}"
RATE_HZ="${RATE_HZ:-2.0}"
MAX_SAMPLES="${MAX_SAMPLES:-0}"
RERUN_GRPC_PORT="${RERUN_GRPC_PORT:-9876}"
RERUN_WEB_PORT="${RERUN_WEB_PORT:-9090}"
# Host-side viewer URL
RERUN_WEB_URL="${RERUN_WEB_URL:-http://127.0.0.1:${RERUN_WEB_PORT}}"
# Container-side gRPC endpoint used by subscriber to reach host rerun server.
RERUN_CONNECT_HOST="${RERUN_CONNECT_HOST:-host.docker.internal}"
RERUN_ADDR="${RERUN_ADDR:-rerun+http://${RERUN_CONNECT_HOST}:${RERUN_GRPC_PORT}/proxy}"
SPAWN_VIEWER="${SPAWN_VIEWER:-1}"
RERUN_OPEN_URL="${RERUN_WEB_URL}?url=rerun%2Bhttp%3A%2F%2Flocalhost%3A${RERUN_GRPC_PORT}%2Fproxy"

if [[ ! -d "${DATAROOT}" ]]; then
  echo "[nuscenes] dataroot not found: ${DATAROOT}" >&2
  exit 1
fi

if [[ "${DATAROOT}" == "${REPO_ROOT}"* ]]; then
  CONTAINER_DATAROOT="/app${DATAROOT#${REPO_ROOT}}"
else
  echo "[nuscenes] DATAROOT must be inside repo for this launcher: ${DATAROOT}" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[nuscenes] docker is required" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "[nuscenes] docker daemon not reachable" >&2
  exit 1
fi

if ! docker image inspect cyclonedds-python:latest >/dev/null 2>&1; then
  echo "[nuscenes] building cyclonedds-python image" >&2
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
    echo "[nuscenes] rerun CLI not found on host. Install rerun and retry, or run with SPAWN_VIEWER=0." >&2
    exit 1
  fi
  if command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"${RERUN_GRPC_PORT}" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "[nuscenes] rerun gRPC port ${RERUN_GRPC_PORT} already in use. Stop existing process or set RERUN_GRPC_PORT." >&2
      exit 1
    fi
    if lsof -iTCP:"${RERUN_WEB_PORT}" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      echo "[nuscenes] rerun web port ${RERUN_WEB_PORT} already in use. Stop existing process or set RERUN_WEB_PORT." >&2
      exit 1
    fi
  fi
  echo "[nuscenes] starting rerun web viewer at ${RERUN_WEB_URL}" >&2
  if [[ -f "${RERUN_PID_FILE}" ]]; then
    old_pid="$(cat "${RERUN_PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" >/dev/null 2>&1; then
      echo "[nuscenes] rerun demo already running (pid ${old_pid}). Run: bash nuscenes/stop_docker_demo.sh" >&2
      exit 1
    fi
    rm -f "${RERUN_PID_FILE}"
  fi
  rerun --serve-web --web-viewer --port "${RERUN_GRPC_PORT}" --web-viewer-port "${RERUN_WEB_PORT}" >/tmp/nuscenes_rerun.log 2>&1 &
  rerun_pid=$!
  echo "${rerun_pid}" > "${RERUN_PID_FILE}"
  sleep 1
  if ! kill -0 "${rerun_pid}" >/dev/null 2>&1; then
    echo "[nuscenes] rerun failed to start. See /tmp/nuscenes_rerun.log" >&2
    exit 1
  fi
  echo "[nuscenes] open ${RERUN_OPEN_URL}" >&2
  echo "[nuscenes] container will connect to ${RERUN_ADDR}" >&2
fi

echo "[nuscenes] starting docker demo" >&2

RERUN_CONNECT_ARGS=""
if [[ "${SPAWN_VIEWER}" == "1" && -n "${RERUN_ADDR}" ]]; then
  RERUN_CONNECT_ARGS="--rerun-connect ${RERUN_ADDR}"
fi

docker run --rm --network host \
  -v "${REPO_ROOT}:/app" \
  -w /app \
  -e PYTHONUNBUFFERED=1 \
  -e PYTHONPATH=/app \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN="${DOMAIN}" \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python bash -lc "
    python3 -m pip install -r /app/nuscenes/requirements.txt >/tmp/nuscenes_pip.log 2>&1 &&
    python3 /app/nuscenes/run_demo.py \
      --dataroot ${CONTAINER_DATAROOT} \
      --scene ${SCENE} \
      --version ${VERSION} \
      --domain ${DOMAIN} \
      --rate-hz ${RATE_HZ} \
      --max-samples ${MAX_SAMPLES} \
      ${RERUN_CONNECT_ARGS}
  "
