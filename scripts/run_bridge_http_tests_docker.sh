#!/usr/bin/env bash
set -euo pipefail

# Run from anywhere: the mount, the build context and the log all resolve from
# this script's own location. It used to use ${PWD}, which meant running it
# from a subdirectory mounted that subdirectory as /app and failed deep inside
# a container rather than here.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO}"

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
  docker build -t cyclonedds-python "${REPO}"
fi

docker run --rm --network host \
  -v "${REPO}:/app" \
  -w /app \
  -e PYTHONPATH=/app \
  -e SPATIALDDS_TRANSPORT=dds \
  -e SPATIALDDS_DDS_DOMAIN=1 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  cyclonedds-python bash -lc "\
    python3 -m pip install -r /app/requirements.txt -r /app/bridges/web_bridge/requirements.txt && \
    python3 bridges/web_bridge/tests/run_bridge_http_tests.py"
