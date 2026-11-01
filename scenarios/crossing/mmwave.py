#!/usr/bin/env python3
"""mmwave-ut-04: the roadside sensing unit, its tracks, and its claims.

    python3 -m scenarios.crossing.mmwave --domain 47 --port 8871

**Emulated, and labelled so on every record.** The scene stands in for a 6G
integrated sensing-and-communication roadside unit: a pole-mounted sensor
between the crosswalk and the level crossing, high enough to see over the
traffic. Its tracks are ground truth filtered by range and line of sight
(`visibility.py`), with no radio model, no noise and no misses, and each
track says that in `demo.emulation`.

**Tracks, OBSERVED**, keyed `ent:mmwave-ut-04:<actor>`: the unit's own record
of what it sees, beside (never instead of) anyone else's, kept by the rule in
`tracks.py`. The unit itself is a DECLARED, static entity at its mount.

**Claims**, on the relationship lane through `claims.ClaimWriter`
(docs/crossing-census.md §5), decided by `ZoneClaims` from the unit's own
published track poses and nothing else:

* C1, a pedestrian `geo:sfWithin` the crosswalk: predicted (DERIVED) before
  it is true, then revised to OBSERVED when it is (P2, ruled: the same edge
  id), then retired when it stops being true;
* C2, a bus `geo:sfWithin` the stop zone: OBSERVED only, from arrival to
  departure;
* C4, a train `geo:sfIntersects` the crossing zone, predicted then observed
  as C1 is. The census proposed `sfWithin`; a 120 m train is never within a
  12.8 m zone, so `sfWithin` would be false at every instant, and the
  predicate is the one that is true (docs/crossing-claims.md, finding).

A prediction names the pose records it was derived from (`derived_from`):
the two the velocity was taken between. The predictor is constant velocity
over the last `WINDOW_S` of the track; a pedestrian standing still at the
kerb and facing the crossing is predicted in `KERB_STEP_S` ahead, with a
lower confidence, and re-predicted each time that passes while it waits
(the P1 measurement).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import spatialdds as sd
from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.owm import Basis, ModelLayer

from spatialdds_demo import claims
from scenarios.crossing import participant as runner
from scenarios.crossing import zones
from scenarios.crossing.scene import (
    POSE_TOPIC, SCENARIO, SF_INTERSECTS, SF_WITHIN, Publisher, decided,
    entity, entity_id, to_time)
from scenarios.crossing.tracks import Tracks
from scenarios.crossing.visibility import Sight

UNIT = SCENARIO["sensing_unit"]
NAME = UNIT["id"]
LOST_AFTER_S = 2.0
ZONES = {z["id"]: z for z in SCENARIO["zones"]}

#: The span of track a velocity is taken over.
WINDOW_S = 0.5
#: Below this a track is standing still.
STILL_MPS = 0.2
#: A prediction moves only when it changes by more than this.
REVISE_S = 0.5
#: A pedestrian standing at the kerb, facing in, is predicted to be in at the
#: soonest this far ahead, and is re-predicted each time that passes.
KERB_STEP_S = 1.0
#: How close to the zone's edge, straight ahead, counts as at the kerb.
KERB_REACH_M = 1.5

PREDICT_MOVING = ("constant velocity over the track's last 0.5 s, "
                  "heading held")
PREDICT_KERB = ("standing at the kerb facing the crossing: in within a "
                "second if it steps off, re-predicted each second it waits")
OBSERVED_NOTE = "observed by the unit's own track"


@dataclass(frozen=True)
class Rule:
    """One claim the unit makes: things of `kind` in `zone`."""
    name: str
    kind: str
    zone: str
    predicate: str
    predictive: bool
    horizon_s: float = 0.0
    confidence: float = 0.0
    kerb: bool = False


RULES = (
    Rule("C1", "pedestrian", "crosswalk", SF_WITHIN, True, 5.0, 0.9, kerb=True),
    Rule("C2", "bus", "stop-zone", SF_WITHIN, False),
    Rule("C4", "train", "crossing-zone", SF_INTERSECTS, True, 15.0, 0.9),
)
KERB_CONFIDENCE = 0.5


def secs(t: Time) -> float:
    return t.sec + t.nanosec / 1e9


class ZoneClaims:
    """The unit's claims, decided each step from its tracks.

    `writer` is a `claims.ClaimWriter` (or anything with its `hold`, `retire`,
    `holds` and `held`); `zone_ref(zone_id)` is the map author's record id."""

    def __init__(self, writer, eid, zone_ref) -> None:
        self.w = writer
        self.eid = eid
        self.zone_ref = zone_ref
        self.log: List[str] = []
        self.t = 0.0                         # the step being decided

    def _ref(self, aid: str, stamp: Time) -> claims.EvidenceRef:
        return claims.evidence(POSE_TOPIC, self.eid(aid), stamp)

    def _held(self, rule: Rule, aid: str):
        rid = claims.edge_id(self.w.asserter, self.eid(aid), rule.predicate,
                             self.zone_ref(rule.zone))
        return self.w.held().get(rid)

    def _hold(self, rule, aid, basis, now: Time, **kw):
        edge = self.w.hold(self.eid(aid), rule.predicate,
                           self.zone_ref(rule.zone), basis, **kw)
        if edge is not None:
            vf = (f" valid_from +{secs(kw['valid_from']) - secs(now):.2f}s"
                  if basis == Basis.DERIVED else "")
            self.log.append(f"{rule.name} {aid} {edge.state.name} "
                            f"{basis.name}{vf}")
        return edge

    def _retire(self, rule, aid, cause, detail, valid_until):
        if self.w.retire(self.eid(aid), rule.predicate,
                         self.zone_ref(rule.zone), cause, detail,
                         valid_until) is not None:
            self.log.append(f"{rule.name} {aid} RETIRED {cause}: {detail}")

    def _is_in(self, rule: Rule, a: dict) -> bool:
        test = zones.within if rule.predicate == SF_WITHIN else zones.intersects
        return test(a["x"], a["y"], a["yaw_deg"], a["length"], a["width"],
                    ZONES[rule.zone])

    def predict(self, rule: Rule, history: Sequence[Tuple[float, Time, dict]]
                ) -> Optional[Tuple[float, float, str, List[Time]]]:
        """(seconds ahead, confidence, how, the stamps it read), or None."""
        t_now, s_now, a = history[-1]
        window = [h for h in history if h[0] >= t_now - WINDOW_S - 1e-9]
        if len(window) < 2 or window[-1][0] - window[0][0] < WINDOW_S - 0.15:
            return None
        v = zones.heading_of([(h[0], h[2]["x"], h[2]["y"]) for h in window])
        zone = ZONES[rule.zone]
        if v is not None and math.hypot(*v) > STILL_MPS:
            ahead = (zones.time_to_within if rule.predicate == SF_WITHIN
                     else zones.time_to_intersect)(
                a["x"], a["y"], v[0], v[1], a["yaw_deg"], a["length"],
                a["width"], zone, horizon_s=rule.horizon_s)
            if ahead is not None and ahead > 0:
                return ahead, rule.confidence, PREDICT_MOVING, [window[0][1], s_now]
            return None
        if rule.kerb and zones.gap_ahead(a["x"], a["y"], a["yaw_deg"], zone,
                                         KERB_REACH_M) is not None:
            return KERB_STEP_S, KERB_CONFIDENCE, PREDICT_KERB, [s_now]
        return None

    def step(self, tracks: Tracks, stamp: Time) -> None:
        now = secs(stamp)
        for aid in tracks.lost:
            for rule in RULES:
                held = self._held(rule, aid)
                if held is not None:
                    self._retire(rule, aid, claims.SUBJECT_UNOBSERVED,
                                 f"{aid}: track lost", None)
        for aid, history in tracks.history.items():
            t_last, s_last, a = history[-1]
            seen_now = abs(secs(s_last) - now) < 1e-6
            for rule in RULES:
                if a["kind"] != rule.kind:
                    continue
                held = self._held(rule, aid)
                if not seen_now:
                    if held is not None:
                        self._retire(rule, aid, claims.SUBJECT_UNOBSERVED,
                                     f"{aid} is out of the unit's view",
                                     s_last)
                    continue
                self._decide(rule, aid, history, held, stamp, now)

    def _decide(self, rule, aid, history, held, stamp, now) -> None:
        if self._is_in(rule, history[-1][2]):
            if held is None or held.basis != Basis.OBSERVED:
                # P2, ruled: a prediction that comes true revises its own
                # edge, DERIVED to OBSERVED, from the instant it was seen.
                self._hold(rule, aid, Basis.OBSERVED, valid_from=stamp,
                           derived_from=[self._ref(aid, stamp)],
                           meta=[("demo.how", OBSERVED_NOTE), decided(self.t)],
                           now=stamp)
            return
        if held is not None and held.basis == Basis.OBSERVED:
            self._retire(rule, aid, claims.NO_LONGER_HOLDS,
                         f"{aid} is no longer in {rule.zone}", stamp)
            return
        p = self.predict(rule, history) if rule.predictive else None
        if p is None:
            if held is not None:
                self._retire(rule, aid, claims.NO_LONGER_HOLDS,
                             f"{aid} is no longer predicted in {rule.zone}",
                             stamp)
            return
        ahead, confidence, how, read = p
        meta = {kv.key: kv.value for kv in held.meta} if held else {}
        if held is not None and meta.get("demo.how") == how:
            kept = secs(held.valid_from)
            if how == PREDICT_KERB and kept > now + 1e-6:
                return                          # not yet passed: keep it
            if how == PREDICT_MOVING and abs(now + ahead - kept) <= REVISE_S:
                return
        self._hold(rule, aid, Basis.DERIVED,
                   valid_from=to_time(now + ahead), confidence=confidence,
                   derived_from=[self._ref(aid, s) for s in read],
                   meta=[("demo.how", how), decided(self.t)], now=stamp)


