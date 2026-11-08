#!/usr/bin/env bash
# The claims tooth, inside its own container: the tier, a hazard, three arms.
#
# Started by tools/claims_tooth.sh, never by hand. Everything the checker
# reads is written under /out as it happens:
#
#   tier.log       the tier, the robot node's claims notes among it
#   lane.jsonl     the relationship lane as a reader saw it (claims_watch.py)
#   probes.jsonl   one plan and its mask per phase (_claims_probe.py)
#   events.jsonl   when each phase began, and the kill
#
# The phases, each planned on the global costmap the claims filter paints:
#   before      no hazard on the bus
#   claimed     the trusted camera holds its OBSERVED claim
#   after-kill  the camera SIGKILLed, its liveliness over
#   untrusted   the same claim from a camera nobody trusts
# ROS's setup script reads unset variables, so -u comes after it.
source /opt/ros/humble/setup.bash
set -u
export PYTHONPATH=/ws:${PYTHONPATH:-}
export SPATIALDDS_TRANSPORT=dds
export SPATIALDDS_DDS_DOMAIN=${SPATIALDDS_DDS_DOMAIN:-71}
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
OUT=/out
KILL_SIGNAL=${KILL_SIGNAL:-KILL}

event() { echo "{\"event\": \"$1\", \"t\": $(date +%s.%N | cut -c1-14)}" >> "$OUT/events.jsonl"; }
probe() { python3 /ws/robot_tier/_claims_probe.py "$1" "$OUT/probes.jsonl"; }
# Wait for a line in the tier log after a given line number, up to N seconds.
await_log() {
  local pattern="$1" since="$2" limit="$3" waited=0
  while (( waited < limit * 10 )); do
    if tail -n "+$since" "$OUT/tier.log" | grep -q -- "$pattern"; then return 0; fi
    sleep 0.1; waited=$((waited + 1))
  done
  echo "claims_tooth: gave up waiting ${limit}s for: $pattern" >&2
  return 1
}
lines() { wc -l < "$OUT/tier.log"; }

echo "claims_tooth: spatialdds_ros $(python3 -c 'import spatialdds_ros as r; print(r.__version__, r.__file__)')"
python3 -m spatialdds_demo.model_service --quiet > "$OUT/model_service.log" 2>&1 &
python3 /ws/tools/claims_watch.py "$OUT/lane.jsonl" > "$OUT/watch.log" 2>&1 &
WATCH=$!
bash /ws/robot_tier/_bringup.sh > "$OUT/tier.log" 2>&1 &

# nav2 is up when a plan comes back.
for _ in $(seq 1 30); do
  if python3 /ws/robot_tier/_claims_probe.py warmup /dev/null >/dev/null 2>&1; then break; fi
  sleep 2
done
sleep 3

event before
probe before

mark=$(lines)
event claimed-start
python3 /ws/tools/hazard_asserter.py --source svc:hazard:plaza-cam > "$OUT/asserter.log" 2>&1 &
CAM=$!
await_log "drawing rel:svc:hazard:plaza-cam" "$mark" 20
sleep 3                                  # the global costmap updates at 1 Hz
probe claimed

mark=$(lines)
kill "-$KILL_SIGNAL" "$CAM"
event "kill-$KILL_SIGNAL"
await_log "cell(s) at 50; drawing nothing" "$mark" 30
sleep 3
event after-kill
probe after-kill

mark=$(lines)
event untrusted-start
python3 /ws/tools/hazard_asserter.py --source svc:hazard:stranger > "$OUT/stranger.log" 2>&1 &
STRANGER=$!
# The node reports the stranger's claim once, when it is first skipped; if
# the trust rule admits it (the filter plant), it is drawn instead.
await_log "rel:svc:hazard:stranger" "$mark" 20
sleep 3
probe untrusted
kill -TERM "$STRANGER"; sleep 1.5

event done
kill -TERM "$WATCH"; sleep 0.5
kill $(jobs -p) 2>/dev/null
pkill -f nav2_ 2>/dev/null
pkill -f spatialdds_ros.robot_node 2>/dev/null
exit 0
