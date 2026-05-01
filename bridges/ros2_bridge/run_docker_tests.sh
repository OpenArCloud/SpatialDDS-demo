#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

PLATFORM_FLAG=""
case "$(uname -m)" in
  arm64|aarch64)
    # ros:humble-ros-base is amd64-only; emulate on Apple Silicon.
    PLATFORM_FLAG="--platform=linux/amd64"
    echo "[run_docker_tests] arm64 host detected; building with $PLATFORM_FLAG"
    ;;
esac

echo "============================================"
echo "  Building ROS 2 bridge test image..."
echo "============================================"
docker build $PLATFORM_FLAG -f bridges/ros2_bridge/Dockerfile.test -t spatialdds-ros2-test .

echo ""
echo "============================================"
echo "  Tier 1 + 2 + 3b (single container)"
echo "============================================"
docker run --rm $PLATFORM_FLAG --network host spatialdds-ros2-test

echo ""
echo "============================================"
echo "  Tier 3 (full integration, 3 containers)"
echo "============================================"
(
  cd bridges/ros2_bridge
  docker compose -f docker-compose.test.yaml up \
    --build --abort-on-container-exit --exit-code-from verifier
  docker compose -f docker-compose.test.yaml down
)

echo ""
echo "============================================"
echo "  All tiers passed."
echo "============================================"
