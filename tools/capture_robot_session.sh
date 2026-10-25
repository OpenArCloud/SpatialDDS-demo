#!/usr/bin/env bash
# Capture the robot demo as it ships, from inside the demo image, with the
# duck-reaction behaviour in it.
#
#   docker run --rm -v "$PWD:/app" -w /app cyclonedds-python \
#       bash tools/capture_robot_session.sh
#
# Same contract as tools/capture_owm_session.sh and for the same reasons: the
# real entry points under the same env contract, the recorder joined by --domain,
# and the recorder in the foreground so no publisher's farewell disposes land
# inside the window. Read that file's header for the ordering argument; it is
# the same one.
#
# WHAT IS BEING MEASURED, and why the drive has two legs.
#
# duck_mover avoids `ent:robot:tb3` within AVOID_RADIUS_M = 15.0 m. The robot
# starts at (17.0, -21.5), which is 9.0-11.0 m from all three ducks -- already
# inside the threshold -- so a capture that just let it sit would have no
# far-field baseline, and "the ducks moved away" would be indistinguishable
# from ducks wandering, which they do anyway.
#
# So the robot is driven out and then back:
#
#   leg 1   goto (25, -28)   19.1-20.9 m from every duck: outside. ~17s.
#   leg 2   goto (12, -21)    6.7-11.4 m from every duck: inside.  ~25s.
#
# Both goals sit south of the pond (y < -20), so neither leg asks the robot to
# cross a declared keep-out -- the kinematic navigator would decline, and a
# capture of a refusal is not a capture of a reaction.
#
# That gives one recording carrying both arms of the comparison, so the
# control is not a second run that could have been tuned to agree.
set -u
export SPATIALDDS_TRANSPORT=dds
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
export SPATIALDDS_DDS_DOMAIN="${SPATIALDDS_DDS_DOMAIN:-54}"
export PYTHONPATH=/app
DOMAIN="$SPATIALDDS_DDS_DOMAIN"
OUT="${OUT:-/app/recordings/robot-littlefield.mcap}"
WINDOW="${WINDOW:-70}"
DRIVER="${DRIVER:-tools/capture_robot_session.sh (scripted operator session)}"
LOG=/tmp/robotcap
mkdir -p $LOG
cd /app

say () { echo "capture: $*"; }

python3 -c "import mcap" 2>/dev/null || {
  say "installing mcap (absent from the demo image)"
  # certifi first, and only on the path that actually fetches:
  # GHSA-6q48-6c7w-9gw2. See docs/certifi-emsign-advisory.md.
  python3 -m pip install -U pip certifi --quiet
  python3 -m pip install --quiet "mcap>=1.1.0" || {
    say "could not install mcap; the recorder cannot run"; exit 1; }
}

goto () {   # x y note
  python3 - "$1" "$2" "$DOMAIN" <<'PY'
import sys, time
import spatialdds as sd
from spatialdds_demo.lanes import MODEL_COMMAND, SCENE
from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.core import Aabb3, PoseSE3
from spatialdds.dds.types.spatial.owm import ModelCommand
x, y, domain = float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
p = sd.participant(SCENE, domain=domain,
                   service_id="tool:capture_robot_session")
w = p.writer("model_command", qos_profile=MODEL_COMMAND)
w.wait_matched(timeout=5.0)
now = time.time()
w.write(ModelCommand(
    command_id=f"cmd:robot:goto-{x:.0f}-{y:.0f}", verb="goto",
    subject_id="ent:robot:tb3",
    reason="recorded session: driving the reaction measurement",
    requester_id="tool:capture_robot_session", has_pose=True,
    pose=PoseSE3(t=[x, y, -0.84], q=[0.0, 0.0, 0.0, 1.0]),
    has_extent=False, extent=Aabb3(min_xyz=[0.0]*3, max_xyz=[0.0]*3),
    stamp=Time(sec=int(now), nanosec=int((now % 1) * 1e9))))
p.close(reason="the command was sent")
PY
}

# --- the venue -------------------------------------------------------------
say "domain $DOMAIN, window ${WINDOW}s, out $OUT"
python3 -m spatialdds_demo.model_service > $LOG/model.log 2>&1 &  MODEL=$!
python3 scripts/gnome_publisher.py       > $LOG/gnome.log 2>&1 &  GNOME=$!
python3 -m spatialdds_demo.pond_watch    > $LOG/pond.log  2>&1 &  POND=$!
sleep 7
grep -q "latched, holding" $LOG/model.log || { say "model service never latched"; cat $LOG/model.log; exit 1; }
say "venue up: $(grep -c '^model: entity ent:' $LOG/model.log) entities"

# --- the robot and the ducks ----------------------------------------------
python3 -m spatialdds_demo.robot_bridge --source kinematic > $LOG/robot.log 2>&1 & ROBOT=$!
python3 -m spatialdds_demo.duck_mover > $LOG/mover.log 2>&1 &  MOVER=$!
sleep 5
grep -qi "robot\|kinematic" $LOG/robot.log || { say "robot bridge never came up"; cat $LOG/robot.log; exit 1; }
say "robot up (kinematic, no ROS); mover watching the fast lane"

# --- the two legs, inside the window --------------------------------------
(
  sleep 3
  say "leg 1 — goto (25, -28): outside 15 m of every duck, the control arm"
  goto 25 -28 >> $LOG/ops.log 2>&1
  sleep 26
  say "leg 2 — goto (12, -21): inside 15 m of every duck, the reaction arm"
  goto 12 -21 >> $LOG/ops.log 2>&1
) &
OPS=$!

python3 tools/record_owm_session.py --seconds "$WINDOW" --domain "$DOMAIN" \
    --out "$OUT" --driver "$DRIVER"
RC=$?

wait $OPS 2>/dev/null
for p in $MOVER $ROBOT $POND $GNOME $MODEL; do kill $p 2>/dev/null; done
for p in $MOVER $ROBOT $POND $GNOME $MODEL; do wait $p 2>/dev/null; done
say "robot log:";  grep -v "^robot: pose" $LOG/robot.log | tail -8 | sed 's/^/  /'
say "mover log:";  grep -i "clear\|robot" $LOG/mover.log | tail -5 | sed 's/^/  /'
exit $RC