class SensingUnit:
    def __init__(self, p: "sd.dds.Participant") -> None:
        self.pub = Publisher(p)
        self.sight = Sight(SCENARIO["buildings"])
        self.mount = tuple(UNIT["position"])
        self.range_m = float(UNIT["range_m"])
        self.tracks = Tracks(self.pub, lambda aid: entity_id(NAME, aid),
                             props=lambda aid, a: [("demo.emulation", UNIT["label"])],
                             lost_after_s=LOST_AFTER_S)
        self.claims = ZoneClaims(claims.ClaimWriter(p),
                                 lambda aid: entity_id(NAME, aid),
                                 lambda zone: entity_id("map", zone))

    def on_start(self, stamp) -> None:
        x, y, z = self.mount
        self.pub.assert_entity(entity(
            entity_id(NAME, "self"), Basis.DECLARED, "roadside_unit",
            source=self.pub.source, stamp=stamp, layer=ModelLayer.STATIC,
            pose=(x, y, z, 0.0),
            properties=[("demo.label", NAME), ("demo.range_m", f"{self.range_m:g}"),
                        ("demo.emulation", UNIT["label"])]),
            now=0.0, moving=False)

    def on_truth(self, frame, stamp) -> None:
        t, actors = frame["t"], frame["actors"]
        seen = self.sight.visible(self.mount, self.range_m, actors)
        self.tracks.update(t, stamp, actors, seen, frame["arrived"])
        self.claims.t = t
        self.claims.step(self.tracks, stamp)
        for line in self.claims.log:
            print(f"{NAME}: t={t:.1f} {line}", flush=True)
        self.claims.log.clear()


if __name__ == "__main__":
    runner.main_for(NAME, SensingUnit, __doc__)
