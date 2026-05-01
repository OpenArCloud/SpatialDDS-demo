#!/usr/bin/env bash
# Tier-3 publisher container entrypoint: emits PoseStamped, NavSatFix, and Imu
# on standard ROS 2 topics. Used by docker-compose.test.yaml.
# Source ROS 2 setup BEFORE enabling -u (setup.bash uses unset variables).
source /opt/ros/humble/setup.bash
set -eo pipefail

echo "[publisher] /robot/pose @ 10 Hz"
ros2 topic pub /robot/pose geometry_msgs/msg/PoseStamped \
  '{header: {stamp: {sec: 100, nanosec: 0}, frame_id: map}, pose: {position: {x: 42.0, y: -7.5, z: 1.2}, orientation: {x: 0.0, y: 0.0, z: 0.383, w: 0.924}}}' \
  -r 10 &

echo "[publisher] /gps/fix @ 1 Hz"
ros2 topic pub /gps/fix sensor_msgs/msg/NavSatFix \
  '{header: {frame_id: gnss}, latitude: 30.267, longitude: -97.743, altitude: 150.0}' \
  -r 1 &

echo "[publisher] /robot/imu @ 50 Hz"
ros2 topic pub /robot/imu sensor_msgs/msg/Imu \
  '{header: {frame_id: imu_link}, angular_velocity: {x: 0.001, y: -0.002, z: 0.05}, linear_acceleration: {x: 0.1, y: -0.05, z: 9.78}}' \
  -r 50 &

wait
