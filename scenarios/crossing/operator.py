#!/usr/bin/env python3
"""A fleet operator: its own vehicles, and what they can see.

    python3 -m scenarios.crossing.operator --name operator-a --domain 47 --port 8871

**Its fleet, OBSERVED.** An operator knows where its vehicles are from the
vehicles themselves (positioning reported by each car), which is an
observation, not a declaration. The fleet is the scenario's (`fleets` in
scenario.json); a car the operator does not run is not its to publish. A
vehicle that leaves the street is withdrawn, "left the scene".

**What its fleet sees, OBSERVED, through the visibility model.** Each fleet
car carries a sensor at its centre, `operator_sensing.sensor_height_m` up,
with `operator_sensing.range_m` of range; what it sees is what `visibility.py`
says a straight sight line reaches, past buildings and buses and trains. The
operator publishes one record per thing any of its cars sees,
`ent:<operator>:<thing>`, with `demo.seen_by` naming which of its cars see it
this step (sorted, comma-joined; empty once none does). A thing no car sees
goes UNOBSERVED and is withdrawn, "track lost", by the same rule as the
roadside unit's tracks (`tracks.py`). Its own cars are not perceived: it
already publishes where they are.

Emulated, and labelled so on every perceived record (`demo.emulation`).
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Tuple

import spatialdds as sd
from spatialdds.dds.types.spatial.owm import Basis

from scenarios.crossing import participant as runner
from scenarios.crossing.scene import (
    SCENARIO, Publisher, entity, entity_id, footprint_extent,
    oriented_dims)
from scenarios.crossing.tracks import Tracks
from scenarios.crossing.visibility import Sight

SENSING = SCENARIO["operator_sensing"]
EMULATION = SENSING["label"]


def fleet_view(sight: Sight, fleet, actors: Dict[str, dict], range_m: float,
               height_m: float) -> Dict[str, Dict[str, Optional[str]]]:
    """Per fleet car on the street, what it sees: each actor in its range
    mapped to None if seen or to what blocks it. The operator's own cars are
    neither seen nor occluders."""
    out = {}
    own = [v for v in fleet if v in actors]
    for car in own:
        a = actors[car]
        others = {k: v for k, v in actors.items() if k not in fleet}
        out[car] = sight.visible((a["x"], a["y"], a["z"] + height_m), range_m,
                                 others)
    return out


def merge(views: Dict[str, Dict[str, Optional[str]]]
          ) -> Tuple[Dict[str, Optional[str]], Dict[str, List[str]]]:
    """The fleet's union: seen if any car sees it, else the first blocker
    any car reports; and, per thing, which cars see it."""
    seen: Dict[str, Optional[str]] = {}
    by: Dict[str, List[str]] = {}
    for car in sorted(views):
        for aid, blocked in views[car].items():
            if blocked is None:
                seen[aid] = None
                by.setdefault(aid, []).append(car)
            elif aid not in seen:
                seen[aid] = blocked
    return seen, by


class Operator:
    def __init__(self, name: str, p: "sd.dds.Participant") -> None:
        self.name = name
        self.fleet = set(SCENARIO["fleets"][name])
        self.protagonists = set(SCENARIO["protagonists"])
        self.pub = Publisher(p)
        self.sight = Sight(SCENARIO["buildings"])
        self.range_m = float(SENSING["range_m"])
        self.height_m = float(SENSING["sensor_height_m"])
        self._seen_by: Dict[str, List[str]] = {}
        self.perceived = Tracks(
            self.pub, lambda aid: entity_id(self.name, aid),
            props=lambda aid, a: [
                ("demo.seen_by", ",".join(self._seen_by.get(aid, []))),
                ("demo.emulation", EMULATION)])

    def on_truth(self, frame, stamp) -> None:
        t, actors = frame["t"], frame["actors"]
        for vid, a in actors.items():
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
        seen, self._seen_by = merge(fleet_view(
            self.sight, self.fleet, actors, self.range_m, self.height_m))
        # settle() is the tracker's last act, for the fleet's records too.
        self.perceived.update(t, stamp, actors, seen, frame["arrived"])


def main() -> None:
    ap = runner.parser(__doc__)
    ap.add_argument("--name", required=True, choices=sorted(SCENARIO["fleets"]))
    args = ap.parse_args()
    sys.exit(runner.run(args.name, lambda p: Operator(args.name, p), args))


if __name__ == "__main__":
    main()
