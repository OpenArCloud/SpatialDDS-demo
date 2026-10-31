#!/usr/bin/env python3
"""mmwave-ut-04: the roadside sensing unit, and every track it holds.

    python3 -m scenarios.crossing.mmwave --domain 47 --port 8871

**Emulated, and labelled so on every record.** The scene stands in for a 6G
integrated sensing-and-communication roadside unit: a pole-mounted sensor
between the crosswalk and the level crossing, high enough to see over the
traffic. Its tracks are ground truth filtered by range and line of sight
(`visibility.py`), with no radio model, no noise and no misses, and each
track says that in `demo.emulation`.

OBSERVED, each track, keyed `ent:mmwave-ut-04:<actor>`: the unit's own
record of what it sees, beside (never instead of) anyone else's. A track that
stops being seen goes UNOBSERVED with what hides it, and is withdrawn,
"track lost", after `LOST_AFTER_S` without a sighting; seen again inside
that, it is ACTIVE again on the same id. The unit itself is a DECLARED,
static entity at its mount.
"""
from __future__ import annotations

from typing import Dict

import spatialdds as sd
from spatialdds.dds.types.spatial.owm import Basis, LifecycleState, ModelLayer

from scenarios.crossing import participant as runner
from scenarios.crossing.scene import (
    SCENARIO, Publisher, entity, entity_id, footprint_extent,
    oriented_dims)
from scenarios.crossing.visibility import Sight

UNIT = SCENARIO["sensing_unit"]
NAME = UNIT["id"]
LOST_AFTER_S = 2.0


class SensingUnit:
    def __init__(self, p: "sd.dds.Participant") -> None:
        self.pub = Publisher(p)
        self.sight = Sight(SCENARIO["buildings"])
        self.mount = tuple(UNIT["position"])
        self.range_m = float(UNIT["range_m"])
        self._last_seen: Dict[str, float] = {}
        self._last: Dict[str, dict] = {}

    def on_start(self, stamp) -> None:
        x, y, z = self.mount
        self.pub.assert_entity(entity(
            entity_id(NAME, "self"), Basis.DECLARED, "roadside_unit",
            source=self.pub.source, stamp=stamp, layer=ModelLayer.STATIC,
            pose=(x, y, z, 0.0),
            properties=[("demo.label", NAME), ("demo.range_m", f"{self.range_m:g}"),
                        ("demo.emulation", UNIT["label"])]),
            now=0.0, moving=False)

    def _track(self, aid: str, a: dict, stamp, state=LifecycleState.ACTIVE,
               reason: str = ""):
        return entity(
            entity_id(NAME, aid), Basis.OBSERVED, a["kind"],
            source=self.pub.source, stamp=stamp,
            pose=(a["x"], a["y"], a["z"], a["yaw_deg"]),
            extent=footprint_extent(a["x"], a["y"], a["z"], a["yaw_deg"],
                                    a["length"], a["width"], a["height"]),
            properties=[("demo.label", aid), ("demo.emulation", UNIT["label"]),
                        *oriented_dims(a["length"], a["width"], a["height"])],
            state=state, state_reason=reason)

    def on_truth(self, frame, stamp) -> None:
        t, actors = frame["t"], frame["actors"]
        seen = self.sight.visible(self.mount, self.range_m, actors)
        for aid, blocked in seen.items():
            if blocked is None:
                self._last_seen[aid] = t
                self._last[aid] = actors[aid]
                self.pub.assert_entity(self._track(aid, actors[aid], stamp),
                                       now=t, moving=True)
        for aid in list(self._last_seen):
            if self._last_seen[aid] == t:
                continue
            eid = entity_id(NAME, aid)
            if t - self._last_seen[aid] >= LOST_AFTER_S or aid in frame["arrived"]:
                self.pub.withdraw(eid, "track lost")
                del self._last_seen[aid], self._last[aid]
                continue
            held = self.pub.held().get(eid)
            if held is not None and held.state == LifecycleState.ACTIVE:
                why = (f"out of view: behind {seen[aid]}" if seen.get(aid)
                       else "out of range")
                # The last place it was seen, not where the world now has it.
                self.pub.assert_entity(self._track(
                    aid, self._last[aid], stamp, LifecycleState.UNOBSERVED, why),
                    now=t, moving=False)
        self.pub.settle(t)


if __name__ == "__main__":
    runner.main_for(NAME, SensingUnit, __doc__)
