#!/usr/bin/env bash
set -u

server_name=""
catalog_name=""
server_pid=""
catalog_pid=""
bootstrap_name=""
bootstrap_pid=""

summarize_messages() {
  local label="$1"
  local log_path="$2"
  local pattern="\\[[[:space:]]*[0-9.]+s\\][[:space:]]+[←→][[:space:]]+(BOOTSTRAP_QUERY|BOOTSTRAP_RESPONSE|ANNOUNCE|COVERAGE_QUERY|COVERAGE_RESPONSE|LOCALIZE_REQUEST|LOCALIZE_RESPONSE|CATALOG_QUERY|CATALOG_RESPONSE|ANCHOR_DELTA)\\b"

  echo "High-level messages (${label}):"
  if command -v rg >/dev/null 2>&1; then
    rg -n "${pattern}" "${log_path}" || true
  else
    grep -En "${pattern}" "${log_path}" || true
  fi
}

cleanup() {
  if [[ -n "${bootstrap_name}" || -n "${server_name}" || -n "${catalog_name}" ]]; then
    echo "Cleaning up DDS containers..."
  fi
  if [[ -n "${bootstrap_name}" ]]; then
    docker stop "${bootstrap_name}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${server_name}" ]]; then
    docker stop "${server_name}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${catalog_name}" ]]; then
    docker stop "${catalog_name}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${bootstrap_pid}" || -n "${server_pid}" || -n "${catalog_pid}" ]]; then
    wait "${bootstrap_pid}" "${server_pid}" "${catalog_pid}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but not installed or not on PATH."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running or not accessible."
  exit 1
fi

cleanup_stale() {
  local containers
  containers="$(docker ps -q --filter "name=dds_server_" --filter "name=dds_catalog_" --filter "name=dds_bootstrap_")"
  if [[ -n "${containers}" ]]; then
    echo "Stopping lingering DDS containers..."
    docker rm -f ${containers} >/dev/null 2>&1 || true
  fi
}

cleanup_stale

if ! docker image inspect cyclonedds-python:latest >/dev/null 2>&1; then
  echo "Docker image cyclonedds-python:latest not found. Building from Dockerfile..."
  docker build -t cyclonedds-python .
fi

bts="$(date +%Y%m%d_%H%M%S)"
mock_bootstrap_log="mock_bootstrap_${bts}.log"
bootstrap_log="dds_bootstrap_${bts}.log"
dds_bootstrap_server_log="vps_server_${bts}.log"
dds_bootstrap_catalog_log="dds_catalog_${bts}.log"
dds_bootstrap_client_log="dds_client_${bts}.log"

echo "Running mock test with bootstrap -> ${mock_bootstrap_log}"
SPATIALDDS_TRANSPORT=mock python3 spatialdds_test.py --detailed >"${mock_bootstrap_log}" 2>&1
mock_bootstrap_status=$?

echo "Running DDS demo with bootstrap -> ${bootstrap_log}, ${dds_bootstrap_server_log}, ${dds_bootstrap_catalog_log}, ${dds_bootstrap_client_log}"
bootstrap_name="dds_bootstrap_${bts}"
docker run --rm --network host --name "${bootstrap_name}" \
  -e PYTHONUNBUFFERED=1 \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=0 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python python3 spatialdds_bootstrap_server.py --domain 1 --detailed >"${bootstrap_log}" 2>&1 &
bootstrap_pid=$!

server_name="dds_server_${bts}"
docker run --rm --network host --name "${server_name}" \
  -e PYTHONUNBUFFERED=1 \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=1 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python python3 spatialdds_vps_server.py --detailed >"${dds_bootstrap_server_log}" 2>&1 &
server_pid=$!

catalog_name="dds_catalog_${bts}"
docker run --rm --network host --name "${catalog_name}" \
  -e PYTHONUNBUFFERED=1 \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=1 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python python3 spatialdds_catalog_server.py --detailed >"${dds_bootstrap_catalog_log}" 2>&1 &
catalog_pid=$!

sleep 2

docker run --rm --network host \
  -e PYTHONUNBUFFERED=1 \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=0 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python python3 spatialdds_demo_client.py --detailed >"${dds_bootstrap_client_log}" 2>&1
bootstrap_client_status=$?

docker stop "${bootstrap_name}" "${server_name}" "${catalog_name}" >/dev/null 2>&1 || true
wait "${bootstrap_pid}" "${server_pid}" "${catalog_pid}" 2>/dev/null || true

echo "Bootstrap summary:"
if [[ "${mock_bootstrap_status}" -eq 0 ]]; then
  echo "- Mock bootstrap: PASS (${mock_bootstrap_log})"
else
  echo "- Mock bootstrap: FAIL (${mock_bootstrap_log})"
fi
if [[ "${bootstrap_client_status}" -eq 0 ]]; then
  echo "- DDS bootstrap: PASS (${dds_bootstrap_client_log})"
else
  echo "- DDS bootstrap: FAIL (${dds_bootstrap_client_log})"
fi
echo "- Bootstrap log: ${bootstrap_log}"
echo "- VPS server log: ${dds_bootstrap_server_log}"
echo "- Catalog log: ${dds_bootstrap_catalog_log}"

summarize_messages "mock_bootstrap" "${mock_bootstrap_log}"
summarize_messages "dds_bootstrap_client" "${dds_bootstrap_client_log}"

exit "${bootstrap_client_status}"
