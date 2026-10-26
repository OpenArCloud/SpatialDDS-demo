#!/usr/bin/env python3
"""
The robot near each duck: the prototype's derived edge, with its lineage.

`near#15m(ent:robot:tb3, <duck>)`, asserted while the two are closer than
duck_mover's `AVOID_RADIUS_M` in the plane, which is the distance inside
which the venue's ducks flee the robot. The threshold is imported, not
copied, so that this claim and the behaviour it explains cannot drift
apart: when the panel shows a near edge born, the ducks start to swim away.

DERIVED: nothing here is observed. The claim is fused from two other
parties' records, and `derived_from` names both records it read, by lane,
key and stamp: the robot's pose and the duck's. Each comes from the fast lane
where it has one, and otherwise from its latched entity, which is why a
reference names a record and not just an entity. A duck that has not moved
since the venue latched it has no pose sample to point at.

GeoSPARQL has no nearness relation (census §2), so the predicate is the
demo's own (`claims.near`). The distance is `geof:metricDistance` in the
plane, the same `math.hypot` duck_mover's rule uses, and it says so in
`meta` beside the threshold, for machine reading.

Its edges end in the three ways `claims.py` lists: the distance reaching the
threshold, the robot going UNOBSERVED, or this service stopping cleanly. A
withdrawn duck or robot ends its edges as `no longer holds`.

    python3 -m spatialdds_demo.near_fuser
"""
from __future__ import annotations

import argparse
import math
import signal
import sys
import time
from typing import Dict, List, Optional, Tuple

import spatialdds as sd
from spatialdds.dds.types.spatial.owm import Basis, Entity, LifecycleState

from spatialdds_demo import claims
from spatialdds_demo.duck_mover import AVOID_ENTITY_ID, AVOID_RADIUS_M
from spatialdds_demo.lanes import MODEL_FAST, MODEL_LATCHED, SCENE
from spatialdds_demo.model_service import TYPE_RUBBER_DUCK, live_log

SOURCE_ID = "svc:claims:demo/near"
POLL_S = 0.1

Fix = Tuple[Tuple[float, float], claims.EvidenceRef]


class NearFuser:
    """near(robot, duck) for every duck, from the robot's and the duck's poses."""

    def __init__(self, participant: "sd.dds.Participant",
                 robot_id: str = AVOID_ENTITY_ID,
                 threshold_m: float = AVOID_RADIUS_M):
        self.robot_id, self.threshold_m = robot_id, threshold_m
        self.predicate = claims.near(threshold_m)
        self._entities_r = participant.reader("model_entity",
                                              qos_profile=MODEL_LATCHED)
        self._poses_r = participant.reader("model_pose", qos_profile=MODEL_FAST)
        self.claims = claims.ClaimWriter(participant)
        self._entities: Dict[str, Entity] = {}
        # Each party's newest position, and the record it came from.
        self._fix: Dict[str, Fix] = {}
        # Per duck, the last evidence that showed the claim true.
        self._last_near: Dict[str, claims.Time] = {}

    def poll(self) -> None:
        for ev in self._entities_r.take():
            if isinstance(ev, sd.Sample):
                self._entities[ev.key] = ev.data
                # A latched record is evidence only until a pose supersedes it.
                if ev.data.has_pose and (
                        ev.key not in self._fix
                        or self._fix[ev.key][1].topic == ev.topic):
                    t = ev.data.pose.t
                    self._fix[ev.key] = ((t[0], t[1]), claims.evidence(
                        ev.topic, ev.key, ev.data.stamp))
            elif isinstance(ev, sd.Dispose):
                self._entities.pop(ev.key, None)
                self._fix.pop(ev.key, None)
                self.claims.retire_involving(
                    ev.key, claims.PARTY_RETIRED,
                    f"{ev.key} was withdrawn ({ev.reason})")
        for ev in self._poses_r.take():
            if isinstance(ev, sd.Sample):
                t = ev.data.pose.t
                self._fix[ev.key] = ((t[0], t[1]), claims.evidence(
                    ev.topic, ev.key, ev.data.stamp))

    def ducks(self) -> List[str]:
        return sorted(k for k, e in self._entities.items()
                      if TYPE_RUBBER_DUCK in e.type_uris
                      and e.state == LifecycleState.ACTIVE)

    def decide(self) -> List[str]:
        notes: List[str] = []
        robot = self._entities.get(self.robot_id)
        if robot is not None and robot.state == LifecycleState.UNOBSERVED:
            for duck in list(self._last_near):
                if self.claims.holds(self.robot_id, self.predicate, duck):
                    self.claims.retire(
                        self.robot_id, self.predicate, duck,
                        claims.SUBJECT_UNOBSERVED,
                        f"{self.robot_id} is UNOBSERVED "
                        f"({robot.state_reason}); it was last within "
                        f"{self.threshold_m:g} m of {duck}",
                        valid_until=self._last_near.pop(duck))
                    notes.append(f"retired near {duck}: subject unobserved")
            return notes
        if robot is None or self.robot_id not in self._fix:
            return notes
        (rx, ry), rref = self._fix[self.robot_id]
        for duck in self.ducks():
            if duck not in self._fix:
                continue
            (dx, dy), dref = self._fix[duck]
            distance = math.hypot(dx - rx, dy - ry)
            # The claim is about the later of the two records: before both
            # existed, nothing showed it.
            at = max(rref.stamp, dref.stamp,
                     key=lambda t: (t.sec, t.nanosec))
            held = self.claims.holds(self.robot_id, self.predicate, duck)
            if distance < self.threshold_m:
                self._last_near[duck] = at
                if not held:
                    self.claims.hold(
                        self.robot_id, self.predicate, duck, Basis.DERIVED,
                        valid_from=at, derived_from=[rref, dref],
                        meta=[("oarc.near.threshold_m", f"{self.threshold_m:g}"),
                              ("oarc.near.metric",
                               "geof:metricDistance in the venue plane "
                               "(x, y), as duck_mover measures it")])
                    notes.append(f"near {duck} at {distance:.1f} m")
            elif held:
                self.claims.retire(
                    self.robot_id, self.predicate, duck,
                    claims.NO_LONGER_HOLDS,
                    f"{distance:.1f} m from {duck}, not under "
                    f"{self.threshold_m:g} m", valid_until=at)
                self._last_near.pop(duck, None)
                notes.append(f"no longer near {duck} ({distance:.1f} m)")
        return notes

    def tick(self) -> List[str]:
        self.poll()
        return self.decide()


def run(domain_id: Optional[int] = None) -> int:
    live_log()
    participant = sd.participant(SCENE, domain=domain_id, service_id=SOURCE_ID)
    fuser = NearFuser(participant)
    print(f"near_fuser: {SOURCE_ID} asserts {fuser.predicate} from "
          f"{AVOID_ENTITY_ID} to each duck on {fuser.claims.topic}", flush=True)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(1))
    try:
        while not stopping:
            for note in fuser.tick():
                print(f"near_fuser: {note}", flush=True)
            time.sleep(POLL_S)
    finally:
        participant.close(reason=f"{claims.ASSERTER_STOPPED}: {SOURCE_ID} "
                                 f"stopped cleanly")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--domain", type=int, default=None)
    return run(ap.parse_args().domain)


if __name__ == "__main__":
    sys.exit(main())
