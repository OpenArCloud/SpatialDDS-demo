#!/usr/bin/env bash
# A world from curl: populate, watch, lapse, withdraw.
#
# spatialdds 0.8.0's write surface puts living things into a world over plain
# HTTP: an entity, a second entity somebody is watching, and a claim between
# them, with this repository's own OWM 0.2 relationship type as the claim.
# Nothing here is DDS and nothing from this repository runs on the client
# side; the gateway it talks to is `python -m spatialdds.serve`.
#
#   1. populate  a DECLARED bench, an OBSERVED cyclist (refused without
#                ?refresh=, accepted with it), and a "near" relationship
#                whose asserter is left empty for the gateway to stamp
#   2. refresh   the cyclist re-PUT inside its interval, a few times
#   3. lapse     the refreshing stops; the gateway withdraws the cyclist
#                ("refresh lapsed"), and the bench is still there
#   4. withdraw  the relationship and the bench, each DELETE with its reason
#
# Run a gateway that serves the relationship type and, to see the world on a
# DDS bus as well, joins a domain:
#
#   PYTHONPATH=. python -m spatialdds.tokens --file tokens.json \
#       add me --provider example.org --scene curl-world \
#       --may-create-scenes                                  # prints a token
#   PYTHONPATH=. python -m spatialdds.serve --publish --tokens tokens.json \
#       --extension spatialdds_demo.lanes --dds-domain 0
#
# Then, in one terminal, watch, and in another, run this:
#
#   export SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 SPATIALDDS_TOKEN=<token>
#   python3 examples/watch_world.py
#   examples/world_from_curl.sh
#
# Needs spatialdds>=0.8.0 on the gateway, curl here. The spec is
# tests/test_world_from_curl.py, which runs exactly this.
set -euo pipefail

E="${SPATIALDDS_ENDPOINT:-http://127.0.0.1:8787}"
: "${SPATIALDDS_TOKEN:?export SPATIALDDS_TOKEN, a token whose principal may write the scene}"
SCENE="${WORLD_SCENE:-curl-world}"
REFRESH="${WORLD_REFRESH:-5}"          # seconds the cyclist promises to re-PUT within
ROUNDS="${WORLD_ROUNDS:-3}"            # refreshes before the client goes quiet

ENT="$E/$SCENE/world/entity/v1"
REL="$E/$SCENE/world/oarc.relationship/v1"
H=(-H "Authorization: Bearer $SPATIALDDS_TOKEN" -H "Content-Type: application/json")

# sd.frame("map/curl-world", convention="ENU", meters_per_unit=1.0)
FRAME='{"uuid": "b091a874-9449-550c-90ed-e1fb4a3b7648", "fqn": "map/curl-world",
  "has_coord_convention": true, "coord_convention": "ENU", "has_scale": true,
  "scale_status": "SCALE_DECLARED", "meters_per_unit": 1.0, "display_unit": "m"}'

stamp() { printf '{"sec": "%s", "nanosec": 0}' "$(date +%s)"; }

entity() {  # id basis x y label
  cat <<EOF
{"entity_id": "$1", "basis": "$2", "type_uris": ["https://example.org/$5"],
 "layer": "FAST", "frame_ref": $FRAME,
 "has_pose": true, "pose": {"t": [$3, $4, 0.0], "q": [0.0, 0.0, 0.0, 1.0]},
 "has_extent": false, "extent": {"min_xyz": [0.0, 0.0, 0.0], "max_xyz": [0.0, 0.0, 0.0]},
 "properties": [{"key": "demo.label", "value": "$5"}], "external_refs": [],
 "content_refs": [], "state": "ACTIVE", "state_reason": "",
 "source_id": "svc:curl-world:me", "stamp": $(stamp)}
EOF
}

relationship() {  # id subject predicate object
  cat <<EOF
{"id": "$1", "subject_ref": "$2", "predicate": "$3", "object_ref": "$4",
 "basis": "DECLARED", "asserter": "",
 "has_confidence": false, "confidence": 0.0, "derived_from": [],
 "stamp": $(stamp), "has_valid_from": false, "valid_from": {"sec": "0", "nanosec": 0},
 "has_valid_until": false, "valid_until": {"sec": "0", "nanosec": 0},
 "meta": [], "state": "CREATED", "state_reason": ""}
EOF
}

# One request, one line: the step, what, the status, and the problem type or
# the field worth seeing.
call() {  # step what method url [body]
  local step=$1 what=$2 method=$3 url=$4 body=${5:-} out code
  out=$(mktemp)
  if [ -n "$body" ]; then
    code=$(curl -s -o "$out" -w '%{http_code}' -X "$method" "${H[@]}" --data "$body" "$url")
  else
    code=$(curl -s -o "$out" -w '%{http_code}' -X "$method" "${H[@]}" "$url")
  fi
  local note
  note=$(python3 -c 'import json,sys
try: d = json.load(open(sys.argv[1]))
except Exception: d = {}
d = d if isinstance(d, dict) else {}
a = "asserter=" + repr(d["asserter"]) if "asserter" in d else ""
print(d.get("type") or a)' "$out")
  rm -f "$out"
  printf '%-9s %-34s %s %s\n' "$step" "$what" "$code" "$note"
}

echo "world from curl: $E, scene $SCENE, cyclist refresh ${REFRESH}s"

call populate "bench (DECLARED)" PUT "$ENT/ent:bench" "$(entity ent:bench DECLARED 3.0 4.0 bench)"
call populate "cyclist (OBSERVED), no ?refresh=" PUT "$ENT/ent:cyclist" \
     "$(entity ent:cyclist OBSERVED 0.0 0.0 cyclist)"
call populate "cyclist (OBSERVED), ?refresh=$REFRESH" PUT "$ENT/ent:cyclist?refresh=$REFRESH" \
     "$(entity ent:cyclist OBSERVED 0.0 0.0 cyclist)"
call populate "near: cyclist -> bench" PUT "$REL/near-1" \
     "$(relationship near-1 ent:cyclist https://example.org/predicate/near ent:bench)"
call populate "the relationship, as served" GET "$REL/near-1"

half=$(python3 -c "print($REFRESH / 2)")
for i in $(seq 1 "$ROUNDS"); do
  sleep "$half"
  x=$(python3 -c "print(0.5 * $i)")
  call refresh "cyclist, round $i" PUT "$ENT/ent:cyclist?refresh=$REFRESH" \
       "$(entity ent:cyclist OBSERVED "$x" 0.0 cyclist)"
done

wait_s=$(python3 -c "print($REFRESH + 2)")
echo "lapse     ... the cyclist's client goes quiet; waiting ${wait_s}s"
sleep "$wait_s"
call lapse "cyclist, after its interval" GET "$ENT/ent:cyclist"
call lapse "bench, under the same silence" GET "$ENT/ent:bench"

call withdraw "near-1" DELETE "$REL/near-1?reason=cyclist%20left"
call withdraw "bench" DELETE "$ENT/ent:bench?reason=bench%20removed%20for%20repainting"
call withdraw "bench, after" GET "$ENT/ent:bench"
