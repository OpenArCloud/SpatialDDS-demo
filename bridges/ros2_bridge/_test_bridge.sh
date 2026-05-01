#!/usr/bin/env bash
# Tier-3 bridge container entrypoint. Used by docker-compose.test.yaml.
# Source ROS 2 setup BEFORE enabling -u (setup.bash uses unset variables).
source /opt/ros/humble/setup.bash
set -eo pipefail

# Wait for the publisher to start announcing topics.
sleep 3
echo "[bridge] starting SpatialDDS ↔ ROS 2 bridge"
exec python3 -m bridges.ros2_bridge.bridge_node \
  --operator test_fleet \
  --ros-domain 42 \
  --dds-domain 43 \
  --topics /robot/pose /gps/fix /robot/imu
