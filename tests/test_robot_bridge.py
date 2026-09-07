"""
The robot's northbound half.

`ent:robot:tb3` is owned by one writer, published OBSERVED because a
self-localizing robot is looking at the world rather than fusing other
people's claims about it, and marked UNOBSERVED -- not disposed -- when the
feed goes quiet.

That last distinction is the one worth being careful about. Disposing would
say the robot is gone. All that is actually known is that we have stopped
hearing from it, and the difference matters to anyone deciding whether to
drive somewhere: a stale pose presented as current is worse than an
acknowledged silence.
"""

import re
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import typed_transport as tt  # noqa: E402
from spatialdds_demo.qos_profiles import MODEL_FAST, MODEL_LATCHED  # noqa: E402
from spatialdds_demo.robot_bridge import (  # noqa: E402
    ENTITY_ID, ROBOT_CONTENT_ID, SOURCE_ID, TYPE_MOBILE_ROBOT, PlazaSim,
    RobotBridge, yaw_quaternion,
)
from spatialdds_demo.tempo import IDLE_FLOOR_S, LATCH_EVERY_N  # noqa: E402
from spatialdds_demo.topics import (  # noqa: E402
    TOPIC_MODEL_ENTITY_V1, TOPIC_MODEL_POSE_V1,
)
from spatialdds_idl.oarc_model import Entity, ModelPose  # noqa: E402

DOMAIN = 64


def _participant(domain_id: int):
    try:
        from cyclonedds.domain import DomainParticipant
        return DomainParticipant(domain_id)
    except Exception as exc:
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


class Ownership(unittest.TestCase):
    """
    One writer per key, asserted where it can be enforced.

    Structural, like the mover's: watching the bus shows only that it did not
    write something else today. The rule is that it cannot.
    """

    def test_it_writes_only_its_own_two_lanes(self):
        source = (REPO / "spatialdds_demo" / "robot_bridge.py").read_text()
        writers = re.findall(r"make_writer\(\s*\w+,\s*(\w+)", source)
        self.assertEqual(sorted(writers),
                         ["TOPIC_MODEL_ENTITY_V1", "TOPIC_MODEL_POSE_V1"],
                         f"the bridge should write its entity and its poses "
                         f"and nothing else; found {writers}")

    def test_it_writes_only_one_entity_id(self):
        source = (REPO / "spatialdds_demo" / "robot_bridge.py").read_text()
        self.assertEqual(source.count('entity_id=ENTITY_ID'), 2,
                         "one entity, one pose lane, one key")
        self.assertNotIn("ent:duck", source)
        self.assertNotIn("ent:pond", source)

    def test_it_cannot_dispose_its_entity(self):
        """
        UNOBSERVED is not absence, enforced where it can be.

        The behavioural test below watches for a dispose notification and is
        weaker than it looks: a dispose immediately followed by a rewrite
        leaves the instance alive again, and a reader taking a moment later
        sees only the rewrite. Mutating the bridge to dispose-then-republish
        passed that test. The rule is that the bridge never disposes this
        entity at all, which is a fact about the code.
        """
        source = (REPO / "spatialdds_demo" / "robot_bridge.py").read_text()
        self.assertNotIn(".dispose(", source,
                         "the bridge must never dispose the robot: silence "
                         "means we stopped hearing, not that it is gone")

    def test_it_reads_the_command_lane_but_never_writes_it(self):
        """
        It listens for `goto` and asks for nothing. The northbound half
        reports; the southbound half obeys. Neither publishes a command,
        which is what keeps the operator tools the only things asking.
        """
        source = (REPO / "spatialdds_demo" / "robot_bridge.py").read_text()
        self.assertIn("TOPIC_MODEL_COMMAND_V1", source)
        self.assertIn("make_reader(\n            participant, TOPIC_MODEL_COMMAND_V1",
                      source)


