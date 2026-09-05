"""
The mover: a consumer that produces.

It reads the model like any client and asks the authority for changes on the
command lane. Two properties are worth pinning and one is worth pinning
structurally:

* it stays inside whatever bounds the model currently declares, with no
  knowledge of ponds or water beyond "the box the model says";
* it selects by type, so a fourth duck swims without editing it;
* it never writes a model topic -- which is a claim about code, not about
  behaviour, so the test reads the source rather than watching the bus.
"""

import random
import re
import sys
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import typed_transport as tt  # noqa: E402
from spatialdds_demo.duck_mover import (  # noqa: E402
    INSET_M, DuckMover, clamp_into, heading_quaternion,
)
from spatialdds_demo.model_service import (  # noqa: E402
    ModelPublisher, seed_entities, seed_relationships,
)
from spatialdds_demo.qos_profiles import MODEL_COMMAND, MODEL_LATCHED  # noqa: E402
from spatialdds_demo.topics import (  # noqa: E402
    TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1,
)
from spatialdds_idl.oarc_model import Entity, ModelCommand  # noqa: E402
from spatialdds_idl.spatial.core import Aabb3  # noqa: E402

DOMAIN = 56
POND = Aabb3(min_xyz=[9.5, -18.0, -2.0], max_xyz=[20.0, -10.0, -1.0])


def _participant(domain_id: int):
    try:
        from cyclonedds.domain import DomainParticipant
        return DomainParticipant(domain_id)
    except Exception as exc:
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


class Geometry(unittest.TestCase):
    def test_a_point_outside_is_pulled_in_past_the_inset(self):
        x, y = clamp_into(30.0, -30.0, POND)
        self.assertAlmostEqual(x, POND.max_xyz[0] - INSET_M)
        self.assertAlmostEqual(y, POND.min_xyz[1] + INSET_M)

    def test_a_point_inside_is_left_alone(self):
        self.assertEqual(clamp_into(15.0, -14.0, POND), (15.0, -14.0))

    def test_a_box_too_small_for_the_inset_gives_up_the_inset_not_the_duck(self):
        """
        Better a duck on the rim of a pond too small to hold it than a duck in
        the plaza. Reachable in P3.4, where the pond is deliberately shrunk.
        """
        tiny = Aabb3(min_xyz=[10.0, -12.0, -2.0], max_xyz=[11.0, -11.0, -1.0])
        x, y = clamp_into(50.0, 50.0, tiny)
        self.assertGreaterEqual(x, tiny.min_xyz[0])
        self.assertLessEqual(x, tiny.max_xyz[0])
        self.assertGreaterEqual(y, tiny.min_xyz[1])
        self.assertLessEqual(y, tiny.max_xyz[1])

    def test_heading_matches_the_frame_convention_the_seeder_uses(self):
        """Identity faces east here, so north-east must reproduce the
        quaternion the seeder hand-wrote for Skipper."""
        self.assertEqual([round(v, 4) for v in heading_quaternion(1, 1, [0, 0, 0, 1])],
                         [0.0, 0.0, 0.3827, 0.9239])
        self.assertEqual([round(v, 4) for v in heading_quaternion(1, 0, [0, 0, 0, 1])],
                         [0.0, 0.0, 0.0, 1.0])

    def test_a_duck_holds_its_heading_rather_than_redrawing_it(self):
        """
        The difference between swimming and jittering.

        A direction drawn afresh every step is a random walk: displacement
        grows with the square root of the step count, so at half a metre six
        times a second three ducks spend a minute vibrating where they
        started. Measured over 360 steps, uniform headings net about 18 m of
        travel and persistent ones about 51 m -- and the second looks like a
        duck crossing a pond while the first looks like a rendering fault.

        Asserted as a property of the path, not of the constant: consecutive
        steps must mostly continue in a similar direction.
        """
        import math

        from spatialdds_demo.duck_mover import TURN_RADIANS
        from spatialdds_idl.spatial.core import PoseSE3

        mover = DuckMover.__new__(DuckMover)      # geometry only; no bus.
        mover._rng = random.Random(9)
        mover._heading = {}

        duck = next(e for e in seed_entities() if e.entity_id == "ent:duck:west")
        wide = Aabb3(min_xyz=[-500.0, -500.0, -2.0], max_xyz=[500.0, 500.0, -1.0])
        headings = []
        for _ in range(40):
            before = list(duck.pose.t)
            duck.pose = mover.step_for(duck, wide)
            headings.append(math.atan2(duck.pose.t[1] - before[1],
                                       duck.pose.t[0] - before[0]))

        turns = [abs((b - a + math.pi) % (2 * math.pi) - math.pi)
                 for a, b in zip(headings, headings[1:])]
        self.assertLessEqual(max(turns), TURN_RADIANS + 1e-6,
                             "a duck may not spin between steps")
        # And it goes somewhere: forty half-metre steps of a persistent walk
        # cover far more ground than the ~3 m a random walk would average.
        travelled = math.hypot(duck.pose.t[0] - seed_entities()[3].pose.t[0],
                               duck.pose.t[1] - seed_entities()[3].pose.t[1])
        self.assertGreater(travelled, 6.0,
                           "forty steps should have taken it somewhere")

    def test_hitting_the_edge_turns_it_around_rather_than_grinding_along(self):
        """A duck pressed against a boundary by a heading that points out of
        the water would sit there until the random turn walked it back."""
        import math

        mover = DuckMover.__new__(DuckMover)
        mover._rng = random.Random(3)
        mover._heading = {"ent:duck:west": 0.0}       # due east, at the edge

        duck = next(e for e in seed_entities() if e.entity_id == "ent:duck:west")
        from spatialdds_idl.spatial.core import PoseSE3
        duck.pose = PoseSE3(t=[POND.max_xyz[0] - INSET_M, -14.0, -1.423],
                            q=[0, 0, 0, 1])
        mover.step_for(duck, POND)
        turned = mover._heading["ent:duck:west"]
        # Away from the wall it just hit: the eastward component must now be
        # negative. `abs(cos)` was the first attempt and rejected "turned
        # right around" along with "did not turn at all" -- the two outcomes
        # it exists to tell apart.
        self.assertLess(math.cos(turned), 0.0,
                        "after a clamp it should be heading away from the edge")

    def test_a_zero_step_keeps_the_facing_it_had(self):
        self.assertEqual(heading_quaternion(0, 0, [0, 0, 0.5, 0.5]), [0, 0, 0.5, 0.5])


