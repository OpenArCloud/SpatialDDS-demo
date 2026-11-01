#!/usr/bin/env python3
"""The crossing scene's claims, perception and control, offline.

Each asserter's own code, `claims.ClaimWriter` and `Publisher` included, run
over the committed session's ground truth (`tests/offline.py`), and the
consumers fed bus events built here. No DDS and no SUMO: what the bus adds is
the whole-session test's (`test_crossing_session.py`).

* **Zones.** `geo:sfWithin` and `geo:sfIntersects` as DE-9IM has them, and
  the train that is never within the crossing zone (the C4 finding).
* **Perception.** operator-a's record of ped-17 never names car-a1 while the
  bus blocks car-a1's line of sight, and does once it clears.
* **Claims.** C1's lifecycle (predicted ahead, re-predicted at the kerb,
  revised to OBSERVED on the same id, retired when it stops being true), C2,
  C3 and C4; C5 derived from C3 alone.
* **Control.** The controller reads nothing from a truth frame; trusts only
  the asserter and basis it names; stops short of a held claim's zone;
  halts in a crosswalk and clears a crossing.
"""
from __future__ import annotations

import io
import math
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import spatialdds as sd                                         # noqa: E402
from spatialdds.dds.types.spatial.owm import Basis              # noqa: E402

from spatialdds_demo import claims                              # noqa: E402
from scenarios.crossing import (                                # noqa: E402
    controller, fuser, gate_ctl, mmwave, operator, visibility, zones)
from scenarios.crossing.scene import (                          # noqa: E402
    DEMO_BLOCKED_BY, DEMO_CLOSES, POSE_TOPIC, RELATIONSHIP_TOPIC, SCENARIO,
    SF_INTERSECTS, SF_WITHIN, entity, footprint_extent, service_id, to_time)
from scenarios.crossing.tests.offline import (                  # noqa: E402
    OfflineParticipant, drive, frames)

EPOCH = 1_000_000.0
ZONES = {z["id"]: z for z in SCENARIO["zones"]}


def t_of(stamp) -> float:
    return round(stamp.sec + stamp.nanosec / 1e9 - EPOCH, 3)


def quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def edges(p: OfflineParticipant):
    """Every relationship written, and every withdrawal, in order."""
    out = []
    for op, topic, x in p.out:
        if topic != "oarc.relationship":
            continue
        if op == "write":
            out.append(("write", x))
        else:
            out.append(("withdraw", x))
    return out


class Zones(unittest.TestCase):

    def test_within_needs_the_whole_footprint(self):
        cw = ZONES["crosswalk"]
        self.assertTrue(zones.within(0.0, 0.0, 90.0, 0.4, 0.6, cw))
        # The centre inside, a corner out: not within, but intersecting.
        self.assertFalse(zones.within(0.0, -6.3, 90.0, 0.4, 0.6, cw))
        self.assertTrue(zones.intersects(0.0, -6.3, 90.0, 0.4, 0.6, cw))

    def test_the_train_is_never_within_the_crossing_zone(self):
        """The finding behind C4's predicate: 120 m of train, 12.8 m of zone."""
        z = ZONES["crossing-zone"]
        cx = (z["min_xyz"][0] + z["max_xyz"][0]) / 2
        self.assertFalse(any(zones.within(cx, y, 90.0, 120.0, 3.0, z)
                             for y in range(-200, 200)))
        self.assertTrue(zones.intersects(cx, 0.0, 90.0, 120.0, 3.0, z))

    def test_time_to_within(self):
        cw = ZONES["crosswalk"]
        t = zones.time_to_within(0.0, -8.0, 0.0, 1.0, 90.0, 0.4, 0.6, cw,
                                 horizon_s=5.0)
        self.assertAlmostEqual(t, 1.8, places=1)        # -8.0 + 0.2 -> -6.4
        self.assertIsNone(zones.time_to_within(0.0, -8.0, 1.0, 0.0, 0.0, 0.4,
                                               0.6, cw, horizon_s=5.0))


