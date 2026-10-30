"""Watch a world populated over HTTP: what a subscriber sees, as it arrives.

The other half of examples/world_from_curl.sh. One WebSocket per lane on the
web binding (Appendix N.5), each printing samples and disposes with the
lifecycle members that say what happened: a lapse arrives as RETIRED with
"refresh lapsed" and then its dispose, a DELETE the same with its reason.
Only `websockets` is needed; nothing from this repository is imported.

    export SPATIALDDS_ENDPOINT=http://127.0.0.1:8787 SPATIALDDS_TOKEN=<token>
    python3 examples/watch_world.py [seconds]
"""
import json, os, sys, threading, time
from urllib.request import Request, urlopen
from websockets.sync.client import connect

E = os.environ.get("SPATIALDDS_ENDPOINT") or "http://127.0.0.1:8787"
SCENE = os.environ.get("WORLD_SCENE") or "curl-world"
AUTH = {"Authorization": f"Bearer {os.environ.get('SPATIALDDS_TOKEN', '')}"}
UNTIL = time.time() + float(sys.argv[1]) if len(sys.argv) > 1 else float("inf")
T0 = time.time()


def exists(path: str) -> bool:
    try:
        urlopen(Request(E + path, headers=AUTH), timeout=2).close()
        return True
    except OSError:
        return False


def watch(lane: str, path: str) -> None:
    # A lane exists once something is put on it. Wait for it with a GET, not
    # by retrying the socket: websockets' sync client, in a thread, takes its
    # 10 s close timeout to report a refused handshake (measured, 16.0).
    while not exists(path):
        if time.time() > UNTIL:
            return
        time.sleep(0.2)
    with connect((E + path).replace("http", "ws", 1),
                 additional_headers=AUTH) as ws:
        while time.time() < UNTIL:
            try:
                m = json.loads(ws.recv(timeout=0.5))
            except TimeoutError:
                continue
            ev, d = m.get("event"), m.get("data") or {}
            what = (f"state={d.get('state')} reason={d.get('state_reason')!r}"
                    + (f" asserter={d['asserter']!r}" if "asserter" in d else "")
                    if ev == "sample" else f"reason={m.get('reason')!r}"
                    if ev == "dispose" else json.dumps(m)[:60])
            print(f"{time.time() - T0:6.1f}s {lane:12} {ev:8} "
                  f"{m.get('key', ''):12} {what}", flush=True)


lanes = {"entity": f"/{SCENE}/world/entity/v1",
         "relationship": f"/{SCENE}/world/oarc.relationship/v1"}
threads = [threading.Thread(target=watch, args=kv, daemon=True)
           for kv in lanes.items()]
for t in threads:
    t.start()
for t in threads:
    t.join()
