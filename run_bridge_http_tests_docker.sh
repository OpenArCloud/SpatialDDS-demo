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

docker run --rm --network host \
  -v "${PWD}:/app" \
  -w /app \
  -e PYTHONPATH=/app \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=1 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python bash -lc "\
    python3 -m pip install -r /app/requirements.txt -r /app/bridge/requirements.txt && \
    python3 bridge/tests/run_bridge_http_tests.py"
