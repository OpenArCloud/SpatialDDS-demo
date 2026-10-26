#!/usr/bin/env python3
"""
The OWM 0.2 relationship prototype's conventions, on a real bus.

`spatialdds_demo/claims.py` is the one place an edge is identified, revised
and withdrawn, and these tests hold it to the census ruling
(docs/owm-02-census.md):

* proposition identity: one asserter, one (subject, predicate, object), one
  id; a second asserter is a second edge;
* revision is a change of opinion, and lineage stays with the claim;
* three retirement causes, told apart by the reason, each with the
  `valid_until` it promises;
* claims inherit crash semantics: a killed asserter's edges stand with its
  liveliness gone, and a clean stop withdraws them.

Then each asserter against its own evidence: both zone watches (latched and
fast), the near fuser's lineage resolving to the records it read, the venue's
declared edges cascading in front of a tombstone, and duck_mover's
enforced-policy edge.

These need a DDS domain and skip loudly without one.
"""
import dataclasses
import os
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import _bus as bus  # noqa: E402
import spatialdds as sd  # noqa: E402
from spatialdds.dds.types.spatial.owm import (  # noqa: E402
    Basis, LifecycleState, ModelPose)
from spatialdds.dds.types.spatial.core import PoseSE3  # noqa: E402

from spatialdds_demo import claims  # noqa: E402
from spatialdds_demo.lanes import MODEL_LATCHED, RELATIONSHIP  # noqa: E402
from spatialdds_demo.model_service import seed_entities  # noqa: E402
from spatialdds_demo_idl18.oarc_owm import RelationshipLifecycle  # noqa: E402

DOMAIN = 80
ROBOT = "ent:robot:tb3"
FOUNTAIN = "ent:fountain:littlefield"
DUCK = "ent:duck:east"


def relationship_reader(p):
    return p.reader(RELATIONSHIP, stream=claims.STREAM,
                    qos_profile=MODEL_LATCHED)


def gather(reader, until, timeout=8.0):
    """Every event until `until(events)` is true, or the timeout."""
    events, deadline = [], time.time() + timeout
    while time.time() < deadline:
        events.extend(reader.take())
        if until(events):
            return events
        time.sleep(0.05)
    return events


def samples(events):
    return [e.data for e in events if isinstance(e, sd.Sample)]


def disposes(events):
    return [e for e in events if isinstance(e, sd.Dispose)]


def seeded(entity_id):
    return next(e for e in seed_entities() if e.entity_id == entity_id)


def robot_entity(x, y, state=LifecycleState.ACTIVE, reason=""):
    duck = seeded(DUCK)
    return dataclasses.replace(
        duck, entity_id=ROBOT, basis=Basis.OBSERVED, type_uris=[],
        pose=PoseSE3(t=[x, y, -0.84], q=[0.0, 0.0, 0.0, 1.0]),
        state=state, state_reason=reason, stamp=claims.now_time())


def pose(entity_id, x, y, z=-0.84):
    return ModelPose(entity_id=entity_id,
                     pose=PoseSE3(t=[x, y, z], q=[0.0, 0.0, 0.0, 1.0]),
                     source_id="svc:test:robot", stamp=claims.now_time())


class Identity(unittest.TestCase):
    """No bus: the id and the predicates."""

    def test_one_asserter_one_proposition_one_id(self):
        a = claims.edge_id("svc:a/x", ROBOT, claims.SF_WITHIN, FOUNTAIN)
        self.assertEqual(a, claims.edge_id("svc:a/x", ROBOT, claims.SF_WITHIN,
                                           FOUNTAIN))
        self.assertNotEqual(a, claims.edge_id("svc:b/x", ROBOT,
                                              claims.SF_WITHIN, FOUNTAIN))
        self.assertNotEqual(a, claims.edge_id("svc:a/x", FOUNTAIN,
                                              claims.SF_WITHIN, ROBOT))
        self.assertNotIn("/", a, "an id must stay one URL path segment")

    def test_the_threshold_is_part_of_the_predicate(self):
        """Ruled 2026-10-25: near at 15 m and near at 6 m are two propositions."""
        self.assertEqual(claims.near(15.0), claims.NEAR_BASE + "#15m")
        self.assertNotEqual(claims.edge_id("s", ROBOT, claims.near(15), DUCK),
                            claims.edge_id("s", ROBOT, claims.near(6), DUCK))

    def test_the_three_causes_are_told_apart_by_the_reason(self):
        for cause in (claims.NO_LONGER_HOLDS, claims.SUBJECT_UNOBSERVED,
                      claims.ASSERTER_STOPPED):
            self.assertEqual(claims.cause_of(f"{cause}: because"), cause)
        self.assertEqual(claims.cause_of("the participant stopped"), "")


