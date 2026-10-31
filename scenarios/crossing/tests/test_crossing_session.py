#!/usr/bin/env python3
"""A whole crossing session on the pinned SDK: every participant runs clean.

Runs `scenarios/crossing/run_session.py` unpaced on a domain of its own: the
world service, the map author, both operators, mmwave-ut-04 and gate-ctl-9,
recorded. Asserts what a unit test of any one module cannot: that each
participant exits 0 through the SDK the repository pins, and that the
recording holds every lane's records, withdrawals with their reasons, and the
gate's declared states.

This exists because one was missing. The first sessions on the pinned
spatialdds crashed three of five participants at their first withdrawal (the
pinned `withdraw()` refuses a stamp; docs/crossing-census.md), and nothing
smaller had run a withdrawal at all.

Needs the pinned SUMO (`scenarios/crossing/requirements.txt`); skips saying
so without it. About a minute.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DOMAIN = 63
OBB_KEYS = ("demo.obb.length_m", "demo.obb.width_m", "demo.obb.height_m")


def encloses(e, length: float, width: float, height: float) -> bool:
    """Whether the entity's Aabb3 holds the oriented box its meta describes:
    the dimensions, centred on the pose, turned by the pose's yaw."""
    import math
    x, y, z = e.pose.t
    qz, qw = e.pose.q[2], e.pose.q[3]
    yaw = 2 * math.atan2(qz, qw)
    c, s = math.cos(yaw), math.sin(yaw)
    lo, hi, eps = e.extent.min_xyz, e.extent.max_xyz, 1e-6
    for dl in (-length / 2, length / 2):
        for dw in (-width / 2, width / 2):
            px, py = x + c * dl - s * dw, y + s * dl + c * dw
            if not (lo[0] - eps <= px <= hi[0] + eps and lo[1] - eps <= py <= hi[1] + eps):
                return False
    return lo[2] - eps <= z and z + height <= hi[2] + eps


class AWholeSession(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import importlib.util
        # Probed, not imported: tests are held to the seam too.
        if importlib.util.find_spec("sumo") is None:
            raise unittest.SkipTest(
                "the pinned SUMO is not installed: pip install -r "
                "scenarios/crossing/requirements.txt")
        from mcap.reader import make_reader
        from spatialdds.dds.types.spatial.owm import Entity

        from scenarios.crossing import run_session
        from scenarios.crossing.scene import ENTITY_TOPIC, FRAMES_TOPIC, POSE_TOPIC
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.result = run_session.run(tmp / "s.mcap", DOMAIN, 0.0, 42,
                                     logs=tmp, settle_s=2.0)
        cls.logs = tmp
        cls.counts, cls.sources, cls.reasons, cls.gate = {}, set(), set(), []
        cls.tracks, cls.unenclosed, cls.undimensioned = 0, [], []
        with open(tmp / "s.mcap", "rb") as f:
            reader = make_reader(f)
            for _s, ch, msg in reader.iter_messages(log_time_order=True):
                cls.counts[ch.topic] = cls.counts.get(ch.topic, 0) + 1
                if ch.topic == ENTITY_TOPIC:
                    e = Entity.deserialize(msg.data)
                    cls.sources.add(e.source_id)
                    if e.state.name == "RETIRED":
                        cls.reasons.add(e.state_reason.split(":")[0])
                    for kv in e.properties:
                        if kv.key == "demo.gate_state" and (
                                not cls.gate or cls.gate[-1] != kv.value):
                            cls.gate.append(kv.value)
                    if e.layer.name == "FAST" and e.state.name != "RETIRED":
                        cls.tracks += 1
                        props = {kv.key: kv.value for kv in e.properties}
                        if not all(k in props for k in OBB_KEYS):
                            cls.undimensioned.append(e.entity_id)
                        elif not encloses(e, *(float(props[k]) for k in OBB_KEYS)):
                            cls.unenclosed.append(e.entity_id)
            f.seek(0)
            cls.disposes = sum(1 for m in make_reader(f).iter_metadata()
                               if m.name.endswith("dispose"))
        cls.topics = (ENTITY_TOPIC, POSE_TOPIC, FRAMES_TOPIC)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_process_exits_clean(self):
        bad = {n: rc for n, rc in self.result["returncodes"].items() if rc != 0}
        detail = "".join(f"\n--- {n}\n" + (self.logs / f"{n}.log").read_text()[-1500:]
                         for n in bad)
        self.assertEqual(bad, {}, detail)

    def test_every_lane_was_recorded(self):
        for topic in self.topics:
            self.assertGreater(self.counts.get(topic, 0), 0, topic)

    def test_every_asserter_published(self):
        self.assertEqual(self.sources, {
            "svc:crossing:map", "svc:crossing:operator-a",
            "svc:crossing:operator-b", "svc:crossing:mmwave-ut-04",
            "svc:crossing:gate-ctl-9"})

    def test_withdrawals_carry_their_reasons(self):
        self.assertGreater(self.disposes, 0)
        self.assertIn("asserter stopped", self.reasons)
        self.assertTrue({"left the scene", "track lost"} & self.reasons,
                        self.reasons)

    def test_every_track_carries_its_oriented_box(self):
        """The native Aabb3 is the enclosing box; the true one is meta."""
        self.assertGreater(self.tracks, 0)
        self.assertEqual(self.undimensioned, [])
        self.assertEqual(self.unenclosed, [])

    def test_the_gate_declared_its_cycle(self):
        self.assertEqual(self.gate, ["open", "warning", "closed", "opening", "open"])


if __name__ == "__main__":
    unittest.main()