class Perception(unittest.TestCase):
    """operator-a, over the committed truth, against the visibility model."""

    @classmethod
    def setUpClass(cls):
        cls.p = OfflineParticipant("operator-a")
        quiet(drive, operator.Operator("operator-a", cls.p), epoch=EPOCH, until=50)
        cls.records = [e for e in cls.p.written("model_entity")
                       if e.entity_id == "ent:operator-a:ped-17"]
        # Ground truth: when the bus is what keeps car-a1 from ped-17.
        sight = visibility.Sight(SCENARIO["buildings"])
        h = SCENARIO["operator_sensing"]["sensor_height_m"]
        cls.hidden = []
        for fr in frames():
            a, ped = fr["actors"].get("car-a1"), fr["actors"].get("ped-17")
            if a and ped and fr["t"] <= 50:
                seen = sight.visible((a["x"], a["y"], a["z"] + h), 60.0,
                                     {"ped-17": ped, **{k: v for k, v in fr["actors"].items()
                                                        if v["kind"] in ("bus", "train")}})
                if seen.get("ped-17") == "bus-3":
                    cls.hidden.append(fr["t"])

    def seen_by(self, e):
        return {kv.key: kv.value for kv in e.properties}.get("demo.seen_by", "").split(",")

    def test_the_bus_hides_ped_17_from_car_a1(self):
        self.assertGreater(len(self.hidden), 20, "no occlusion to test")
        for e in self.records:
            if t_of(e.stamp) in self.hidden:
                self.assertNotIn("car-a1", self.seen_by(e), t_of(e.stamp))

    def test_car_a1_sees_ped_17_once_past_the_bus(self):
        first = next((t_of(e.stamp) for e in self.records
                      if "car-a1" in self.seen_by(e)), None)
        self.assertIsNotNone(first)
        self.assertGreater(first, max(self.hidden))

    def test_a_record_seen_by_none_is_unobserved(self):
        for e in self.records:
            if self.seen_by(e) == [""]:
                self.assertEqual(e.state.name, "UNOBSERVED")


class TheUnitsClaims(unittest.TestCase):
    """mmwave-ut-04 over the committed truth: C1, C2, C4."""

    @classmethod
    def setUpClass(cls):
        cls.p = OfflineParticipant("mmwave-ut-04")
        quiet(drive, mmwave.SensingUnit(cls.p), epoch=EPOCH)
        cls.log = edges(cls.p)

    def about(self, subject):
        return [x for op, x in self.log if op == "write" and x.subject_ref == subject]

    def test_c1_is_predicted_ahead_of_its_stamp(self):
        c1 = self.about("ent:mmwave-ut-04:ped-17")
        self.assertEqual(c1[0].state.name, "CREATED")
        self.assertEqual(c1[0].basis, Basis.DERIVED)
        self.assertEqual(c1[0].predicate, SF_WITHIN)
        self.assertEqual(c1[0].object_ref, "ent:map:crosswalk")
        self.assertTrue(c1[0].has_confidence)
        decided = float({kv.key: kv.value for kv in c1[0].meta}["demo.decided_t"])
        self.assertGreater(t_of(c1[0].valid_from), decided)
        self.assertTrue(all(r.topic == "model_pose" or r.topic == POSE_TOPIC
                            for r in c1[0].derived_from))

    def test_c1_is_re_predicted_while_ped_17_waits(self):
        c1 = self.about("ent:mmwave-ut-04:ped-17")
        kerb = [x for x in c1 if {kv.key: kv.value for kv in x.meta}.get(
            "demo.how") == mmwave.PREDICT_KERB]
        self.assertGreaterEqual(len(kerb), 5)
        self.assertTrue(all(x.state.name == "REVISED" for x in kerb))

    def test_p2_revises_the_same_edge_to_observed(self):
        c1 = self.about("ent:mmwave-ut-04:ped-17")
        observed = [x for x in c1 if x.basis == Basis.OBSERVED]
        self.assertEqual(len(observed), 1)
        before = c1[c1.index(observed[0]) - 1]
        self.assertEqual(before.basis, Basis.DERIVED)
        self.assertEqual(observed[0].id, before.id)
        self.assertEqual(observed[0].state.name, "REVISED")
        self.assertFalse(observed[0].has_confidence)
        # ClaimWriter keeps the lineage that established the claim: the
        # observation still names the first prediction's poses (P2's cost).
        self.assertEqual(list(observed[0].derived_from), list(c1[0].derived_from))

    def test_c1_retires_when_ped_17_leaves(self):
        rid = self.about("ent:mmwave-ut-04:ped-17")[0].id
        retired = [x for op, x in self.log if op == "withdraw" and x[0] == rid]
        self.assertEqual(len(retired), 1)
        key, reason, kw = retired[0]
        self.assertTrue(reason.startswith(claims.NO_LONGER_HOLDS), reason)
        self.assertTrue(kw["has_valid_until"])

    def test_c2_is_observed_from_arrival_to_departure(self):
        c2 = self.about("ent:mmwave-ut-04:bus-3")
        self.assertEqual([x.basis for x in c2], [Basis.OBSERVED])
        self.assertEqual(c2[0].object_ref, "ent:map:stop-zone")

    def test_c4_intersects_and_is_predicted_then_observed(self):
        c4 = self.about("ent:mmwave-ut-04:train-7")
        self.assertEqual({x.predicate for x in c4}, {SF_INTERSECTS})
        self.assertEqual([x.basis for x in c4][0], Basis.DERIVED)
        self.assertEqual([x.basis for x in c4][-1], Basis.OBSERVED)