class WritesNothing(unittest.TestCase):
    def test_the_mover_opens_no_writer_on_a_model_topic(self):
        """
        Structural, because it is a claim about the code.

        Watching the bus would only show that it did not happen to write
        during the test. The single-writer rule is that it *cannot*.
        """
        source = (REPO / "spatialdds_demo" / "duck_mover.py").read_text()
        writers = re.findall(r"make_writer\(\s*\w+,\s*(\w+)", source)
        self.assertEqual(writers, ["TOPIC_MODEL_COMMAND_V1"],
                         f"the mover must only write commands; found {writers}")
        self.assertNotIn("TOPIC_MODEL_RELATIONSHIP_V1", source)


class Wandering(unittest.TestCase):
    """
    The mover, the service and the bus, end to end.

    The service's command loop runs in this test rather than in a subprocess,
    so what is exercised is the real handler on real samples.
    """

    def _stack(self, domain):
        participant = _participant(domain)
        publisher = ModelPublisher(participant)
        entities = seed_entities()
        for entity in entities:
            publisher.publish_entity(entity)
        for relationship in seed_relationships(entities):
            publisher.publish_relationship(relationship)
        commands = tt.make_reader(
            participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
        return participant, publisher, commands

    def test_the_ducks_wander_and_never_leave_the_declared_water(self):
        participant, publisher, commands = self._stack(DOMAIN)
        mover = DuckMover(_participant(DOMAIN), rng=random.Random(7))
        time.sleep(0.6)

        seen = []
        try:
            for _ in range(60):                       # a sustained window
                mover.tick()
                for command in tt.take_samples(commands) or []:
                    publisher.handle_command(command)
                    seen.append((command.subject_id,
                                 command.pose.t[0], command.pose.t[1]))
                time.sleep(0.02)

            self.assertGreater(len(seen), 20, "the mover should have asked for moves")
            self.assertEqual({s[0] for s in seen},
                             {"ent:duck:catalog-pose", "ent:duck:west",
                              "ent:duck:east"},
                             "every duck should get a turn, selected by type")

            pond = next(e for e in seed_entities()
                        if e.entity_id == "ent:pond:littlefield").extent
            for entity_id, x, y in seen:
                with self.subTest(entity_id=entity_id, x=round(x, 2), y=round(y, 2)):
                    self.assertGreaterEqual(x, pond.min_xyz[0] + INSET_M - 1e-6)
                    self.assertLessEqual(x, pond.max_xyz[0] - INSET_M + 1e-6)
                    self.assertGreaterEqual(y, pond.min_xyz[1] + INSET_M - 1e-6)
                    self.assertLessEqual(y, pond.max_xyz[1] - INSET_M + 1e-6)

            spread = max(s[1] for s in seen) - min(s[1] for s in seen)
            print(f"\n  mover: {len(seen)} moves applied, all inside the declared "
                  f"water; x spread {spread:.1f} m")
            self.assertGreater(spread, 0.5, "they should actually be moving")
        finally:
            publisher.close()

    def test_stopping_the_mover_freezes_the_ducks_for_everyone(self):
        """
        No ghost writer. The Part 2 lesson, from the other side.

        The mover asks; the service owns. So when the mover stops there is
        nothing of its to expire, nothing latched under its name, and no
        second answer to where a duck is. A reader created *after* it stopped
        must agree with the world it left behind -- which would not hold if
        the mover had been writing poses itself.
        """
        participant, publisher, commands = self._stack(DOMAIN + 2)
        mover = DuckMover(_participant(DOMAIN + 2), rng=random.Random(3))
        time.sleep(0.6)
        try:
            for _ in range(40):
                mover.tick()
                for command in tt.take_samples(commands) or []:
                    publisher.handle_command(command)
                time.sleep(0.02)

            # The mover stops. Nothing else does.
            del mover
            for _ in range(10):
                for command in tt.take_samples(commands) or []:
                    publisher.handle_command(command)
                time.sleep(0.05)

            def read_world():
                reader = tt.make_reader(_participant(DOMAIN + 2),
                                        TOPIC_MODEL_ENTITY_V1, Entity,
                                        MODEL_LATCHED.name)
                got, deadline = {}, time.time() + 6
                while time.time() < deadline and len(got) < 5:
                    for sample in tt.take_samples(reader) or []:
                        got[sample.entity_id] = tuple(
                            round(v, 6) for v in sample.pose.t)
                    time.sleep(0.02)
                return got

            first = read_world()
            self.assertEqual(len(first), 5)
            time.sleep(1.5)
            second = read_world()

            self.assertEqual(first, second,
                             "nothing may move once the mover has stopped")
            ducks = {k: v for k, v in first.items() if k.startswith("ent:duck")}
            print(f"  freeze: {len(ducks)} ducks still, and a reader that "
                  f"arrived afterwards sees the same poses")
        finally:
            publisher.close()

    def test_a_shrunk_pond_crowds_them_without_any_duck_to_water_code(self):
        """
        The P3.4 moment, asserted here rather than only screenshotted there.

        Nothing in the mover knows what a pond is. It reads a box off the
        model and clamps into it, so making the box smaller is the entire
        mechanism.
        """
        participant, publisher, commands = self._stack(DOMAIN + 1)
        mover = DuckMover(_participant(DOMAIN + 1), rng=random.Random(11))
        time.sleep(0.6)
        small = Aabb3(min_xyz=[10.0, -16.0, -2.0], max_xyz=[14.0, -12.0, -1.0])
        try:
            publisher.set_extent("ent:pond:littlefield", small)
            time.sleep(0.6)

            asked = []
            for _ in range(80):
                mover.tick()
                for command in tt.take_samples(commands) or []:
                    publisher.handle_command(command)
                    asked.append((command.pose.t[0], command.pose.t[1]))
                time.sleep(0.02)

            self.assertGreater(len(asked), 20)
            late = asked[len(asked) // 2:]        # once they have had time to swim in
            for x, y in late:
                with self.subTest(x=round(x, 2), y=round(y, 2)):
                    self.assertGreaterEqual(x, small.min_xyz[0] - 1e-6)
                    self.assertLessEqual(x, small.max_xyz[0] + 1e-6)
                    self.assertGreaterEqual(y, small.min_xyz[1] - 1e-6)
                    self.assertLessEqual(y, small.max_xyz[1] + 1e-6)
            print(f"  shrink: {len(late)} later moves, all inside the smaller water")
        finally:
            publisher.close()


if __name__ == "__main__":
    unittest.main()
