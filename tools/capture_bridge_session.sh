#!/usr/bin/env bash
# The end-to-end session: a web POST becomes a verb on the bus, the robot acts
# on it, the ducks react, and an N.5 subscriber watches the whole thing.
#
#   docker run --rm -p 8089:8089 -v "$PWD:/app" -w /app cyclonedds-python \
#       bash tools/capture_bridge_session.sh
#
# Both lanes in one session, which is what makes it end-to-end rather than two
# demonstrations:
#
#   upstream    a goto POSTed to the binding's N.6 surface, crossing the
#               bridge onto DDS as a keyed ModelCommand
#   downstream  the robot's poses and the ducks' reaction reaching an N.5
#               subscriber over the same binding
#
# The ordering contract from tools/capture_owm_session.sh applies unchanged --
# the recorder finishes before any publisher does, because a clean shutdown
# disposes entities and a capture that outlived its publishers would serve a
# venue its own metadata says was removed. Read that file's header for the
# argument.
#
# The bridge needs `spatialdds[server]` 0.5.2 from PyPI, which the demo image
# does not carry: the binding is a dependency of this lane, not of the demo.
set -u
export SPATIALDDS_TRANSPORT=dds
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
export SPATIALDDS_DDS_DOMAIN="${SPATIALDDS_DDS_DOMAIN:-55}"
export PYTHONPATH=/app
DOMAIN="$SPATIALDDS_DDS_DOMAIN"
PORT="${PORT:-8089}"
TOKEN="${TOKEN:-bridge-session}"
OUT="${OUT:-/app/recordings/bridge-littlefield.mcap}"
WINDOW="${WINDOW:-70}"
LOG=/tmp/bridgecap
mkdir -p $LOG
cd /app

say () { echo "session: $*"; }

python3 -c "import mcap" 2>/dev/null || python3 -m pip install --quiet "mcap>=1.1.0"
# 0.5.2, and the pin is exact rather than >=. On 0.5.0 `make_app` discarded a
# command registry that was empty when passed -- the only order an embedder
# can use -- so the upstream lane advertises nothing and every POST is
# refused. An inexact pin would let a session run on the broken one and look
# like a bridge defect.
BINDING="${BINDING:-0.5.2}"
python3 -c "import spatialdds, sys; sys.exit(0 if spatialdds.__version__=='$BINDING' else 1)" 2>/dev/null || {
  say "installing spatialdds[server]==$BINDING from PyPI (the binding, embedded)"
  python3 -m pip install --quiet "spatialdds[server]==$BINDING" || {
    say "could not install the binding"; exit 1; }
}
say "binding  spatialdds[server] $(python3 -c 'import spatialdds; print(spatialdds.__version__)') from PyPI"

# --- the venue, the robot, the ducks ---------------------------------------
say "domain $DOMAIN, window ${WINDOW}s"
python3 -m spatialdds_demo.model_service > $LOG/model.log 2>&1 &  MODEL=$!
python3 scripts/gnome_publisher.py       > $LOG/gnome.log 2>&1 &  GNOME=$!
python3 -m spatialdds_demo.pond_watch    > $LOG/pond.log  2>&1 &  POND=$!
sleep 7
grep -q "latched, holding" $LOG/model.log || { say "venue never latched"; cat $LOG/model.log; exit 1; }
python3 -m spatialdds_demo.robot_bridge --source kinematic > $LOG/robot.log 2>&1 & ROBOT=$!
python3 -m spatialdds_demo.duck_mover > $LOG/mover.log 2>&1 &  MOVER=$!
sleep 4
say "venue up: $(grep -c '^model: entity ent:' $LOG/model.log) entities, robot kinematic, mover watching"

# --- the bridge: the binding, embedded, serving the live bus ---------------
python3 -m bridges.web_bridge.binding --port "$PORT" --domain "$DOMAIN" \
    --token "$TOKEN" > $LOG/bridge.log 2>&1 &  BRIDGE=$!
sleep 6
grep -q "^bridge: serving" $LOG/bridge.log || { say "bridge never came up"; cat $LOG/bridge.log; exit 1; }
sed 's/^/  /' $LOG/bridge.log

# --- the session: POST a goto, watch it land ------------------------------
(
  sleep 4
  say "leg 1 — a web client POSTs goto (25, -28): outside 15 m of every duck"
  python3 tools/post_goto.py --endpoint "http://127.0.0.1:$PORT" \
      --token "$TOKEN" --to 25 -28 >> $LOG/ops.log 2>&1
  sleep 26
  say "leg 2 — a web client POSTs goto (12, -21): inside 15 m, the reaction"
  python3 tools/post_goto.py --endpoint "http://127.0.0.1:$PORT" \
      --token "$TOKEN" --to 12 -21 >> $LOG/ops.log 2>&1
) &
OPS=$!

# --- an N.5 subscriber, watching the downstream lane while it happens -----
python3 tools/watch_n5.py --endpoint "http://127.0.0.1:$PORT" \
    --token "$TOKEN" --seconds "$WINDOW" \
    --out /app/recordings/bridge-n5-observed.json > $LOG/n5.log 2>&1 & N5=$!

# --- and the DDS-side recorder, for the measurement -----------------------
python3 tools/record_owm_session.py --seconds "$WINDOW" --domain "$DOMAIN" \
    --out "$OUT" \
    --driver "tools/capture_bridge_session.sh (web POST -> bus -> N.5)"
RC=$?

wait $OPS 2>/dev/null
wait $N5 2>/dev/null
for p in $BRIDGE $MOVER $ROBOT $POND $GNOME $MODEL; do kill $p 2>/dev/null; done
for p in $BRIDGE $MOVER $ROBOT $POND $GNOME $MODEL; do wait $p 2>/dev/null; done

say "the upstream lane:";   sed 's/^/  /' $LOG/ops.log
# The whole N.5 report, not a tail. `tail -8` cut the entity lane's 80
# events in the first run of this script and made a working lane look
# absent until the JSON was read directly -- a report that drops
# evidence is worse than a long one.
say "the N.5 subscriber, in full:"; sed 's/^/  /' $LOG/n5.log
say "the bridge's own log:"; sed 's/^/  /' $LOG/bridge.log
say "the robot's command lane:"
grep -iE "goto|command|declin" $LOG/robot.log | sed 's/^/  /' || true
exit $RC
