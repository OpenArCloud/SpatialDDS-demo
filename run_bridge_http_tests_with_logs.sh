#!/usr/bin/env bash
set -u

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
log_file="bridge_http_${bts}.log"

echo "Running bridge HTTP pytest in Docker -> ${log_file}"

docker run --rm --network host \
  -e PYTHONUNBUFFERED=1 \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=1 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  -v "${PWD}:/app" \
  cyclonedds-python bash -lc "python3 -m pip install -r requirements.txt -r bridge/requirements.txt >/dev/null && python3 -m pytest -q bridge/tests/test_bridge_http.py -s" \
  >"${log_file}" 2>&1

status=$?
if [[ ${status} -eq 0 ]]; then
  echo "Bridge HTTP pytest: PASS (${log_file})"
else
  echo "Bridge HTTP pytest: FAIL (${log_file})"
fi

exit "${status}"
