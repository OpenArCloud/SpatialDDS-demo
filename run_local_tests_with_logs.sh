#!/usr/bin/env bash
set -u

server_name=""
catalog_name=""
server_pid=""
catalog_pid=""

summarize_messages() {
  local label="$1"
  local log_path="$2"
  local pattern="\\[[[:space:]]*[0-9.]+s\\][[:space:]]+[←→][[:space:]]+(ANNOUNCE|COVERAGE_QUERY|COVERAGE_RESPONSE|LOCALIZE_REQUEST|LOCALIZE_RESPONSE|CATALOG_QUERY|CATALOG_RESPONSE|ANCHOR_DELTA)\\b"

  echo "High-level messages (${label}):"
  if command -v rg >/dev/null 2>&1; then
    rg -n "${pattern}" "${log_path}" || true
  else
    grep -En "${pattern}" "${log_path}" || true
  fi
}

cleanup() {
  if [[ -n "${server_name}" || -n "${catalog_name}" ]]; then
    echo "Cleaning up DDS containers..."
  fi
  if [[ -n "${server_name}" ]]; then
    docker stop "${server_name}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${catalog_name}" ]]; then
    docker stop "${catalog_name}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${server_pid}" || -n "${catalog_pid}" ]]; then
    wait "${server_pid}" "${catalog_pid}" 2>/dev/null || true
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

if ! docker image inspect cyclonedds-python:latest >/dev/null 2>&1; then
  echo "Docker image cyclonedds-python:latest not found. Building from Dockerfile..."
  docker build -t cyclonedds-python .
fi

ts="$(date +%Y%m%d_%H%M%S)"
mock_log="mock_test_${ts}.log"
dds_server_log="dds_server_${ts}.log"
dds_catalog_log="dds_catalog_${ts}.log"
dds_client_log="dds_client_${ts}.log"

echo "Running mock test -> ${mock_log}"
docker run --rm --network host cyclonedds-python \
  python3 spatialdds_test.py --detailed >"${mock_log}" 2>&1
mock_status=$?

echo "Running DDS demo (server/catalog/client) -> ${dds_server_log}, ${dds_catalog_log}, ${dds_client_log}"
server_name="dds_server_${ts}"
catalog_name="dds_catalog_${ts}"
docker run --rm --network host --name "${server_name}" \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=0 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python python3 spatialdds_demo_server.py --detailed >"${dds_server_log}" 2>&1 &
server_pid=$!

docker run --rm --network host --name "${catalog_name}" \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=0 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python python3 spatialdds_catalog_server.py --detailed >"${dds_catalog_log}" 2>&1 &
catalog_pid=$!

sleep 2

docker run --rm --network host \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=0 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python python3 spatialdds_demo_client.py --detailed >"${dds_client_log}" 2>&1
client_status=$?

echo "Summary:"
if [[ "${mock_status}" -eq 0 ]]; then
  echo "- Mock test: PASS (${mock_log})"
else
  echo "- Mock test: FAIL (${mock_log})"
fi
if [[ "${client_status}" -eq 0 ]]; then
  echo "- DDS demo: PASS (${dds_client_log})"
else
  echo "- DDS demo: FAIL (${dds_client_log})"
fi
echo "- Server log: ${dds_server_log}"
echo "- Catalog log: ${dds_catalog_log}"

summarize_messages "mock" "${mock_log}"
summarize_messages "dds_client" "${dds_client_log}"

echo "Mock test exit: ${mock_status}"
echo "DDS client exit: ${client_status}"
echo "LOGS: ${mock_log} ${dds_server_log} ${dds_catalog_log} ${dds_client_log}"

exit "${client_status}"
