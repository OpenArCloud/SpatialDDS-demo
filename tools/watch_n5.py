#!/usr/bin/env python3
"""Watch the bridge's downstream lanes over N.5, as a web client would.

    python3 tools/watch_n5.py --endpoint http://127.0.0.1:8089 \
        --token <tok> --seconds 70 --out observed.json

One socket per resource, which is what the binding models: N.5 has no pattern
subscribe, so a client that wants two lanes opens two sockets and each carries
its own latched replay and its own `end_of_replay` marker. The bridge census
recorded that as the browser's real rewiring cost -- it had been asking for
`spatialdds/model/*` on one socket, which the binding has no equivalent for.

What this records is the *observed* downstream lane: what a web subscriber
actually received while the session ran, which is the only evidence that the
live half works. A collection GET proves state; only a socket proves liveness.
"""
import argparse, asyncio, json, sys
from collections import Counter
from pathlib import Path
import aiohttp

ap = argparse.ArgumentParser()
ap.add_argument("--endpoint", required=True)
ap.add_argument("--token", required=True)
ap.add_argument("--seconds", type=float, default=70.0)
ap.add_argument("--out", type=Path, default=None)
a = ap.parse_args()
AUTH = {"Authorization": f"Bearer {a.token}"}


async def watch(session, url, seconds, events):
    ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
    async with session.ws_connect(ws_url, heartbeat=None) as ws:
        deadline = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=2)
            except asyncio.TimeoutError:
                continue
            if msg.type is not aiohttp.WSMsgType.TEXT:
                break
            events.append(json.loads(msg.data))


async def main():
    async with aiohttp.ClientSession(headers=AUTH) as s:
        async with s.get(f"{a.endpoint}/.well-known/spatialdds/search") as r:
            search = await r.json()
        topics = [t for res in search["results"]
                  for t in res["service"]["topics"]]
        # The downstream lanes: entities and poses. The command lane is
        # upstream and watching it would only show this session's own POSTs
        # coming back, which proves nothing about the downstream half.
        lanes = [t for t in topics if t["type"] in ("entity", "model_pose")]
        print(f"watching {len(lanes)} lane(s), one socket each:")
        for t in lanes:
            print(f"  {t['type']:12} {t['durability']:16} {t['url'].split('/',3)[-1]}")
        per: dict[str, list] = {t["type"]: [] for t in lanes}
        await asyncio.gather(*(watch(s, t["url"], a.seconds, per[t["type"]])
                               for t in lanes))

    report = {}
    for kind, evs in per.items():
        kinds = Counter(e.get("event") for e in evs)
        keys = Counter(e.get("key") for e in evs if e.get("key"))
        marker = [e for e in evs if e.get("event") == "end_of_replay"]
        report[kind] = {
            "events": len(evs),
            "by_event": dict(kinds),
            "by_key": dict(keys),
            "end_of_replay_markers": len(marker),
            "replayed_count_claimed": marker[0].get("replayed") if marker else None,
        }
        print(f"\n{kind}: {len(evs)} event(s) {dict(kinds)}")
        for k, n in keys.most_common():
            print(f"    {n:5d}  {k}")
    if a.out:
        a.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {a.out}")
    live = sum(r["by_event"].get("sample", 0) for r in report.values())
    if live == 0:
        print("\nNOTHING was observed on any lane: the downstream half did "
              "not work, and an empty observation is not evidence that it did")
        sys.exit(1)

asyncio.run(main())
