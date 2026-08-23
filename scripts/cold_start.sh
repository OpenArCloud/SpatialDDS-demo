#!/usr/bin/env bash
#
# The GAA cold-start path, with no SpatialDDS client code.
#
#   bootstrap -> search -> pick a manifest -> /ws subscribe -> live data
#
# Everything here is curl, plus one websocket-client call for the last hop.
# Nothing imports the demo, the IDL, or CycloneDDS: the point is that a client
# holding only an authority name can reach live data through the spec's own
# well-known endpoints.
#
# Bring the stack up first (bridge on :8088, VPS and catalogue behind it):
#
#     ./run_bridge_server_docker.sh
#
# then, from the repo root:
#
#     scripts/cold_start.sh                                  # Austin, the stack's default
#     BRIDGE=http://host:8088 GEOHASH=9q8yy scripts/cold_start.sh
#
set -euo pipefail

BRIDGE="${BRIDGE:-http://127.0.0.1:8088}"
GEOHASH="${GEOHASH:-9v6kr}"      # downtown Austin; 9q8yy is downtown SF
WS="${WS:-$(printf '%s' "$BRIDGE" | sed -e 's|^http|ws|')/ws}"
TIMEOUT="${TIMEOUT:-30}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1. Bootstrap — which bus, and where"
curl -fsS "$BRIDGE/.well-known/spatialdds/bootstrap" | tee /tmp/cold_start_bootstrap.json
echo
DOMAIN=$(python3 -c 'import json;print(json.load(open("/tmp/cold_start_bootstrap.json"))["domain_id"])')

say "2. Search — who covers geohash $GEOHASH"
# The GET convenience form (§3.3.0), equivalent to POST {"geohash": "..."}.
curl -fsS "$BRIDGE/.well-known/spatialdds/search?geohash=$GEOHASH" \
  > /tmp/cold_start_search.json
python3 - <<'STEP2'
import json
body = json.load(open("/tmp/cold_start_search.json"))
print(f'{len(body["results"])} service manifest(s), next_page_token={body["next_page_token"]!r}')
for doc in body["results"]:
    svc = doc["service"]
    print(f'  {svc["service_id"]:32} {svc["kind"]:10} {doc["id"]}')
    for topic in svc.get("topics", []):
        print(f'      {topic["name"]:32} {topic["type"]:12} {topic["qos_profile"]}')
STEP2

say "3. Pick a manifest and the topics it advertises"
python3 - > /tmp/cold_start_pick.txt <<'STEP3'
import json, os, sys
for doc in json.load(open("/tmp/cold_start_search.json"))["results"]:
    topics = doc["service"].get("topics") or []
    if not topics:
        continue
    # A glob over the service's own topics, built from the manifest rather
    # than from anything this script knows about SpatialDDS.
    prefix = os.path.commonprefix([t["name"] for t in topics]).rsplit("/", 1)[0]
    print(doc["service"]["service_id"], prefix + "/*")
    sys.exit(0)
sys.exit("no service in this cell advertises a topic")
STEP3
read -r SERVICE_ID PATTERN < /tmp/cold_start_pick.txt
echo "service:    $SERVICE_ID"
echo "subscribe:  $PATTERN"
echo "dds domain: $DOMAIN  (from bootstrap — a manifest synthesized from an"
echo "                      announce carries no service.connection)"

say "4. Subscribe over /ws, use the service, take the sample"
# A VPS answers requests; it does not stream. So the subscription goes up
# first, the request goes in through the bridge's REST helper, and the reply
# arrives on the topic the manifest advertised. Still no SpatialDDS client
# code: curl in, JSON out.
SDDS_WS="$WS" SDDS_PATTERN="$PATTERN" SDDS_SERVICE="$SERVICE_ID" \
SDDS_BRIDGE="$BRIDGE" SDDS_TIMEOUT="$TIMEOUT" python3 - <<'STEP4'
import json, os, subprocess, sys, time
import websocket                      # pip install websocket-client

ws = websocket.create_connection(os.environ["SDDS_WS"], timeout=5)
ws.send(json.dumps({"type": "subscribe", "id": "cold_start",
                    "pattern": os.environ["SDDS_PATTERN"]}))
print("->", json.loads(ws.recv()))

request = json.dumps({"service_id": os.environ["SDDS_SERVICE"]})
print(f'-> curl -X POST {os.environ["SDDS_BRIDGE"]}/v1/localize -d {request}')
subprocess.run(["curl", "-fsS", "-X", "POST",
                os.environ["SDDS_BRIDGE"] + "/v1/localize",
                "-H", "Content-Type: application/json", "-d", request],
               check=True, stdout=subprocess.DEVNULL)

# Both halves of the exchange come back over the same subscription: the
# query this script triggered, then the service's answer. (The query appears
# twice — once as the bridge's own tx event for its dashboard, once as the
# sample it read back off the bus.)
WANTED = int(os.environ.get("SDDS_SAMPLES", "4"))
seen = 0
deadline = time.time() + float(os.environ["SDDS_TIMEOUT"])
while time.time() < deadline and seen < WANTED:
    try:
        msg = json.loads(ws.recv())
    except websocket.WebSocketTimeoutException:
        continue
    if msg.get("type") != "data":
        continue
    seen += 1
    print(f'<- data on {msg["logical_topic"]} ({msg["msg_type"]})')
    payload = msg["payload"]
    keys = ", ".join(list(payload)[:8]) if isinstance(payload, dict) else ""
    print(f'   fields: {keys}')
    print("   " + json.dumps(payload)[:240] + " ...")
ws.close()
if not seen:
    sys.exit("no sample on " + os.environ["SDDS_PATTERN"] +
             " within " + os.environ["SDDS_TIMEOUT"] + "s")
STEP4

say "Cold start complete — bootstrap to live data, no SpatialDDS client code."