class Writer(unittest.TestCase):
    def setUp(self):
        self.asserter = bus.participant(DOMAIN, "svc:test:asserter")
        self.watcher = bus.participant(DOMAIN, "svc:test:watcher")
        self.reader = relationship_reader(self.watcher)
        self.claims = claims.ClaimWriter(self.asserter)

    def tearDown(self):
        self.asserter.close()
        self.watcher.close()

    def test_create_then_an_unchanged_opinion_writes_nothing(self):
        first = self.claims.hold(ROBOT, claims.SF_WITHIN, FOUNTAIN,
                                 Basis.OBSERVED, valid_from=claims.to_time(10))
        self.assertEqual(first.state, RelationshipLifecycle.CREATED)
        self.assertEqual(first.asserter, "svc:test:asserter")
        self.assertIsNone(self.claims.hold(
            ROBOT, claims.SF_WITHIN, FOUNTAIN, Basis.OBSERVED,
            valid_from=claims.to_time(10)))
        got = samples(gather(self.reader, lambda ev: len(samples(ev)) >= 1))
        time.sleep(0.3)
        got += samples(self.reader.take())
        self.assertEqual([s.id for s in got], [first.id])

    def test_a_changed_opinion_revises_the_same_instance_and_keeps_its_lineage(self):
        ref = claims.evidence("t/robot", ROBOT, claims.to_time(9))
        first = self.claims.hold(ROBOT, claims.near(15), DUCK, Basis.DERIVED,
                                 confidence=0.5, derived_from=[ref])
        second = self.claims.hold(ROBOT, claims.near(15), DUCK, Basis.DERIVED,
                                  confidence=0.9, derived_from=[])
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.state, RelationshipLifecycle.REVISED)
        self.assertEqual(second.derived_from, [ref])
        got = samples(gather(self.reader, lambda ev: len(samples(ev)) >= 2))
        self.assertEqual([s.state for s in got][-1],
                         RelationshipLifecycle.REVISED)

    def test_retirement_is_a_terminal_sample_with_its_reason_then_a_dispose(self):
        self.claims.hold(ROBOT, claims.SF_WITHIN, FOUNTAIN, Basis.OBSERVED,
                         valid_from=claims.to_time(10))
        self.claims.retire(ROBOT, claims.SF_WITHIN, FOUNTAIN,
                           claims.NO_LONGER_HOLDS, "it left",
                           valid_until=claims.to_time(20))
        events = gather(self.reader, lambda ev: disposes(ev))
        terminal = samples(events)[-1]
        self.assertEqual(terminal.state, RelationshipLifecycle.RETIRED)
        self.assertEqual(terminal.state_reason, "no longer holds: it left")
        self.assertTrue(terminal.has_valid_until)
        self.assertEqual(terminal.valid_until.sec, 20)
        self.assertEqual(terminal.valid_from.sec, 10,
                         "a retirement closes the interval, it does not edit it")
        [gone] = disposes(events)
        self.assertEqual(gone.reason, "no longer holds: it left")
        self.assertFalse(self.claims.held())


