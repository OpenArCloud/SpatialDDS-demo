#!/usr/bin/env python3
"""
The robot within the fountain: the prototype's observed, bitemporal edge.

`geo:sfWithin(ent:robot:tb3, ent:fountain:littlefield)`, asserted while the
robot's position lies inside the fountain's published extent.

**What this claim is about.** It is computed against `Entity.extent`, the
box the venue publishes for the fountain, so it asserts something about the
model and not about the unmeasured world. That is what every claim on this
bus does. The seeder calls the box "coarse on purpose"; it is a locator, not
a boundary (`keepout.py`, which records the same gap from the costmap's
side). The coarseness becomes visible the moment an edge depends on it,
which is the claims model making the model's quality inspectable.

**Two of these run, reading different evidence** (census §1.2, ruled
2026-10-25):
- `--evidence latched` reads the robot's position from its latched `Entity`,
  which the robot republishes on a cadence;
- `--evidence fast` reads it from the `ModelPose` fast lane.

Both assert the same proposition, as two edges, because they are two
asserters. Their `valid_from` values differ by up to a latch interval, and
neither is wrong: validity is relative to the evidence an asserter read.

**Three ways an edge ends** (`claims.py`):
- the robot leaves the extent (`no longer holds`, valid_until = the first
  evidence showing it outside);
- the robot goes UNOBSERVED (`subject unobserved`, valid_until = the last
  evidence showing it inside);
- this service stops cleanly (`asserter stopped`, valid_until unset).

A fountain or robot that is withdrawn also retires the edge, as
`no longer holds`. Killed, it withdraws nothing, and its edges stand with
their writer's liveliness gone.

    python3 -m spatialdds_demo.zone_watch --evidence latched
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Dict, Optional, Tuple

import spatialdds as sd
from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.owm import Basis, Entity, LifecycleState

from spatialdds_demo import claims
from spatialdds_demo.lanes import MODEL_FAST, MODEL_LATCHED, SCENE

ROBOT_ID = "ent:robot:tb3"
FOUNTAIN_ID = "ent:fountain:littlefield"

SOURCE_IDS = {
    "latched": "svc:claims:demo/zone-latched",
    "fast": "svc:claims:demo/zone-fast",
}

#: Ten checks a second: finer than the robot's latch cadence and coarser than
#: its fast lane, so the fast asserter's valid_from is limited by this poll
#: and the latched one's by the latch.
POLL_S = 0.1

Point = Tuple[float, float, float]


def inside(point: Point, extent) -> bool:
    """A point within an `Aabb3`, inclusive: DE-9IM within, for a point."""
    return all(extent.min_xyz[i] <= point[i] <= extent.max_xyz[i]
               for i in range(3))


class ZoneWatch:
    """One asserter of sfWithin(subject, zone), from one evidence lane."""

    def __init__(self, participant: "sd.dds.Participant", evidence: str,
                 subject_id: str = ROBOT_ID, zone_id: str = FOUNTAIN_ID):
        if evidence not in SOURCE_IDS:
            raise ValueError(f"evidence must be one of {sorted(SOURCE_IDS)}")
        self.evidence = evidence
        self.subject_id, self.zone_id = subject_id, zone_id
        self._entities_r = participant.reader("model_entity",
                                              qos_profile=MODEL_LATCHED)
        self._poses_r = (participant.reader("model_pose",
                                            qos_profile=MODEL_FAST)
                         if evidence == "fast" else None)
        self.claims = claims.ClaimWriter(participant)
        self._entities: Dict[str, Entity] = {}
        # The subject's newest position, and the record it came from.
        self._position: Optional[Tuple[Point, claims.EvidenceRef]] = None
        # The last evidence that showed the claim true.
        self._last_inside: Optional[Time] = None

    # --- reading -----------------------------------------------------------

    def poll(self) -> None:
        for ev in self._entities_r.take():
            if isinstance(ev, sd.Sample):
                self._entities[ev.key] = ev.data
                if ev.key == self.subject_id and self.evidence == "latched" \
                        and ev.data.has_pose:
                    t = ev.data.pose.t
                    self._position = ((t[0], t[1], t[2]), claims.evidence(
                        ev.topic, ev.key, ev.data.stamp))
            elif isinstance(ev, sd.Dispose):
                self._entities.pop(ev.key, None)
                if ev.key in (self.subject_id, self.zone_id):
                    self.claims.retire_involving(
                        ev.key, claims.PARTY_RETIRED,
                        f"{ev.key} was withdrawn ({ev.reason})")
                    if ev.key == self.subject_id:
                        self._position = None
        if self._poses_r is not None:
            for ev in self._poses_r.take():
                if isinstance(ev, sd.Sample) and ev.key == self.subject_id:
                    t = ev.data.pose.t
                    self._position = ((t[0], t[1], t[2]), claims.evidence(
                        ev.topic, ev.key, ev.data.stamp))

    # --- deciding ------------------------------------------------------------

    def decide(self) -> Optional[str]:
        """Hold, retire, or leave the edge. A log line when something changed."""
        zone = self._entities.get(self.zone_id)
        subject = self._entities.get(self.subject_id)
        held = self.claims.holds(self.subject_id, claims.SF_WITHIN,
                                 self.zone_id)
        if subject is not None and subject.state == LifecycleState.UNOBSERVED:
            if held:
                self.claims.retire(
                    self.subject_id, claims.SF_WITHIN, self.zone_id,
                    claims.SUBJECT_UNOBSERVED,
                    f"{self.subject_id} is UNOBSERVED ({subject.state_reason}); "
                    f"it was last seen within {self.zone_id}",
                    valid_until=self._last_inside)
                return "retired: subject unobserved"
            return None
        if zone is None or not zone.has_extent or self._position is None \
                or zone.state != LifecycleState.ACTIVE:
            return None
        point, ref = self._position
        if inside(point, zone.extent):
            self._last_inside = ref.stamp
            if not held:
                self.claims.hold(
                    self.subject_id, claims.SF_WITHIN, self.zone_id,
                    Basis.OBSERVED, valid_from=ref.stamp,
                    derived_from=[ref, claims.evidence(
                        self._entities_r.topic, self.zone_id,
                        zone.stamp)],
                    meta=[("oarc.claim.evidence", self.evidence),
                          ("oarc.claim.method",
                           "point in the object's published Entity.extent "
                           "(Aabb3)")])
                return (f"holds from {ref.stamp.sec}.{ref.stamp.nanosec:09d} "
                        f"({self.evidence} evidence)")
            return None
        if held:
            self.claims.retire(
                self.subject_id, claims.SF_WITHIN, self.zone_id,
                claims.NO_LONGER_HOLDS,
                f"{self.subject_id} left {self.zone_id}'s extent",
                valid_until=ref.stamp)
            return "retired: left the zone"
        return None

    def tick(self) -> Optional[str]:
        self.poll()
        return self.decide()


def run(evidence: str, domain_id: Optional[int] = None) -> int:
    participant = sd.participant(SCENE, domain=domain_id,
                                 service_id=SOURCE_IDS[evidence])
    watch = ZoneWatch(participant, evidence)
    print(f"zone_watch: {SOURCE_IDS[evidence]} asserts sfWithin({ROBOT_ID}, "
          f"{FOUNTAIN_ID}) from {evidence} evidence on {watch.claims.topic}",
          flush=True)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(1))
    try:
        while not stopping:
            note = watch.tick()
            if note:
                print(f"zone_watch[{evidence}]: {note}", flush=True)
            time.sleep(POLL_S)
    finally:
        participant.close(reason=f"{claims.ASSERTER_STOPPED}: "
                                 f"{SOURCE_IDS[evidence]} stopped cleanly")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--evidence", choices=sorted(SOURCE_IDS), required=True)
    ap.add_argument("--domain", type=int, default=None)
    args = ap.parse_args()
    return run(args.evidence, args.domain)


if __name__ == "__main__":
    sys.exit(main())