class WhatItPublishes(unittest.TestCase):
    def setUp(self):
        self.participant = _participant(DOMAIN)
        self.bridge = RobotBridge(self.participant)
        self.entities = tt.make_reader(
            self.participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        self.poses = tt.make_reader(
            self.participant, TOPIC_MODEL_POSE_V1, ModelPose, MODEL_FAST.name)
        time.sleep(0.5)
        tt.take_samples(self.entities)
        tt.take_samples(self.poses)

    def _drain(self, reader, seconds=0.6):
        got, deadline = [], time.time() + seconds
        while time.time() < deadline:
            got.extend(tt.take_samples(reader) or [])
            time.sleep(0.02)
        return got

    def test_the_first_pose_publishes_a_complete_entity(self):
        self.bridge.observed(20.0, -9.0, 0.0)
        seen = [e for e in self._drain(self.entities) if e.entity_id == ENTITY_ID]
        self.assertTrue(seen, "the robot should appear on its first pose")
        robot = seen[-1]
        self.assertEqual(robot.basis.name, "OBSERVED")
        self.assertEqual(robot.layer.name, "FAST")
        self.assertEqual(robot.state.name, "ACTIVE")
        self.assertEqual(robot.source_id, SOURCE_ID)
        self.assertEqual(robot.type_uris, [TYPE_MOBILE_ROBOT])
        self.assertEqual(list(robot.content_refs), [f"catalog:{ROBOT_CONTENT_ID}"])

    def test_it_appears_at_once_rather_than_on_the_latch_cadence(self):
        """
        A robot that existed only after five poses would be missing for a
        second on every start, which is the kind of gap people learn to
        distrust. Appearing is a change of state, not of position.
        """
        self.bridge.observed(20.0, -9.0, 0.0)
        self.assertTrue([e for e in self._drain(self.entities, 0.4)
                         if e.entity_id == ENTITY_ID])

    def test_every_pose_goes_out_on_the_tempo_lane(self):
        """
        Drained between writes, because the lane is KEEP_LAST(1).

        Four poses written in a tight loop reach a reader as one: BEST_EFFORT
        with a history of one is allowed to overwrite a sample nobody has
        taken yet, and that is the point of the profile -- a pose that missed
        its moment is worth less than the next one. Asserting "four arrive"
        would have been asserting against the QoS the lane was chosen for.
        """
        poses = []
        for i in range(4):
            self.bridge.observed(20.0 + i * 0.1, -9.0, 0.0)
            poses.extend(p for p in self._drain(self.poses, 0.25)
                         if p.entity_id == ENTITY_ID)
        self.assertGreaterEqual(len(poses), 3,
                                "the lane should carry the robot's motion")
        self.assertEqual({p.source_id for p in poses}, {SOURCE_ID})
        self.assertAlmostEqual(poses[-1].pose.t[0], 20.3, places=3,
                               msg="and the last one is the current one")

    def test_the_record_is_not_rewritten_for_every_pose(self):
        self.bridge.observed(20.0, -9.0, 0.0)          # the first, always sent
        self._drain(self.entities, 0.4)
        # Two short of the cadence: the first pose already counted towards it,
        # so LATCH_EVERY_N - 1 more would land exactly on the refresh.
        for i in range(LATCH_EVERY_N - 2):
            self.bridge.observed(20.0 + i * 0.1, -9.0, 0.0)
        self.assertEqual(
            [e for e in self._drain(self.entities, 0.4) if e.entity_id == ENTITY_ID],
            [], "the expensive record should not follow every pose")


class GoingQuiet(unittest.TestCase):
    """UNOBSERVED, and why it is not a dispose."""

    def setUp(self):
        self.participant = _participant(DOMAIN + 1)
        self.bridge = RobotBridge(self.participant)
        self.entities = tt.make_reader(
            self.participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        time.sleep(0.5)
        tt.take_samples(self.entities)

    def test_silence_is_measured_against_the_feed_s_own_cadence(self):
        """A 5 Hz feed is quiet after a second; a 0.2 Hz feed is not."""
        now = 1000.0
        for i in range(6):
            self.bridge.observed(20.0, -9.0, 0.0, now=now + i * 0.2)
        fast = self.bridge.silence_threshold()

        slow_bridge = RobotBridge(self.participant)
        for i in range(6):
            slow_bridge.observed(20.0, -9.0, 0.0, now=now + i * 5.0)
        slow = slow_bridge.silence_threshold()

        self.assertGreater(slow, fast * 3,
                           "a slow feed must not be called silent on a fast "
                           "feed's schedule")
        self.assertGreaterEqual(fast, IDLE_FLOOR_S)
        print(f"\n  silence threshold: {fast:.1f}s at 5 Hz, {slow:.1f}s at 0.2 Hz")

    def test_going_quiet_republishes_rather_than_disposes(self):
        now = 2000.0
        for i in range(4):
            self.bridge.observed(18.0, -9.0, 0.0, now=now + i * 0.2)
        tt.take_samples(self.entities)

        note = self.bridge.tick(now=now + 30.0)
        self.assertIn("unobserved", note or "")

        # Drained repeatedly across the transition rather than once after it.
        # A single late take sees only the surviving sample, which is how a
        # dispose-then-rewrite slipped past an earlier version of this.
        seen = []
        deadline = time.time() + 1.0
        while time.time() < deadline:
            seen.extend(tt.take_with_state(self.entities) or [])
            time.sleep(0.02)
        alive = [s for s in seen if s.data is not None
                 and s.data.entity_id == ENTITY_ID]
        disposed = [s for s in seen if s.data is None]
        self.assertTrue(alive, "the entity must still be published")
        self.assertFalse(disposed, "it must not be disposed: it is not gone")

        robot = alive[-1].data
        self.assertEqual(robot.state.name, "UNOBSERVED")
        self.assertIn("last seen", robot.state_reason)
        # The pose is kept: it is the last place anyone saw it.
        self.assertAlmostEqual(robot.pose.t[0], 18.0, places=3)

    def test_it_comes_back_immediately_rather_than_on_the_cadence(self):
        now = 3000.0
        self.bridge.observed(18.0, -9.0, 0.0, now=now)
        self.bridge.tick(now=now + 30.0)
        self.assertEqual(self.bridge.state, "UNOBSERVED")
        tt.take_samples(self.entities)

        self.bridge.observed(18.5, -9.0, 0.0, now=now + 31.0)
        self.assertEqual(self.bridge.state, "ACTIVE")
        time.sleep(0.5)
        seen = [e for e in (tt.take_samples(self.entities) or [])
                if e.entity_id == ENTITY_ID]
        self.assertTrue(seen, "recovery is a state change and must be sent at once")
        self.assertEqual(seen[-1].state.name, "ACTIVE")
        self.assertEqual(seen[-1].state_reason, "")

    def test_a_late_joiner_during_the_outage_sees_unobserved_not_absence(self):
        now = 4000.0
        for i in range(3):
            self.bridge.observed(17.0, -9.0, 0.0, now=now + i * 0.2)
        self.bridge.tick(now=now + 30.0)
        time.sleep(0.6)

        joiner = tt.make_reader(_participant(DOMAIN + 1), TOPIC_MODEL_ENTITY_V1,
                                Entity, MODEL_LATCHED.name)
        got, deadline = None, time.time() + 6
        while got is None and time.time() < deadline:
            for sample in tt.take_samples(joiner) or []:
                if sample.entity_id == ENTITY_ID:
                    got = sample
            time.sleep(0.02)
        self.assertIsNotNone(got, "a late joiner must find the robot")
        self.assertEqual(got.state.name, "UNOBSERVED")
        print(f"  late join during an outage: {got.state.name} — "
              f"{got.state_reason[:48]}…")


class Kinematics(unittest.TestCase):
    def test_identity_faces_east_like_everything_else_in_this_frame(self):
        self.assertEqual([round(v, 4) for v in yaw_quaternion(0)],
                         [0.0, 0.0, 0.0, 1.0])

    def test_the_sim_drives_towards_a_goal_and_stops_there(self):
        sim = PlazaSim(x=20.0, y=-9.0)
        sim.goal = (14.0, -14.0)
        for _ in range(200):
            sim.step(0.2)
            if sim.goal is None:
                break
        self.assertIsNone(sim.goal, "it should arrive")
        self.assertLess(abs(sim.x - 14.0), 0.2)
        self.assertLess(abs(sim.y - -14.0), 0.2)


if __name__ == "__main__":
    unittest.main()


class Goto(unittest.TestCase):
    """
    What it accepts, what it declines, and what it leaves alone.

    The distinction that matters is the third one. Several services read this
    lane, so a command about the ducks is not this bridge's to refuse -- and a
    malformed command about the robot is not somebody else's to ignore.
    """

    def setUp(self):
        self.participant = _participant(DOMAIN + 2)
        self.bridge = RobotBridge(self.participant)

    def _command(self, verb, subject, x=None, y=None):
        from spatialdds_idl.builtin import Time
        from spatialdds_idl.oarc_model import ModelCommand
        from spatialdds_idl.spatial.core import Aabb3, PoseSE3
        return ModelCommand(
            command_id="c", verb=verb, subject_id=subject, reason="",
            requester_id="tool:test",
            has_pose=x is not None,
            pose=PoseSE3(t=[x or 0.0, y or 0.0, 0.0], q=[0, 0, 0, 1]),
            has_extent=False,
            extent=Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0]),
            stamp=Time(sec=0, nanosec=0))

    def test_a_goal_is_accepted_once_the_robot_is_reporting(self):
        self.bridge.observed(22.0, -8.0, 0.0)
        note = self.bridge.handle_command(
            self._command("goto", ENTITY_ID, 14.0, -20.0))
        self.assertIn("accepted goto", note)
        self.assertEqual(self.bridge.goal, (14.0, -20.0))

    def test_a_goal_with_no_pose_is_declined(self):
        """A zeroed pose is a well-formed request to drive to the frame
        origin, which is somewhere in the plaza. Declining is the only
        reading that cannot be mistaken for obedience."""
        self.bridge.observed(22.0, -8.0, 0.0)
        note = self.bridge.handle_command(self._command("goto", ENTITY_ID))
        self.assertIn("declined", note)
        self.assertIn("no pose", note)
        self.assertIsNone(self.bridge.goal)

    def test_a_goal_is_declined_while_the_robot_is_unheard(self):
        """
        Not queued. A goal held for a robot that may never report again is a
        promise the bridge cannot keep, and the requester would have no way
        to learn that nothing was going to happen.
        """
        note = self.bridge.handle_command(
            self._command("goto", ENTITY_ID, 14.0, -20.0))
        self.assertIn("declined", note)
        self.assertIn("nothing to navigate from", note)
        self.assertIsNone(self.bridge.goal)

    def test_a_goal_is_declined_after_the_feed_goes_quiet(self):
        self.bridge.observed(22.0, -8.0, 0.0, now=100.0)
        self.bridge.tick(now=200.0)
        self.assertEqual(self.bridge.state, "UNOBSERVED")
        note = self.bridge.handle_command(
            self._command("goto", ENTITY_ID, 14.0, -20.0))
        self.assertIn("declined", note)

    def test_somebody_else_s_subject_is_left_alone(self):
        """Silence, not refusal: the ducks have an owner and it is not this."""
        self.bridge.observed(22.0, -8.0, 0.0)
        self.assertIsNone(self.bridge.handle_command(
            self._command("goto", "ent:duck:west", 1.0, 2.0)))
        self.assertIsNone(self.bridge.handle_command(
            self._command("move", "ent:duck:west", 1.0, 2.0)))
        self.assertIsNone(self.bridge.goal)

    def test_a_verb_it_does_not_implement_is_left_alone(self):
        self.bridge.observed(22.0, -8.0, 0.0)
        self.assertIsNone(self.bridge.handle_command(
            self._command("retire", ENTITY_ID, 1.0, 2.0)))

    def test_it_reports_what_it_accepted_not_what_was_sent(self):
        """The coordinates in the log are the ones it took, so a reader can
        tell a rounded or clamped goal from the one they asked for."""
        self.bridge.observed(22.0, -8.0, 0.0)
        note = self.bridge.handle_command(
            self._command("goto", ENTITY_ID, 14.25, -20.5))
        self.assertIn("(14.25, -20.50)", note)


