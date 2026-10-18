#!/usr/bin/env python3
"""POST a goto to the bridge's N.6 surface, as a web client would.

    python3 tools/post_goto.py --endpoint http://127.0.0.1:8089 \
        --token <tok> --to 12 -21

Deliberately nothing but `requests`. The whole point of the upstream lane is
that a web client needs no DDS and no code from this repository to steer a
robot on the bus -- so this uses the surface the way a browser will, and the
response it prints is the N.6 outcome verbatim.

This is also the test of 0.5.0's stated absence: the package ships the command
surface and no client helper, on the argument that `requests.post(url, json=)`
is the whole of it. If that reads awkwardly here, the absence is wrong and a
helper is 0.5.x material argued from a real consumer.
"""
import argparse, json, sys, time, requests

ap = argparse.ArgumentParser()
ap.add_argument("--endpoint", required=True)
ap.add_argument("--token", required=True)
ap.add_argument("--to", nargs=2, type=float, required=True, metavar=("X", "Y"))
ap.add_argument("--subject", default="ent:robot:tb3")
a = ap.parse_args()

# Find the command lane the way a client should: from the manifest.
auth = {"Authorization": f"Bearer {a.token}"}
search = requests.get(f"{a.endpoint}/.well-known/spatialdds/search",
                      headers=auth, timeout=10).json()
lanes = [t for r in search["results"] for t in r["service"]["topics"]
         if t.get("accepts_commands")]
if not lanes:
    sys.exit("this endpoint advertises no command lane")
url = lanes[0]["url"]

now = time.time()
cmd = {
    "command_id": f"cmd:web:goto-{int(now)}",
    "verb": "goto",
    "subject_id": a.subject,
    "reason": "posted by a web client over N.6",
    "requester_id": "tool:post_goto",
    "has_pose": True,
    "pose": {"t": [a.to[0], a.to[1], -0.84], "q": [0.0, 0.0, 0.0, 1.0]},
    "has_extent": False,
    "extent": {"min_xyz": [0.0, 0.0, 0.0], "max_xyz": [0.0, 0.0, 0.0]},
    # N.1: an int64 is a decimal string on the wire.
    "stamp": {"sec": str(int(now)), "nanosec": int((now % 1) * 1e9)},
}
r = requests.post(url, headers=auth, json=cmd, timeout=15)
print(f"POST {url.split('/', 3)[-1]} -> {r.status_code} {r.headers.get('Content-Type','')}")
print("  " + json.dumps(r.json(), sort_keys=True)[:300])
sys.exit(0 if r.status_code in (200, 202) else 1)
