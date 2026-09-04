"""
The fast tier: where a thing is, versus where it was last seen going.

A duck drifting across a pond changes one field. Republishing its identity,
type, extent, references and lifecycle to say so is the wrong shape at any
rate worth calling fast, so a FAST entity's every move goes out on
`spatialdds/model/pose/v1` as a bare pose, and its latched entity record is
refreshed every `LATCH_EVERY_N_MOVES`.

That buys cheapness and costs freshness, and the cost has a floor. **The latch
converges to the stream on idle**: a fast lane may lag while things are moving,
but it may not leave a wrong answer lying around once they have stopped, or a
reader arriving afterwards is handed a pose from before the end with nothing
coming to correct it. One duck, two positions -- the failure Part 2 closed,
which the fast tier would have quietly reopened.

The layer field decides which treatment an entity gets, which is what that
field is for. Nothing here special-cases ducks.
"""

import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import typed_transport as tt  # noqa: E402
from spatialdds_demo.model_service import (  # noqa: E402
    IDLE_FLUSH_S, LATCH_EVERY_N_MOVES, ModelPublisher, seed_entities,
)
from spatialdds_demo.qos_profiles import MODEL_FAST, MODEL_LATCHED  # noqa: E402
from spatialdds_demo.topics import (  # noqa: E402
    TOPIC_MODEL_ENTITY_V1, TOPIC_MODEL_POSE_V1,
)
from spatialdds_idl.oarc_model import Entity, ModelPose  # noqa: E402
from spatialdds_idl.spatial.core import PoseSE3  # noqa: E402

DOMAIN = 58
DUCK = "ent:duck:east"


def _participant(domain_id: int):
    try:
        from cyclonedds.domain import DomainParticipant
        return DomainParticipant(domain_id)
    except Exception as exc:
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