class OneWriterPerKey(unittest.TestCase):
    """
    Two bridges would put one robot in two places.

    The demo stack can start a bridge with its own kinematic sim and the robot
    tier starts one driven by nav2. Both own `ent:robot:tb3`, and both running
    is the two-writers state Part 2 spent itself closing -- except the symptom
    would be a robot that teleports between two sims, not an error anyone can
    read. So a bridge looks before it writes.
    """

    def test_it_sees_a_bridge_that_is_already_publishing(self):
        from spatialdds_demo.robot_bridge import already_published
        participant = _participant(DOMAIN + 3)
        incumbent = RobotBridge(participant)
        incumbent.observed(22.0, -8.0, 0.0)
        time.sleep(0.6)
        self.assertEqual(already_published(_participant(DOMAIN + 3)), SOURCE_ID)

    def test_an_empty_bus_is_free_to_take(self):
        from spatialdds_demo.robot_bridge import already_published
        self.assertIsNone(already_published(_participant(DOMAIN + 4), settle=1.0))


class NavigatorDeclines(unittest.TestCase):
    """`goto` has a second way to be undeliverable: nothing to drive with."""

    class _Unavailable:
        def available(self):
            return "nav2 is not accepting goals (no navigate_to_pose server)"

        def send(self, x, y):
            raise AssertionError("must not be called when unavailable")

    class _Ready:
        def __init__(self):
            self.sent = None

        def available(self):
            return None

        def send(self, x, y):
            self.sent = (x, y)
            return f"nav2 accepted a goal at ({x:.2f}, {y:.2f})"

    def _command(self, x, y):
        from spatialdds_idl.builtin import Time
        from spatialdds_idl.oarc_model import ModelCommand
        from spatialdds_idl.spatial.core import Aabb3, PoseSE3
        return ModelCommand(
            command_id="c", verb="goto", subject_id=ENTITY_ID, reason="",
            requester_id="tool:test", has_pose=True,
            pose=PoseSE3(t=[x, y, 0.0], q=[0, 0, 0, 1]),
            has_extent=False,
            extent=Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0]),
            stamp=Time(sec=0, nanosec=0))

    def test_a_goal_is_declined_when_nothing_will_drive(self):
        """
        Accepting it would be worse than refusing. The robot is reporting its
        pose perfectly, so "no pose" is not the reason -- and a requester
        watching a stationary robot would have no way to learn why.
        """
        bridge = RobotBridge(_participant(DOMAIN + 5),
                             navigator=self._Unavailable())
        bridge.observed(22.0, -8.0, 0.0)
        note = bridge.handle_command(self._command(14.0, -20.0))
        self.assertIn("declined", note)
        self.assertIn("not accepting goals", note)
        self.assertIsNone(bridge.goal)

    def test_it_reports_what_the_navigator_accepted(self):
        navigator = self._Ready()
        bridge = RobotBridge(_participant(DOMAIN + 6), navigator=navigator)
        bridge.observed(22.0, -8.0, 0.0)
        note = bridge.handle_command(self._command(14.25, -20.5))
        self.assertEqual(navigator.sent, (14.25, -20.5))
        self.assertIn("nav2 accepted", note)
        self.assertIn("(14.25, -20.50)", note)

    def test_the_pose_check_still_comes_first(self):
        """A robot nobody can hear cannot be sent anywhere, whatever nav2
        thinks."""
        bridge = RobotBridge(_participant(DOMAIN + 7), navigator=self._Ready())
        note = bridge.handle_command(self._command(14.0, -20.0))
        self.assertIn("nothing to navigate from", note)
