#!/usr/bin/env python3
"""Validate everything the endpoint serves against the schemas it publishes.

    python3 tools/validate_served_owm.py --endpoint http://127.0.0.1:8831 \
        --token <bearer>

The backend-blind conformance suite validates the endpoint's *latched*
instances, which for this recording is the six entities. It does not reach the
pose and command lanes: those are VOLATILE, so they have no instance paths and
appear only as live events. That leaves the two busiest types in the recording
unvalidated by the suite, and "the migration is conformant" would then be a
claim about a third of its traffic.

So this subscribes to every resource the endpoint advertises -- the N.5
WebSocket on the resource path, which replays the latched set, sends one
`end_of_replay` marker and then the live events -- and validates each payload
against the schema the endpoint publishes for its type, fetched over HTTP as a
consumer would fetch it. The schemas come from the
endpoint rather than from a checkout on purpose: a schema read off disk proves
the generator and the gateway agree, not that a consumer with nothing but the
endpoint's URL can check what it is handed.

Exit 1 on any failure, and on zero payloads validated -- an empty pass is not
a pass.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import aiohttp

try:
    import jsonschema
except ImportError:                                   # pragma: no cover
    print("validate: needs jsonschema (pip install jsonschema)")
    sys.exit(2)

WELL_KNOWN = "/.well-known/spatialdds"


def _get(url: str, token: str | None, accept: str = "application/json"):
    req = urllib.request.Request(url, headers={"Accept": accept})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(), dict(r.headers)


async def _subscribe(url: str, token: str | None,
                     settle: float = 2.0,
                     window: float | None = None) -> list[dict]:
    """Every event on the N.5 subscription, replay and live both.

    A recording's live events are sent as fast as the socket takes them and
    then the topic goes quiet, so "the stream is finished" is a silence rather
    than a close. `settle` is how long a silence has to last before this stops
    reading; the marker tells us the replay is complete, nothing tells us the
    live window is.

    **Against a live endpoint a silence means nothing.** A venue whose ducks
    move six times a second still has gaps, and a lane that is quiet right now
    may be busy in a second. Run against a live bridge with the default
    settle, this reported zero payloads on two lanes that an N.5 subscriber
    had just observed 323 samples on -- a count that was wrong rather than
    small. So `window` reads for a fixed duration instead of stopping on
    silence, and the report says which bound it used: against a live backend
    the count is a **floor, not a total**, and the difference has to be on the
    page.
    """
    events: list[dict] = []
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
    deadline = None if window is None else (
        asyncio.get_event_loop().time() + window)
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.ws_connect(ws_url, heartbeat=None) as ws:
            while True:
                if deadline is not None and \
                        asyncio.get_event_loop().time() >= deadline:
                    break
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=settle)
                except asyncio.TimeoutError:
                    if deadline is None:
                        break
                    continue              # a live lane may simply be quiet
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    break
                events.append(json.loads(msg.data))
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "",
                                 formatter_class=argparse
                                 .RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--token", default=None)
    ap.add_argument("--out", type=Path, default=None,
                    help="write the report here as well as to stdout")
    ap.add_argument("--window", type=float, default=None,
                    help="read each lane for this many seconds instead of "
                         "stopping at the first silence. Required against a "
                         "live endpoint, where a quiet lane is not a finished "
                         "one; with it the counts are a floor, not a total, "
                         "and the report says so.")
    args = ap.parse_args()
    base = args.endpoint.rstrip("/")

    index = json.loads(_get(f"{base}{WELL_KNOWN}/schemas", args.token)[0])
    schemas: dict[str, dict] = {}
    for row in index:
        body, _ = _get(row["url"], args.token)
        schemas[row["type"]] = json.loads(body)
    print(f"validate: {len(schemas)} schemas published by the endpoint")

    search = json.loads(_get(f"{base}{WELL_KNOWN}/search", args.token)[0])
    topics: list[dict] = []
    for row in search.get("results", []):
        topics.extend(row.get("service", {}).get("topics", []))
    if not topics:
        print("validate: the endpoint advertises no topics")
        return 1

    lines: list[str] = []
    counts: Counter = Counter()
    failures: list[str] = []

    for topic in sorted(topics, key=lambda t: t.get("name", "")):
        path = topic.get("url") or topic.get("name", "")
        if not path.startswith("http"):
            continue
        url = path
        # The advertised `type` is the topic's type *segment* ("model_pose"),
        # not a type name, and deriving an FQN from it would be string
        # arithmetic on a path. The endpoint states the FQN itself, in
        # `X-SpatialDDS-Type` on the resource it serves, so ask it.
        _, hdrs0 = _get(url, args.token)
        fqn = hdrs0.get("X-SpatialDDS-Type", "")
        if not fqn:
            failures.append(f"{url}: no X-SpatialDDS-Type header, so the "
                            f"endpoint does not say what it is serving")
            continue
        schema = schemas.get(fqn)
        if schema is None:
            failures.append(f"{fqn}: the endpoint serves it and publishes "
                            f"no schema for it")
            continue
        validator = jsonschema.Draft202012Validator(schema)

        # The subscription, read as a consumer reads it: the latched replay,
        # the marker, then every live event the recording carried.
        events = asyncio.run(_subscribe(url, args.token, window=args.window))
        markers = [e for e in events if e.get("event") == "end_of_replay"]
        if len(markers) != 1:
            failures.append(f"{fqn}: {len(markers)} end_of_replay markers, "
                            f"N.5 requires exactly one")
        n_ok = 0
        for event in events:
            payload = event.get("data")
            if payload is None:               # dispose, marker, liveliness
                counts[f"{fqn}:non-sample"] += 1
                continue
            errs = sorted(validator.iter_errors(payload),
                          key=lambda e: list(e.path))
            if errs:
                failures.append(
                    f"{fqn} key={event.get('key','')!r}: "
                    f"{errs[0].json_path} {errs[0].message}")
            else:
                n_ok += 1
        counts[fqn] += n_ok
        replayed = sum(1 for e in events[:events.index(markers[0])]
                       if e.get("data") is not None) if markers else 0
        lines.append(f"  {fqn:30} {n_ok:4d} payload(s) validated "
                     f"({replayed} replayed, {n_ok - replayed} live)")

    total = sum(v for k, v in counts.items() if not k.endswith(":non-sample"))
    bound = (f"each lane read for {args.window:g}s (a live endpoint: these "
             f"counts are a FLOOR, not a total)" if args.window else
             "each lane read to its first silence (a recording: the counts "
             "are the whole file)")
    report = ["validated against the endpoint's own published schemas",
              f"  bound: {bound}", ""]
    report += lines
    non = {k: v for k, v in counts.items() if k.endswith(":non-sample")}
    if non:
        report.append(f"  non-sample events (dispose/marker): "
                      f"{sum(non.values())}")
    report.append("")
    if failures:
        report.append(f"FAIL: {len(failures)} payload(s) did not validate")
        report += [f"  {f}" for f in failures[:20]]
    elif total == 0:
        report.append("FAIL: nothing was validated; an empty pass is not a "
                      "pass")
    else:
        report.append(f"OK: {total} payload(s) across {len(lines)} type(s), "
                      f"0 failures")
    text = "\n".join(report)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    return 0 if (not failures and total) else 1


if __name__ == "__main__":
    sys.exit(main())