class Cadence(unittest.TestCase):
    def setUp(self):
        self.participant = _participant(DOMAIN)
        self.publisher = ModelPublisher(self.participant)
        for entity in seed_entities():
            self.publisher.publish_entity(entity)
        self.poses = tt.make_reader(
            self.participant, TOPIC_MODEL_POSE_V1, ModelPose, MODEL_FAST.name)
        self.entities = tt.make_reader(
            self.participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        time.sleep(0.6)
        tt.take_samples(self.poses)
        tt.take_samples(self.entities)

    def tearDown(self):
        self.publisher.close()

    def _move(self, n: int):
        """
        n moves of half a metre each, and what each lane saw.

        The x position advances across calls, so "how many moves stale" can be
        read straight off a distance. An earlier version restarted at 12.0
        every call, which made two separate runs land on the same coordinates
        and reported a four-move gap as one.
        """
        poses, records = [], []
        for _ in range(n):
            self._x = getattr(self, "_x", 12.0) + 0.5
            self.publisher.move(DUCK, PoseSE3(t=[self._x, -14.0, -1.423],
                                              q=[0.0, 0.0, 0.0, 1.0]))
            time.sleep(0.15)
            poses.extend(tt.take_samples(self.poses) or [])
            records.extend(s for s in (tt.take_samples(self.entities) or [])
                           if s.entity_id == DUCK)
        return poses, records

    def test_every_move_goes_out_on_the_pose_lane(self):
        poses, _ = self._move(4)
        self.assertEqual(len(poses), 4)
        self.assertEqual({p.entity_id for p in poses}, {DUCK})
        self.assertAlmostEqual(poses[-1].pose.t[0], 14.0, places=3)

    def test_the_entity_record_is_refreshed_every_nth_move_and_not_before(self):
        _, records = self._move(LATCH_EVERY_N_MOVES - 1)
        self.assertEqual(records, [],
                         "the expensive record should not be rewritten per move")
        _, records = self._move(1)
        self.assertEqual(len(records), 1,
                         f"and should be, on move {LATCH_EVERY_N_MOVES}")

    def test_a_late_joiner_is_at_most_n_minus_one_moves_stale(self):
        """
        The freshness bound, stated as the brief states it.

        Joining mid-animation costs you up to four moves. It does not cost you
        an unbounded amount, and the next pose on the stream corrects it.
        """
        self._move(LATCH_EVERY_N_MOVES)          # a clean refresh boundary
        poses, _ = self._move(LATCH_EVERY_N_MOVES - 1)
        latest = poses[-1].pose.t[0]

        joiner = tt.make_reader(_participant(DOMAIN), TOPIC_MODEL_ENTITY_V1,
                                Entity, MODEL_LATCHED.name)
        got, deadline = None, time.time() + 6
        while got is None and time.time() < deadline:
            for sample in tt.take_samples(joiner) or []:
                if sample.entity_id == DUCK:
                    got = sample
            time.sleep(0.02)
        self.assertIsNotNone(got)

        stale_by = abs(latest - got.pose.t[0]) / 0.5      # moves, at 0.5 m each
        print(f"\n  late join mid-animation: {stale_by:.0f} moves stale "
              f"(bound is {LATCH_EVERY_N_MOVES - 1})")
        self.assertLessEqual(stale_by, LATCH_EVERY_N_MOVES - 1 + 0.001)

    def test_the_latch_converges_to_the_stream_on_idle(self):
        """
        The rule that keeps rest state exact -- and the one general enough to
        belong in the sketch rather than in this demo.
        """
        poses, _ = self._move(2)                  # short of a refresh
        moving_to = poses[-1].pose.t[0]

        self.assertEqual(self.publisher.flush_idle(), [],
                         "nothing should flush while it is still moving")
        time.sleep(IDLE_FLUSH_S)
        started = time.time()
        self.assertEqual(self.publisher.flush_idle(), [DUCK])
        time.sleep(0.4)

        joiner = tt.make_reader(_participant(DOMAIN), TOPIC_MODEL_ENTITY_V1,
                                Entity, MODEL_LATCHED.name)
        got, deadline = None, time.time() + 6
        while got is None and time.time() < deadline:
            for sample in tt.take_samples(joiner) or []:
                if sample.entity_id == DUCK:
                    got = sample
            time.sleep(0.02)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got.pose.t[0], moving_to, places=3,
                               msg="after the flush the latch must be exact")
        print(f"  idle flush: latch exact {time.time() - started:.2f}s after "
              f"the flush, {IDLE_FLUSH_S}s after motion stopped")

    def test_a_thing_still_moving_is_never_called_idle(self):
        """
        The bug this caught, kept.

        A fixed threshold shorter than an entity's update interval makes every
        gap between updates look like a stop. Against a mover giving each duck
        a turn every 1.5 s and a one-second threshold, the service logged 94
        "it stopped moving" flushes while nothing had stopped -- republishing
        the entity record as often as the pose it is supposed to be cheaper
        than, which is the fast tier collapsing back into the slow one.

        So the threshold is derived from the cadence, and this moves at a
        deliberately slow one to prove it: a gap far longer than IDLE_FLUSH_S,
        with nothing flushed.
        """
        interval = IDLE_FLUSH_S * 1.5

        def step():
            self._x = getattr(self, "_x", 12.0) + 0.5
            self.publisher.move(DUCK, PoseSE3(t=[self._x, -14.0, -1.423],
                                              q=[0.0, 0.0, 0.0, 1.0]))

        # Two moves to establish a cadence. There is a warm-up cost here and
        # it is worth naming: the very first gap has nothing to be compared
        # against, so a slow entity gets one flush before the service has
        # learned how slow it is. That write is correct, just not economical --
        # it publishes the pose the entity is actually at.
        step()
        time.sleep(interval)
        self.assertEqual(self.publisher.flush_idle(), [DUCK],
                         "with no cadence measured yet, the floor applies")
        step()
        time.sleep(interval)

        for _ in range(3):
            step()
            time.sleep(interval)
            self.assertEqual(
                self.publisher.flush_idle(), [],
                f"a gap of {interval:.1f}s is this entity's normal cadence, "
                f"not evidence that it stopped")
        self.assertGreater(self.publisher.idle_threshold(DUCK), interval,
                           "the threshold should have adapted upward")
        print(f"  cadence: moving every {interval:.1f}s, idle threshold "
              f"adapted to {self.publisher.idle_threshold(DUCK):.1f}s")

        # And when it really does stop, it still converges.
        time.sleep(self.publisher.idle_threshold(DUCK))
        self.assertEqual(self.publisher.flush_idle(), [DUCK])

    def test_an_unmeasured_entity_falls_back_to_the_floor(self):
        """One move is not a cadence. Until there are two, the fixed floor is
        all there is to go on."""
        self.assertEqual(self.publisher.idle_threshold("ent:duck:west"),
                         IDLE_FLUSH_S)

    def test_flushing_twice_writes_once(self):
        """Idempotent, so the loop can call it every tick without republishing
        a still world at 10 Hz."""
        self._move(2)
        time.sleep(IDLE_FLUSH_S)
        self.assertEqual(self.publisher.flush_idle(), [DUCK])
        self.assertEqual(self.publisher.flush_idle(), [])


class LayerDecides(unittest.TestCase):
    """The treatment follows the entity's own `layer`, not its id."""

    def test_a_slow_entity_is_still_latched_on_every_move(self):
        participant = _participant(DOMAIN + 1)
        publisher = ModelPublisher(participant)
        for entity in seed_entities():
            publisher.publish_entity(entity)
        entities = tt.make_reader(participant, TOPIC_MODEL_ENTITY_V1, Entity,
                                  MODEL_LATCHED.name)
        time.sleep(0.6)
        tt.take_samples(entities)
        try:
            # The pond is STATIC. Moving it is odd but legal, and it must not
            # take the fast path.
            publisher.move("ent:pond:littlefield",
                           PoseSE3(t=[15.0, -14.0, -1.5], q=[0, 0, 0, 1]))
            time.sleep(0.5)
            seen = [s for s in (tt.take_samples(entities) or [])
                    if s.entity_id == "ent:pond:littlefield"]
            self.assertEqual(len(seen), 1,
                             "a non-FAST entity is latched immediately")
            self.assertEqual(publisher.flush_idle(), [],
                             "and has nothing pending to flush")
        finally:
            publisher.close()

    def test_the_ducks_declare_themselves_fast(self):
        for entity in seed_entities():
            if entity.entity_id.startswith("ent:duck"):
                self.assertEqual(entity.layer.name, "FAST")


if __name__ == "__main__":
    unittest.main()