class FakeFeed:
    def __init__(self):
        self.sent = []

    def send(self, cmd):
        self.sent.append(cmd)

    def stamp_of(self, t):
        return EPOCH + t


class TheGatesClaim(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.p = OfflineParticipant("gate-ctl-9")
        ctl = gate_ctl.GateController(cls.p)
        ctl.feed = FakeFeed()
        quiet(drive, ctl, epoch=EPOCH)
        cls.ctl, cls.log = ctl, edges(cls.p)

    def test_c3_is_declared_at_detection_for_arms_down(self):
        c3 = [x for op, x in self.log if op == "write"]
        self.assertEqual(len(c3), 1)
        self.assertEqual(c3[0].basis, Basis.DECLARED)
        self.assertEqual(c3[0].predicate, DEMO_CLOSES)
        self.assertEqual(list(c3[0].derived_from), [])
        sched = self.ctl.plan.schedule
        self.assertAlmostEqual(t_of(c3[0].valid_from), sched["closed"], places=3)
        decided = float({kv.key: kv.value for kv in c3[0].meta}["demo.decided_t"])
        self.assertAlmostEqual(decided, sched["detected"], places=3)

    def test_c3_retires_when_the_arms_are_up(self):
        out = [x for op, x in self.log if op == "withdraw"]
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0][1].startswith(claims.NO_LONGER_HOLDS))


def c3_edge(state="CREATED", basis=Basis.DECLARED, asserter=None):
    from spatialdds_demo_idl18.oarc_owm import Relationship, RelationshipLifecycle
    from spatialdds.dds.types.spatial.common import KV
    rid = claims.edge_id(asserter or service_id("gate-ctl-9"), fuser.GATE_ID,
                         DEMO_CLOSES, fuser.ZONE_ID)
    return Relationship(
        id=rid, subject_ref=fuser.GATE_ID, predicate=DEMO_CLOSES,
        object_ref=fuser.ZONE_ID, basis=basis,
        asserter=asserter or service_id("gate-ctl-9"), has_confidence=False,
        confidence=0.0, derived_from=[], stamp=to_time(EPOCH + 41.6),
        has_valid_from=True, valid_from=to_time(EPOCH + 49.6),
        has_valid_until=state == "RETIRED", valid_until=to_time(EPOCH + 75.5),
        meta=[KV(key="demo.decided_t", value="41.6")],
        state=getattr(RelationshipLifecycle, state),
        state_reason="no longer holds: gate-9 arms up" if state == "RETIRED" else "")


class TheFuser(unittest.TestCase):

    def run_fuser(self, *events):
        p = OfflineParticipant("fuser")
        from spatialdds_demo.claims import ClaimWriter
        w = ClaimWriter.__new__(ClaimWriter)
        w.asserter, w._w, w._held = p.service_id, p.writer("oarc.relationship"), {}
        w.topic = RELATIONSHIP_TOPIC
        f = fuser.Fuser(w)
        for ev in events:
            f.on_relationship(ev)
        return edges(p)

    def test_c5_is_derived_from_the_c3_it_read(self):
        c3 = c3_edge()
        log = self.run_fuser(sd.Sample(RELATIONSHIP_TOPIC, c3.id, c3))
        (op, c5), = log
        self.assertEqual((c5.subject_ref, c5.predicate, c5.object_ref),
                         (fuser.ZONE_ID, DEMO_BLOCKED_BY, fuser.GATE_ID))
        self.assertEqual(c5.basis, Basis.DERIVED)
        self.assertEqual(c5.valid_from, c3.valid_from)
        (ref,) = c5.derived_from
        self.assertEqual((ref.topic, ref.key, ref.stamp),
                         (RELATIONSHIP_TOPIC, c3.id, c3.stamp))

    def test_c5_retires_with_c3(self):
        log = self.run_fuser(sd.Sample(RELATIONSHIP_TOPIC, "x", c3_edge()),
                             sd.Sample(RELATIONSHIP_TOPIC, "x", c3_edge("RETIRED")))
        self.assertEqual([op for op, _ in log], ["write", "withdraw"])
        self.assertTrue(log[1][1][1].startswith(claims.NO_LONGER_HOLDS))

    def test_only_the_gate_controllers_declaration_counts(self):
        self.assertEqual(self.run_fuser(
            sd.Sample(RELATIONSHIP_TOPIC, "x", c3_edge(basis=Basis.DERIVED)),
            sd.Sample(RELATIONSHIP_TOPIC, "y", c3_edge(asserter="svc:crossing:other"))),
            [])


class Poisoned(dict):
    """A truth frame that fails the test if anything reads it."""

    def __getitem__(self, k):
        raise AssertionError(f"the controller read truth[{k!r}]")

    get = __getitem__


