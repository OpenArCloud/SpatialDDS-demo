#!/usr/bin/env bash
# Capture the awkward case on purpose: a dispose with no tombstone in front of
# it. This is the exhibit for docs/removal-two-doors-finding.md and it is NOT
# evidence of the demo working -- the main recording is deliberately kept free
# of this shape, because a removal the recording loader drops would make that
# artifact contradict itself.
#
#   docker run --rm -v "$PWD:/app" -w /app cyclonedds-python \
#       bash tools/capture_dispose_divergence.sh
#
# `gnome_publisher` disposes its entity on SIGTERM without ever marking it
# RETIRED, which is correct behaviour -- a visitor's ornament leaving is not a
# venue retiring an entity -- and is exactly the shape the two doors disagree
# about. So the ordering here is the inverse of the main capture's: the gnome
# is killed *inside* the window, on purpose.
set -u
export SPATIALDDS_TRANSPORT=dds
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
export SPATIALDDS_DDS_DOMAIN="${SPATIALDDS_DDS_DOMAIN:-52}"
export PYTHONPATH=/app
DOMAIN="$SPATIALDDS_DDS_DOMAIN"
OUT="${OUT:-/app/recordings/dispose-without-tombstone.mcap}"
WINDOW="${WINDOW:-16}"
LOG=/tmp/divergence
mkdir -p $LOG
cd /app

say () { echo "divergence: $*"; }

python3 -c "import mcap" 2>/dev/null || python3 -m pip install --quiet "mcap>=1.1.0"

say "domain $DOMAIN, window ${WINDOW}s, out $OUT"
python3 -m spatialdds_demo.model_service > $LOG/model.log 2>&1 &  MODEL=$!
python3 scripts/gnome_publisher.py       > $LOG/gnome.log 2>&1 &  GNOME=$!
sleep 7
grep -q "latched, holding" $LOG/gnome.log || { say "gnome never latched"; cat $LOG/gnome.log; exit 1; }
say "gnome placed; it will be signalled inside the capture window"

# The gnome leaves mid-window: SIGTERM, its own dispose, no tombstone.
( sleep 7; say "SIGTERM to the gnome — dispose, no tombstone"; kill $GNOME ) &
SIG=$!

python3 tools/record_owm_session.py --seconds "$WINDOW" --domain "$DOMAIN" \
    --out "$OUT" \
    --driver "tools/capture_dispose_divergence.sh (the finding's exhibit)"
RC=$?

wait $SIG 2>/dev/null
kill $MODEL 2>/dev/null; wait $MODEL 2>/dev/null
say "gnome log:"; sed 's/^/  /' $LOG/gnome.log | tail -3
exit $RC
