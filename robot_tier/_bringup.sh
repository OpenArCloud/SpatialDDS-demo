#!/usr/bin/env bash
# Bring the tier up inside the container: sim, keep-out, nav2, lifecycle.
set -e
source /opt/ros/humble/setup.bash
export PYTHONPATH=/ws:$PYTHONPATH
# The demo's transport gate: `require_dds_env` refuses to run against a
# transport nobody asked for, which is the right default and needs saying
# here rather than assuming.
export SPATIALDDS_TRANSPORT=dds
export SPATIALDDS_DDS_DOMAIN=${SPATIALDDS_DDS_DOMAIN:-1}
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
PARAMS=/ws/robot_tier/nav2_params.yaml

# **The image carries `spatialdds_ros` from requirements.txt, and a stale
# image has the wrong one or none.** Checked here, before anything starts,
# because every launch below is backgrounded: when the adoption moved the
# robot to the library on 2026-10-19 and this image was not rebuilt, the node
# died with `No module named 'spatialdds_ros'` into its own stdout while nav2
# and the keep-out came up healthy. The tier looked fine and nothing owned
# ent:robot:tb3 on the bus -- a planner with nothing to drive and no page to
# drive it from. Refusing to start says that in one line instead.
if ! python3 -c "import spatialdds_ros" 2>/dev/null; then
  echo "robot_tier: this image has no spatialdds_ros, so nothing would own" >&2
  echo "  ent:robot:tb3 -- nav2 and the keep-out would run with no robot." >&2
  echo "  The image installs it from requirements.txt; rebuild after a pin" >&2
  echo "  bump, from the repo root:" >&2
  echo "    docker build -t spatialdds-robot-tier:latest -f robot_tier/Dockerfile ." >&2
  exit 1
fi
echo "robot_tier: spatialdds_ros $(python3 -c 'import spatialdds_ros as r; print(r.__version__)')"

python3 /ws/robot_tier/plaza_sim_node.py &
python3 /ws/robot_tier/keepout_node.py &
sleep 3

# The robot bridge, in ROS mode: one process reading /odom, owning
# ent:robot:tb3 on the bus, and driving through nav2. It refuses to start if
# something else is already publishing that key -- see already_published().
# **The library's node, not the demo's bridge** (adopted 2026-10-19). Same
# job, and its pose input defaults to the TF chain `map -> base_link` rather
# than `/odom` — which matters here more than anywhere: `plaza_sim_node`
# publishes `map -> odom` as a static identity, so `/odom` happened to be
# map-frame in this tier and would not be on a localizing robot. Retiring the
# function retired the defect.
#
# Anchorless on purpose: `model_service` publishes this frame's
# FrameTransform and the scene keeps the single claim.
python3 -m spatialdds_ros.robot_node --ros-args \
  -p scene:=ut-austin-littlefield \
  -p entity_id:=ent:robot:tb3 \
  -p frame_id:=map/ut-littlefield-fountain \
  -p source_id:=svc:robot:demo/tb3 \
  -p label:=TurtleBot \
  -p ground_z:=-0.84 \
  -p domain:=${SPATIALDDS_DDS_DOMAIN:-1} \
  -p content_refs:='[catalog:dcef7c31-1d5f-54aa-9344-899912f9d34a]' \
  -p external_refs:='[wikidata=Q4810574]' &
sleep 2

ros2 run nav2_planner planner_server    --ros-args --params-file "$PARAMS" &
ros2 run nav2_controller controller_server --ros-args --params-file "$PARAMS" &
ros2 run nav2_behaviors behavior_server --ros-args --params-file "$PARAMS" &
ros2 run nav2_bt_navigator bt_navigator --ros-args --params-file "$PARAMS" &
sleep 5
ros2 run nav2_lifecycle_manager lifecycle_manager --ros-args \
  -p use_sim_time:=false -p autostart:=true \
  -p "node_names:=[controller_server, planner_server, behavior_server, bt_navigator]" &
wait
