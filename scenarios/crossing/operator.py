#!/usr/bin/env python3
"""A fleet operator: its own vehicles, where they are.

    python3 -m scenarios.crossing.operator --name operator-a --domain 47 --port 8871

OBSERVED: an operator knows where its vehicles are from the vehicles
themselves (positioning reported by each car), which is an observation, not a
declaration. The fleet is the scenario's (`fleets` in scenario.json); a car
the operator does not run is not its to publish. A vehicle that leaves the
street is withdrawn, "left the scene".
"""
from __future__ import annotations

import sys

import spatialdds as sd
from spatialdds.dds.types.spatial.owm import Basis

from scenarios.crossing import participant as runner
from scenarios.crossing.scene import (
    SCENARIO, Publisher, entity, entity_id, footprint_extent,
    oriented_dims)


class Operator:
    def __init__(self, name: str, p: "sd.dds.Participant") -> None:
        self.name = name
        self.fleet = set(SCENARIO["fleets"][name])
        self.protagonists = set(SCENARIO["protagonists"])
        self.pub = Publisher(p)

    def on_truth(self, frame, stamp) -> None:
        t = frame["t"]
        for vid, a in frame["actors"].items():
            if vid not in self.fleet:
                continue
            self.pub.assert_entity(entity(
                entity_id(self.name, vid), Basis.OBSERVED, a["kind"],
                source=self.pub.source, stamp=stamp,
                pose=(a["x"], a["y"], a["z"], a["yaw_deg"]),
                extent=footprint_extent(a["x"], a["y"], a["z"], a["yaw_deg"],
                                        a["length"], a["width"], a["height"]),
                properties=[("demo.label", vid),
                            ("demo.role", "protagonist" if vid in self.protagonists
                             else "fleet"),
                            *oriented_dims(a["length"], a["width"], a["height"])]),
                now=t, moving=True)
        for vid in frame["arrived"]:
            if vid in self.fleet:
                self.pub.withdraw(entity_id(self.name, vid), "left the scene")
        self.pub.settle(t)


def main() -> None:
    ap = runner.parser(__doc__)
    ap.add_argument("--name", required=True, choices=sorted(SCENARIO["fleets"]))
    args = ap.parse_args()
    sys.exit(runner.run(args.name, lambda p: Operator(args.name, p), args))


if __name__ == "__main__":
    main()
