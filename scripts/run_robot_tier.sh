#!/usr/bin/env bash
# Start the ROS 2 robot tier beside a running demo stack.
#
# This is the half of the Open World Model demo that plans. nav2 builds a
# costmap from the venue's declared extents and routes *around* the water;
# the kinematic fallback (SPATIALDDS_ROBOT_SIM=1) only declines to enter it.
# Tap the grass behind the pond and watch the difference.
#
#   ./run_bridge_server_docker.sh          # in one terminal, WITHOUT ROBOT_SIM
#   scripts/run_robot_tier.sh              # in another
#
# It shares the bridge container's network namespace rather than joining a
# docker network. Both stacks then see one loopback, which is what lets
# CycloneDDS discover across them without multicast -- and it is why this
# needs the bridge to be up first: a namespace cannot be shared with a
# container that does not exist.
set -euo pipefail

IMAGE="${ROBOT_TIER_IMAGE:-spatialdds-robot-tier:latest}"
NAME="${ROBOT_TIER_NAME:-robot_tier}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running or not accessible." >&2
  exit 1
fi

bridge="$(docker ps --format '{{.Names}}' | grep '^dds_bridge' | head -1 || true)"
if [[ -z "${bridge}" ]]; then
  echo "No dds_bridge container is running." >&2
  echo "Start the stack first:  ./run_bridge_server_docker.sh" >&2
  exit 1
fi

# One writer per key. The bridge starts its own kinematic robot when
# SPATIALDDS_ROBOT_SIM=1, and it owns the same entity this tier owns --
# `already_published()` would make one of them refuse, and which one loses is
# a race. Better to say so here than to debug a robot that is sometimes absent.
# Anchored, and it has to be. The container's PID 1 is the startup shell,
# whose command line *contains* this string because it is the script that
# conditionally launches it -- so an unanchored match reports a kinematic
# robot on a stack that has none. web/tests/model-stack.helpers.ts carries
# the same note about pkill for the same reason.
if docker exec "${bridge}" pgrep -f '^python3 -m spatialdds_demo.robot_bridge' >/dev/null 2>&1; then
  echo "The stack is running its kinematic robot, which owns the same key." >&2
  echo "Restart it without that flag, then try again:" >&2
  echo "  SPATIALDDS_MODEL_LAYER=1 ./run_bridge_server_docker.sh" >&2
  exit 1
fi

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "Image ${IMAGE} not found. Build it first (this takes a while --"
  echo "it builds CycloneDDS from source):"
  echo "  docker build -t ${IMAGE} robot_tier/"
  exit 1
fi

docker rm -f "${NAME}" >/dev/null 2>&1 || true

echo "Starting the ROS 2 robot tier (${IMAGE})"
echo "  sharing the network namespace of ${bridge}"
echo "  nav2 + keep-out node + plaza sim; ~20 s before it accepts goals"

exec docker run --rm --name "${NAME}" \
  --network "container:${bridge}" \
  -v "${REPO}:/ws" \
  -w /ws \
  -e SPATIALDDS_DDS_DOMAIN="${SPATIALDDS_DDS_DOMAIN:-1}" \
  -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
  -e CYCLONEDDS_HOME=/usr/local \
  "${IMAGE}" bash /ws/robot_tier/_bringup.sh
