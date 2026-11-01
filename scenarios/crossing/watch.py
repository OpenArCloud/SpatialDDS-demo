#!/usr/bin/env python3
"""A live subscriber to the scene's claims: every event on the relationship lane.

    python3 -m scenarios.crossing.watch --domain 47 --out claims.watch.jsonl

What a consumer on the bus sees of the claims as they happen, written one
JSON object per line as it arrives: each sample, in the web binding's JSON
form (`spatialdds.dds.codec.to_json`), each dispose with its reason, and each
liveliness change, every one with the wall time it was taken. The recording
holds the same samples; this is the other witness, a reader that was live,
and the one the claims' lifecycles are quoted from. SIGTERM ends it.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

import spatialdds as sd
from spatialdds.dds.codec import to_json

from spatialdds_demo import claims
from spatialdds_demo.lanes import MODEL_LATCHED, RELATIONSHIP
from scenarios.crossing.scene import SCENE, service_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--domain", type=int, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=600.0,
                    help="the longest to watch; SIGTERM ends it sooner")
    args = ap.parse_args()
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(1))
    p = sd.participant(SCENE, domain=args.domain, service_id=service_id("watch"))
    reader = p.reader(RELATIONSHIP, stream=claims.STREAM, qos_profile=MODEL_LATCHED)
    n = 0
    started = time.time()
    with open(args.out, "w") as f:
        while not stopping and time.time() < started + args.seconds:
            for ev in reader.take():
                # Anything else the reader yields (the SDK's end-of-replay
                # marker) is not an event on the lane.
                if not isinstance(ev, (sd.Sample, sd.Dispose, sd.Liveliness)):
                    continue
                row = {"wall": time.time(), "topic": ev.topic, "key": ev.key}
                if isinstance(ev, sd.Sample):
                    row.update(event="sample", data=to_json(ev.data))
                elif isinstance(ev, sd.Dispose):
                    row.update(event="dispose", reason=ev.reason)
                else:
                    row.update(event="liveliness", alive=ev.alive)
                f.write(json.dumps(row) + "\n")
                f.flush()
                n += 1
            time.sleep(0.01)
    p.close()
    print(f"watch: {n} events on {reader.topic}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