class CrashSemantics(unittest.TestCase):
    """Claims inherit the SDK's: kill withdraws nothing; a clean stop withdraws."""

    SCRIPT = textwrap.dedent("""
        import signal, sys, time
        sys.path.insert(0, {repo!r})
        import spatialdds as sd
        from spatialdds.dds.types.spatial.owm import Basis
        from spatialdds_demo import claims
        from spatialdds_demo.lanes import SCENE
        p = sd.participant(SCENE, domain={domain}, service_id="svc:test:held")
        c = claims.ClaimWriter(p)
        c.hold("ent:robot:tb3", claims.SF_WITHIN, "ent:fountain:littlefield",
               Basis.OBSERVED, valid_from=claims.now_time())
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        print("held", flush=True)
        while not stop:
            time.sleep(0.05)
        p.close(reason="asserter stopped: svc:test:held stopped cleanly")
    """)

    def _asserter(self, domain):
        child = subprocess.Popen(
            [sys.executable, "-c",
             self.SCRIPT.format(repo=str(REPO), domain=domain)],
            stdout=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.assertEqual(child.stdout.readline().strip(), "held")
        return child

    def _watch(self, domain):
        p = bus.participant(domain, "svc:test:crash-watcher")
        self.addCleanup(p.close)
        r = relationship_reader(p)
        got = gather(r, lambda ev: samples(ev))
        self.assertEqual(len(samples(got)), 1, "the edge was never seen")
        return r

    def test_a_killed_asserter_leaves_its_edge_standing_with_liveliness_lost(self):
        child = self._asserter(DOMAIN + 1)
        reader = self._watch(DOMAIN + 1)
        child.send_signal(signal.SIGKILL)
        events = gather(reader, lambda ev: any(
            isinstance(e, sd.Liveliness) and not e.alive for e in ev),
            timeout=20.0)
        self.assertTrue(any(isinstance(e, sd.Liveliness) and not e.alive
                            for e in events),
                        f"no liveliness loss seen: {events!r}")
        self.assertEqual(disposes(events), [],
                         "a killed asserter must not withdraw anything")

    def test_a_clean_stop_withdraws_with_the_asserter_stopped_reason(self):
        child = self._asserter(DOMAIN + 2)
        reader = self._watch(DOMAIN + 2)
        child.send_signal(signal.SIGTERM)
        events = gather(reader, lambda ev: disposes(ev), timeout=10.0)
        child.wait(timeout=10)
        [gone] = disposes(events)
        self.assertEqual(claims.cause_of(gone.reason), claims.ASSERTER_STOPPED)
        terminal = samples(events)[-1]
        self.assertEqual(terminal.state, RelationshipLifecycle.RETIRED)
        self.assertFalse(terminal.has_valid_until,
                         "a clean stop did not consult the world, so it "
                         "cannot say when the claim stopped being true")


class ZoneWatches(unittest.TestCase):
    """Both evidence lanes, and all three endings a robot can cause."""

    def setUp(self):
        from spatialdds_demo.zone_watch import ZoneWatch
        self.venue = bus.participant(DOMAIN + 3, "svc:test:venue")
        self.entities = bus.writer(self.venue, "model_entity")
        self.poses = bus.writer(self.venue, "model_pose")
        self.entities.write(seeded(FOUNTAIN))
        self.watches = {}
        for evidence in ("latched", "fast"):
            p = bus.participant(DOMAIN + 3, f"svc:test:zone-{evidence}")
            self.addCleanup(p.close)
            self.watches[evidence] = ZoneWatch(p, evidence)
        self.addCleanup(self.venue.close)

    def _robot_at(self, x, y, **kw):
        self.entities.write(robot_entity(x, y, **kw))
        self.poses.write(pose(ROBOT, x, y))

    def _settle(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for w in self.watches.values():
                w.tick()
            time.sleep(0.05)
            if all(w.claims.holds(ROBOT, claims.SF_WITHIN, FOUNTAIN)
                   for w in self.watches.values()) == self._want:
                return
        self.fail(f"zone watches did not settle on holds={self._want}")

    def test_entry_then_exit_on_both_lanes(self):
        self._robot_at(17.0, -23.0)                 # the start: outside
        self._want = False
        self._settle(1.0)
        self._robot_at(12.0, 0.0)                   # behind the pond: inside
        self._want = True
        self._settle()
        edges = [w.claims.held() for w in self.watches.values()]
        ids = {next(iter(e)) for e in edges}
        self.assertEqual(len(ids), 2, "two asserters, two edges")
        for e in edges:
            [edge] = e.values()
            self.assertEqual(edge.basis, Basis.OBSERVED)
            self.assertTrue(edge.has_valid_from)
            self.assertEqual(len(edge.derived_from), 2,
                             "the robot's record and the fountain's")
        lanes = {w.evidence: next(iter(w.claims.held().values())).derived_from[0]
                 .topic for w in self.watches.values()}
        self.assertTrue(lanes["latched"].endswith("/world/entity/v1"), lanes)
        self.assertTrue(lanes["fast"].endswith("/model_pose/v1"), lanes)
        self._robot_at(17.0, -23.0)
        self._want = False
        self._settle()

    def test_an_unobserved_robot_retires_with_its_own_reason(self):
        watcher = bus.participant(DOMAIN + 3, "svc:test:zone-reader")
        self.addCleanup(watcher.close)
        reader = relationship_reader(watcher)
        self._robot_at(12.0, 0.0)
        self._want = True
        self._settle()
        self.entities.write(robot_entity(
            12.0, 0.0, state=LifecycleState.UNOBSERVED,
            reason="no pose for 6.0s"))
        self._want = False
        self._settle()
        events = gather(reader, lambda ev: len(disposes(ev)) >= 2)
        reasons = [d.reason for d in disposes(events)]
        self.assertEqual(len(reasons), 2, events)
        for reason in reasons:
            self.assertEqual(claims.cause_of(reason),
                             claims.SUBJECT_UNOBSERVED, reason)
        terminals = [s for s in samples(events)
                     if s.state == RelationshipLifecycle.RETIRED]
        self.assertTrue(terminals and all(t.has_valid_until for t in terminals),
                        "valid_until is the last evidence that showed it true")


class NearFuserLineage(unittest.TestCase):
    """derived_from resolves to the records the fuser actually read."""

    def test_the_refs_are_the_robot_and_duck_pose_records(self):
        from spatialdds_demo.near_fuser import NearFuser
        venue = bus.participant(DOMAIN + 4, "svc:test:venue")
        self.addCleanup(venue.close)
        entities = bus.writer(venue, "model_entity")
        poses = bus.writer(venue, "model_pose")
        entities.write(seeded(DUCK))
        entities.write(robot_entity(17.0, -23.0))
        p = bus.participant(DOMAIN + 4, "svc:test:near")
        self.addCleanup(p.close)
        fuser = NearFuser(p, threshold_m=15.0)
        robot_pose = pose(ROBOT, 17.0, -23.0)
        duck_pose = pose(DUCK, 16.5, -10.5, z=-1.423)
        poses.write(robot_pose)
        poses.write(duck_pose)
        deadline = time.time() + 5
        while time.time() < deadline and not fuser.claims.held():
            fuser.tick()
            time.sleep(0.05)
        [edge] = fuser.claims.held().values()
        self.assertEqual(edge.basis, Basis.DERIVED)
        self.assertEqual(edge.predicate, claims.near(15.0))
        refs = {(r.key, r.stamp.sec, r.stamp.nanosec) for r in edge.derived_from}
        self.assertEqual(refs, {
            (ROBOT, robot_pose.stamp.sec, robot_pose.stamp.nanosec),
            (DUCK, duck_pose.stamp.sec, duck_pose.stamp.nanosec)})
        for r in edge.derived_from:
            self.assertTrue(r.topic.endswith("/model_pose/v1"), r.topic)
        poses.write(pose(ROBOT, 17.0, -40.0))       # 29.5 m away
        deadline = time.time() + 5
        while time.time() < deadline and fuser.claims.held():
            fuser.tick()
            time.sleep(0.05)
        self.assertFalse(fuser.claims.held(), "the edge did not retire")


class DeclaredEdges(unittest.TestCase):
    def test_retiring_the_pond_cascades_both_part_of_edges_first(self):
        from spatialdds_demo.model_service import ModelPublisher
        p = bus.participant(DOMAIN + 5, "svc:model:demo/venue")
        self.addCleanup(p.close)
        watcher = bus.participant(DOMAIN + 5, "svc:test:declared-reader")
        self.addCleanup(watcher.close)
        reader = relationship_reader(watcher)
        publisher = ModelPublisher(p)
        for e in seed_entities():
            publisher.publish_entity(e)
        ids = publisher.publish_declared_edges()
        self.assertEqual(len(ids), 2)
        for edge in publisher.claims.held().values():
            self.assertEqual(edge.predicate, claims.PART_OF)
            self.assertEqual(edge.basis, Basis.DECLARED)
        cascaded = publisher.retire("ent:pond:littlefield", "drained",
                                    settle=0.1)
        self.assertEqual(sorted(cascaded), sorted(ids),
                         "the pond is the subject of one and the object of "
                         "the other")
        events = gather(reader, lambda ev: len(disposes(ev)) >= 2)
        # Counted first: a loop over no disposes asserts nothing, and a
        # cascade that withdrew nothing passed this test that way once.
        self.assertEqual(len(disposes(events)), 2, events)
        for d in disposes(events):
            self.assertEqual(claims.cause_of(d.reason), claims.NO_LONGER_HOLDS)
            self.assertIn("drained", d.reason)


class DuckMoverEdges(unittest.TestCase):
    def test_each_duck_within_the_box_it_is_kept_in_declared_as_policy(self):
        from spatialdds_demo.duck_mover import DuckMover
        venue = bus.participant(DOMAIN + 6, "svc:test:venue")
        self.addCleanup(venue.close)
        entities = bus.writer(venue, "model_entity")
        for e in seed_entities():
            entities.write(e)
        p = bus.participant(DOMAIN + 6, "svc:test:mover")
        self.addCleanup(p.close)
        mover = DuckMover(p, "ent:shallows:littlefield")
        deadline = time.time() + 5
        while time.time() < deadline and len(mover.claims.held()) < 3:
            mover.poll()
            mover.assert_containment()
            time.sleep(0.05)
        edges = list(mover.claims.held().values())
        self.assertEqual(len(edges), 3)
        for edge in edges:
            self.assertEqual(edge.object_ref, "ent:shallows:littlefield")
            self.assertEqual(edge.basis, Basis.DECLARED)
            self.assertIn("oarc.claim.enforced_by",
                          [kv.key for kv in edge.meta])


if __name__ == "__main__":
    unittest.main()
