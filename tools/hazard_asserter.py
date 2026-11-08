#!/usr/bin/env python3
"""
A scripted hazard asserter: a camera that sees a spill near the pond.

The claims tooth's source (`tools/claims_tooth.sh`, `docs/ros-claims.md`).
No detector: one box, placed where the script says, published through
`spatialdds_ros.perception.ObstaclePublisher`, which is the library's API for
a robot or a camera to say what it sees. It holds one OBSERVED claim about
the box, until it is killed or told to stop:

    python3 tools/hazard_asserter.py --source svc:hazard:plaza-cam

**The claim is true where it is placed, and names only the venue's own
entities.** `ent:<camera>:spill-1 near#5m ent:pond:littlefield`: the spill's
north edge is 2.2 m from the pond's declared box at the default position, so
"within 5 m of the pond" is what a camera looking at both would report. The
near predicate is the demo's own (`spatialdds_demo/claims.py`); nothing is
minted to give the claim an object.

SIGTERM is a clean stop and withdraws ("asserter stopped"); SIGKILL is a
crash and withdraws nothing, which is the arm the tooth is about.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from cyclonedds.domain import DomainParticipant

from spatialdds_ros.config import Config
from spatialdds_ros.perception import ObstaclePublisher

SCENE = "ut-austin-littlefield"
FRAME = "map/ut-littlefield-fountain"
GROUND_Z = -0.84
POND = "ent:pond:littlefield"
#: `claims.near(5.0)`, spelled out so this runs with only the library on
#: the path (the tier's own image has the demo tree, but the tooth's
#: container is told only what it needs).
NEAR_5M = "https://demo.spatialdds.org/owm/0.2/predicate/near#5m"
#: On the robot's eastbound route from its start, (17, -23) to (31, -23).
SPILL = dict(x=24.0, y=-23.0, length=2.0, width=1.6)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True,
                        help="the asserter's service id, e.g. "
                             "svc:hazard:plaza-cam")
    parser.add_argument("--domain", type=int,
                        default=int(os.environ.get("SPATIALDDS_DDS_DOMAIN",
                                                   "1")))
    args = parser.parse_args()
    camera = args.source.split(":")[-1]
    config = Config(scene=SCENE, entity_id=f"ent:camera:{camera}",
                    frame_id=FRAME, source_id=args.source,
                    ground_z=GROUND_Z, domain=args.domain)
    publisher = ObstaclePublisher(DomainParticipant(args.domain), config)

    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append("SIGTERM"))
    signal.signal(signal.SIGINT, lambda *_: stop.append("SIGINT"))

    def sight():
        return publisher.observed("spill-1", SPILL["x"], SPILL["y"],
                                  length=SPILL["length"],
                                  width=SPILL["width"], height=0.05,
                                  type_uri="http://www.wikidata.org/entity/"
                                           "Q223557", label="spill")
    sight()
    claim = publisher.claim("spill-1", NEAR_5M, POND)
    print(f"hazard_asserter: {args.source} holds {claim}: "
          f"{publisher.entity_id('spill-1')} near#5m {POND}, OBSERVED",
          flush=True)
    while not stop:
        sight()
        for note in publisher.tick():
            print(f"hazard_asserter: {note}", flush=True)
        time.sleep(0.1)
    publisher.close(f"{stop[0]}, a clean stop")
    print(f"hazard_asserter: {stop[0]}: withdrew what it held", flush=True)
    time.sleep(0.5)                 # let the terminal samples go out
    return 0


if __name__ == "__main__":
    sys.exit(main())
