#!/usr/bin/env bash
# Capture the OWM demo as it ships, from inside the demo image.
#
#   docker run --rm -v "$PWD:/app" -w /app cyclonedds-python \
#       bash tools/capture_owm_session.sh
#
# The real entry points under the shipped env contract -- SPATIALDDS_TRANSPORT,
# CYCLONEDDS_URI=file:///etc/cyclonedds.xml, require_dds_env -- with
# tools/record_owm_session.py joined to the same bus by --domain. Nothing here
# constructs a participant by hand; the harness in run_owm_session.py does that
# and is the weaker provenance.
#
# ORDERING IS THE CONTRACT OF THIS FILE, not a detail of it.
#
# The recorder must finish before any publisher does. `ModelPublisher.close`
# disposes every entity on purpose, so a clean shutdown is six genuine
# disposes; `gnome_publisher` disposes on SIGTERM. Both are correct behaviour
# and both are poison in an evidence recording, because neither carries a
# RETIRED tombstone and the gateway's recording loader derives removal from
# the tombstone alone. A window that outlived its publishers would produce a
# capture whose metadata says the venue was removed and whose served form
# shows it alive -- a recording that contradicts itself.
#
# So: recorder in the foreground, every publisher killed only after it
# returns. The two removal exhibits are the deliberate retires inside the
# window, each a tombstone followed by a dispose, which is the one removal
# shape the loader honours.
set -u
export SPATIALDDS_TRANSPORT=dds
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
export SPATIALDDS_DDS_DOMAIN="${SPATIALDDS_DDS_DOMAIN:-51}"
export PYTHONPATH=/app
DOMAIN="$SPATIALDDS_DDS_DOMAIN"
OUT="${OUT:-/app/recordings/owm-littlefield.mcap}"
WINDOW="${WINDOW:-44}"
DRIVER="${DRIVER:-tools/capture_owm_session.sh (scripted operator session)}"
LOG=/tmp/capture
mkdir -p $LOG
cd /app

say () { echo "capture: $*"; }

# The demo image has CycloneDDS and the demo's own requirements; it does not
# carry `mcap`, which only the recorder needs. Environment, not a defect --
# run_bridge_server_docker.sh pip-installs at container start for the same
# reason. Pinned the same way bridges/ros2_bridge/requirements.txt pins it,
# which is the only place in the tree that declares it.
python3 -c "import mcap" 2>/dev/null || {
  say "installing mcap (absent from the demo image)"
  # certifi first, and only on the path that actually fetches:
  # GHSA-6q48-6c7w-9gw2. See docs/certifi-emsign-advisory.md.
  python3 -m pip install -U pip certifi --quiet
  python3 -m pip install --quiet "mcap>=1.1.0" || {
    say "could not install mcap; the recorder cannot run"; exit 1; }
}

# --- the venue, holding ------------------------------------------------------
say "domain $DOMAIN, window ${WINDOW}s, out $OUT"
python3 -m spatialdds_demo.model_service > $LOG/model.log 2>&1 &  MODEL=$!
python3 scripts/gnome_publisher.py       > $LOG/gnome.log 2>&1 &  GNOME=$!
python3 -m spatialdds_demo.pond_watch    > $LOG/pond.log  2>&1 &  POND=$!
sleep 7
grep -q "latched, holding" $LOG/model.log || { say "model service never latched"; cat $LOG/model.log; exit 1; }
grep -q "latched, holding" $LOG/gnome.log || { say "gnome never latched";         cat $LOG/gnome.log; exit 1; }
# `^model: entity` also matches the "entity topic" banner line, which made
# this say 7. A readiness line that miscounts is a wrong number in prose.
say "venue up: $(grep -c '^model: entity ent:' $LOG/model.log) entities, gnome placed, pond_watch measuring"

python3 -m spatialdds_demo.duck_mover > $LOG/mover.log 2>&1 &  MOVER=$!
sleep 3

# --- the operator session, inside the window --------------------------------
# Scheduled relative to the recorder's start so each lands in the capture.
(
  sleep 6
  say "reshape: shrinking the shallows"
  python3 scripts/reshape_pond.py --shrink --entity ent:shallows:littlefield \
      >> $LOG/ops.log 2>&1

  sleep 7
  say "retire ent:duck:east — tombstone, then dispose"
  python3 scripts/retire_entity.py ent:duck:east "taken in for the winter" \
      >> $LOG/ops.log 2>&1

  sleep 8
  say "restore — the retired id is not burned"
  python3 scripts/retire_entity.py --restore >> $LOG/ops.log 2>&1

  sleep 8
  say "retire ent:duck:catalog-pose — and no restore follows it"
  python3 scripts/retire_entity.py ent:duck:catalog-pose \
      "kept in for the season; not coming back" >> $LOG/ops.log 2>&1
) &
OPS=$!

# --- the capture, in the foreground so teardown cannot precede it -----------
python3 tools/record_owm_session.py --seconds "$WINDOW" --domain "$DOMAIN" \
    --out "$OUT" --driver "$DRIVER"
RC=$?

# --- teardown, only now -----------------------------------------------------
wait $OPS 2>/dev/null
for p in $MOVER $POND $GNOME $MODEL; do
  kill $p 2>/dev/null
done
for p in $MOVER $POND $GNOME $MODEL; do
  wait $p 2>/dev/null
done
say "operator session:"; sed 's/^/  /' $LOG/ops.log
say "service log, the lifecycle lines:"
grep -v "^model: moved" $LOG/model.log | tail -14 | sed 's/^/  /'
exit $RC
