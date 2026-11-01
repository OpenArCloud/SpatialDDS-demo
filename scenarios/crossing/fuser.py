#!/usr/bin/env python3
"""The fuser: crossing-zone blocked, derived from the gate's declaration.

    python3 -m scenarios.crossing.fuser --domain 47 --port 8871

C5, the minimal truthful set as ruled (docs/crossing-census.md §5): one edge,
`crossing-zone demo:blockedBy gate-9`, DERIVED, asserted when it reads C3
(`gate-9 demo:closes crossing-zone`, DECLARED by gate-ctl-9) and from C3's
own `valid_from`, the arms-down time; retired, "no longer holds", when C3 is,
with C3's `valid_until`. Its `derived_from` names the C3 record it read, by
lane, key and stamp, and keeps naming it: `ClaimWriter` keeps the lineage
that established a claim.

The train is not its object: before train-7 reaches the zone, "blocked by
train-7" is false, and the gate is what blocks the zone from the first
instant. The fuser reads only the bus; the truth feed is its clock and
nothing else (it is a participant like the rest, so it waits for time 0 and
closes at the session's end).
"""
from __future__ import annotations

from typing import Optional

import spatialdds as sd
from spatialdds.dds.types.spatial.owm import Basis

from spatialdds_demo import claims
from spatialdds_demo.lanes import MODEL_LATCHED, RELATIONSHIP
from scenarios.crossing import participant as runner
from scenarios.crossing.scene import (
    DEMO_BLOCKED_BY, DEMO_CLOSES, SCENARIO, decided, entity_id, service_id)

NAME = "fuser"
GATE = SCENARIO["gate"]
GATE_ID = entity_id(GATE["controller"], GATE["entity"])
ZONE_ID = entity_id("map", "crossing-zone")
GATE_ASSERTER = service_id(GATE["controller"])


def is_c3(edge) -> bool:
    """The gate controller's declaration that its gate closes the zone."""
    return (edge.asserter == GATE_ASSERTER and edge.basis == Basis.DECLARED
            and edge.predicate == DEMO_CLOSES and edge.subject_ref == GATE_ID
            and edge.object_ref == ZONE_ID)


class Fuser:
    """C5 from C3. `on_relationship(event)` takes the relationship lane's
    events, `sd.Sample` and `sd.Dispose`, in order."""

    def __init__(self, writer) -> None:
        self.w = writer
        self.c3: Optional[str] = None
        self.log = []
        self.t = 0.0                         # the step it is read at

    def on_relationship(self, ev) -> None:
        if isinstance(ev, sd.Sample):
            edge = ev.data
            if not is_c3(edge):
                return
            if edge.state.name == "RETIRED":
                self._retire(edge.id, edge.state_reason,
                             edge.valid_until if edge.has_valid_until else None)
                return
            self.c3 = edge.id
            if self.w.hold(ZONE_ID, DEMO_BLOCKED_BY, GATE_ID, Basis.DERIVED,
                           valid_from=edge.valid_from if edge.has_valid_from
                           else None,
                           derived_from=[claims.evidence(ev.topic, edge.id,
                                                         edge.stamp)],
                           meta=[("demo.how", "from the gate controller's "
                                  "declaration that gate-9 closes the zone"),
                                 decided(self.t)]
                           ) is not None:
                self.log.append(f"C5 {edge.state.name} from {edge.id}")
        elif isinstance(ev, sd.Dispose) and ev.key == self.c3:
            self._retire(ev.key, ev.reason, None)

    def _retire(self, c3: str, reason: str, until) -> None:
        if self.w.retire(ZONE_ID, DEMO_BLOCKED_BY, GATE_ID,
                         claims.NO_LONGER_HOLDS,
                         f"{c3} was retired ({reason})", until) is not None:
            self.log.append(f"C5 RETIRED: {c3} retired")
        self.c3 = None


class FuserParticipant:
    def __init__(self, p: "sd.dds.Participant") -> None:
        self.reader = p.reader(RELATIONSHIP, stream=claims.STREAM,
                               qos_profile=MODEL_LATCHED)
        self.fuser = Fuser(claims.ClaimWriter(p))

    def on_truth(self, frame, stamp) -> None:
        # The frame is only a tick and a clock: everything decided here is
        # read off the bus.
        self.fuser.t = frame["t"]
        for ev in self.reader.take():
            self.fuser.on_relationship(ev)
        for line in self.fuser.log:
            print(f"{NAME}: t={frame['t']:.1f} {line}", flush=True)
        self.fuser.log.clear()


if __name__ == "__main__":
    runner.main_for(NAME, FuserParticipant, __doc__)