class EmptyReader:
    topic = "x"

    def take(self):
        return []


def own_record(x: float, yaw: float = 0.0, operator="operator-a", car="car-a1"):
    return entity(f"ent:{operator}:{car}", Basis.OBSERVED, "car",
                  source=service_id(operator), stamp=to_time(EPOCH + 30.0),
                  pose=(x, -1.6, 0.0, yaw),
                  extent=footprint_extent(x, -1.6, 0.0, yaw, 4.6, 1.85, 1.5),
                  properties=[("demo.obb.length_m", "4.6")])


def zone_record(zid: str):
    z = ZONES[zid]
    return entity(f"ent:map:{zid}", Basis.AUTHORED, z["type"], source="m",
                  stamp=to_time(EPOCH), pose=(0, 0, 0, 0),
                  extent=__import__("spatialdds.dds.types.spatial.core",
                                    fromlist=["Aabb3"]).Aabb3(
                      min_xyz=list(z["min_xyz"]), max_xyz=list(z["max_xyz"])))


def c1_edge(asserter="mmwave-ut-04", basis=Basis.DERIVED):
    from spatialdds_demo_idl18.oarc_owm import Relationship, RelationshipLifecycle
    sid = service_id(asserter)
    return Relationship(
        id=claims.edge_id(sid, "ent:mmwave-ut-04:ped-17", SF_WITHIN,
                          "ent:map:crosswalk"),
        subject_ref="ent:mmwave-ut-04:ped-17", predicate=SF_WITHIN,
        object_ref="ent:map:crosswalk", basis=basis, asserter=sid,
        has_confidence=True, confidence=0.9, derived_from=[],
        stamp=to_time(EPOCH + 30.0), has_valid_from=True,
        valid_from=to_time(EPOCH + 31.0), has_valid_until=False,
        valid_until=to_time(0), meta=[], state=RelationshipLifecycle.CREATED,
        state_reason="")


class TheController(unittest.TestCase):

    def ctl(self, x: float, *edges_, yaw: float = 0.0, car="car-a1"):
        operator = SCENARIO["protagonists"][car]["operator"]
        c = controller.Controller(car, operator)
        for zid in ("crosswalk", "crossing-zone"):
            r = zone_record(zid)
            c.on_entity(sd.Sample("e", r.entity_id, r))
        own = own_record(x, yaw, operator, car)
        c.on_entity(sd.Sample("e", own.entity_id, own))
        for e in edges_:
            c.on_relationship(sd.Sample(RELATIONSHIP_TOPIC, e.id, e))
        return c

    def test_it_reads_no_truth(self):
        blind = controller.Blind.__new__(controller.Blind)
        blind.ctl = controller.Controller("car-a1", "operator-a")
        blind.readers = [(EmptyReader(), blind.ctl.on_entity)]
        blind.feed = FakeFeed()
        blind.on_truth(Poisoned(), None)         # raises if it reads the frame

    def test_a_held_prediction_caps_the_speed_to_stop_short(self):
        v, why, d = self.ctl(-60.0, c1_edge()).target()
        self.assertIn("pedestrian in the crosswalk", why)
        stop_x = ZONES["crosswalk"]["min_xyz"][0] - controller.MARGIN_M
        self.assertAlmostEqual(d, stop_x - (-60.0 + 2.3), places=3)
        self.assertAlmostEqual(v, math.sqrt(2 * controller.A_PLAN * d), places=6)

    def test_untrusted_asserters_and_bases_are_ignored(self):
        self.assertIsNone(self.ctl(-60.0, c1_edge(asserter="operator-b")).target()[0])
        self.assertIsNone(self.ctl(-60.0, c1_edge(basis=Basis.DECLARED)).target()[0])

    def test_in_the_crosswalk_it_halts(self):
        v, why, _d = self.ctl(-1.0, c1_edge()).target()
        self.assertEqual(v, 0.0)

    def test_on_the_crossing_it_clears(self):
        c3 = c3_edge()
        self.assertIsNone(self.ctl(120.0, c3).target()[0])
        v, why, _ = self.ctl(80.0, c3).target()
        self.assertIn("gate-9 closes", why)

    def test_westbound_stops_on_the_far_side(self):
        v, why, d = self.ctl(160.0, c3_edge(), yaw=180.0, car="car-b2").target()
        stop_x = ZONES["crossing-zone"]["max_xyz"][0] + controller.MARGIN_M
        self.assertAlmostEqual(d, (160.0 - 2.3) - stop_x, places=3)

    def test_the_first_command_takes_control(self):
        cmd = self.ctl(-200.0).decide()
        self.assertEqual(cmd["speed_mode"], controller.SPEED_MODE)
        self.assertIsNone(cmd["cap"])


if __name__ == "__main__":
    unittest.main()
