#!/usr/bin/env bash
# The OWM 0.2 prototype's lifecycle film: one capture in which every way an
# edge is born and ends happens on the bus, against the demo as it ships.
#
#   SPATIALDDS_MODEL_LAYER=1 SPATIALDDS_POND_WATCH=1 ./run_bridge_server_docker.sh
#   scripts/run_robot_tier.sh
#   bash tools/capture_owm02_lifecycle.sh
#
# The stack and the nav2 tier must already be up; this drives them and records.
# The recorder runs inside the bridge container on its domain, as a peer like
# any other consumer, and writes recordings/owm-02-lifecycle.mcap with its
# provenance beside it.
#
# THE SCRIPT, in order. Each leg waits for the robot to arrive, read off the
# gateway, and only then checks the edges: waiting on the edges alone was the
# first run's mistake. nav2 rounds the pond's east end along x = 25.8, which
# is the fountain box's own east edge (25.81), so the edges are born and
# retired there on the way, and that flicker satisfied every wait within
# seconds while the robot was still on its first leg.
#
#   1  goto (12, -2), north of the pond: inside the fountain's published
#      extent and clear of the keep-out's shoulder. Both zone watches' edges
#      are born, each with its own valid_from.
#   2  goto (17, -23), the start: both edges retire, "no longer holds", with
#      valid_until.
#   3  goto (12, -2) again: born again.
#   4  SIGKILL the latched zone watch: its edge stands, liveliness lost, never
#      disposed.
#   5  SIGTERM the fast zone watch: a clean stop withdraws its edge,
#      "asserter stopped", valid_until unset.
#   6  restart both: their edges are born again, on the same ids.
#   7  kill the sim: the robot goes UNOBSERVED (spatialdds_ros 0.4.1), and
#      every edge about it retires, "subject unobserved".
#
# The near fuser's edges come and go throughout, as the robot passes within
# 15 m of the ducks, and the ducks swim away while they hold.
set -u
cd "$(dirname "$0")/.."
SCENE=ut-austin-littlefield
GW=${GW:-http://127.0.0.1:8089}
TOKEN=${SPATIALDDS_BINDING_TOKEN:-bridge-session}
WINDOW=${WINDOW:-540}
OUT=${OUT:-/app/recordings/owm-02-lifecycle.mcap}
REL="$GW/$SCENE/world/oarc.relationship/v1"
ZONE_LATCHED=svc:claims:demo/zone-latched
ZONE_FAST=svc:claims:demo/zone-fast

C=$(docker ps --format '{{.Names}}' | grep '^dds_bridge' | head -1)
[ -n "$C" ] || { echo "film: no bridge container; start the stack" >&2; exit 2; }
docker ps --format '{{.Names}}' | grep -qx robot_tier \
  || { echo "film: no robot_tier; run scripts/run_robot_tier.sh" >&2; exit 2; }

say() { echo "film: $(date -u +%H:%M:%S) $*"; }

# The robot's edges by asserter, as "asserter state" lines.
edges() {
  curl -s "$REL" | python3 -c "
import json, sys
for d in json.load(sys.stdin):
    if d['subject_ref'] == 'ent:robot:tb3' and d['predicate'].endswith('sfWithin'):
        print(d['asserter'])"
}
# Wait until `edges` lists exactly the asserters given (none for empty).
wait_edges() {
  local want="$1" limit="$2" got
  for _ in $(seq 1 "$limit"); do
    got=$(edges | sort | tr '\n' ' ' | sed 's/ $//')
    [ "$got" = "$want" ] && { say "within-fountain edges: [${got}]"; return 0; }
    sleep 1
  done
  say "GAVE UP waiting for [${want}], have [${got}]"; return 1
}
# Wait until the robot's latched record is within 1.5 m of (x, y) and has
# stopped: nav2 has finished. A goal sent before then is a preemption, and on
# this tier nav2 was measured reporting the new goal succeeded at the old
# goal's position without moving (claims-stack.spec.ts says more).
arrive() {
  local x="$1" y="$2" limit="$3" at
  for _ in $(seq 1 "$limit"); do
    at=$(curl -s "$GW/$SCENE/world/entity/v1/ent:robot:tb3" | python3 -c "
import json, math, sys
t = json.load(sys.stdin)['pose']['t']
print(f'{t[0]:.1f},{t[1]:.1f}', 'yes' if math.hypot(t[0]-$x, t[1]-$y) < 1.5 else 'no')")
    case "$at" in *yes)
      sleep 3
      again=$(curl -s "$GW/$SCENE/world/entity/v1/ent:robot:tb3" | python3 -c "
import json, sys
t = json.load(sys.stdin)['pose']['t']
print(f'{t[0]:.1f},{t[1]:.1f}')")
      [ "$again" = "${at% *}" ] && { say "robot at ${at% *}, stopped"; return 0; };;
    esac
    sleep 1
  done
  say "GAVE UP waiting for the robot at ($x, $y); it is at ${at% *}"; return 1
}
goto() {
  python3 -W ignore tools/post_goto.py --endpoint "$GW" --token "$TOKEN" \
    --to "$1" "$2" 2>/dev/null | tail -1 | cut -c1-100
}
# One zone watch, by its own argv. Anchored at `^python3`: the container's
# main `bash -lc` carries every service's command line in its own, so an
# unanchored pattern also matches the shell that holds the whole stack.
zone() {   # zone <latched|fast> <signal>
  local pat="^python3 -m spatialdds_demo.zone_watch --evidence $1"
  docker exec "$C" pgrep -f "$pat" >/dev/null \
    || { say "no zone_watch --evidence $1 to signal"; return 1; }
  docker exec "$C" pkill "-$2" -f "$pat" && say "sent SIG$2 to zone_watch --evidence $1"
}
zone_start() {
  docker exec -d "$C" bash -lc "cd /app && PYTHONPATH=/app \
    python3 -m spatialdds_demo.zone_watch --evidence $1 \
    >>/app/bridges/web_bridge/logs/zone_$1_film.log 2>&1"
  say "started zone_watch --evidence $1"
}

say "recording ${WINDOW}s to ${OUT}"
docker exec "$C" bash -lc "cd /app && PYTHONPATH=/app python3 -W ignore \
  tools/record_owm_session.py --seconds $WINDOW --out $OUT \
  --domain \${SPATIALDDS_DDS_DOMAIN:-1} \
  --driver 'tools/capture_owm02_lifecycle.sh (scripted, nav2 tier)'" \
  > /tmp/owm02-recorder.$$.log 2>&1 &
REC=$!
sleep 6
ok=1
both="$ZONE_FAST $ZONE_LATCHED"

say "1: north of the pond";      goto 12 -2
arrive 12 -2 180 || ok=0;        wait_edges "$both" 10 || ok=0
say "2: back to the start";      goto 17 -23
arrive 17 -23 180 || ok=0;       wait_edges "" 10 || ok=0
say "3: north again";            goto 12 -2
arrive 12 -2 180 || ok=0;        wait_edges "$both" 10 || ok=0
sleep 3
say "4: kill the latched watch"; zone latched KILL
sleep 15                          # past the liveliness lease, still standing
wait_edges "$both" 5 || ok=0
say "5: stop the fast watch";    zone fast TERM
wait_edges "$ZONE_LATCHED" 30 || ok=0
say "6: restart both";           zone_start latched; zone_start fast
wait_edges "$both" 60 || ok=0
sleep 3
say "7: kill the sim"
docker exec robot_tier pkill -f plaza_sim_node.py && say "sim killed"
wait_edges "" 120 || ok=0

say "waiting for the recorder"
wait "$REC"
cat /tmp/owm02-recorder.$$.log
rm -f /tmp/owm02-recorder.$$.log
[ "$ok" = 1 ] && say "every step did what it said" || say "A STEP FAILED; see above"
[ "$ok" = 1 ]
