"""
The demo's robot lane, which runs on `spatialdds_ros`.

**What moved and what did not.** As of 2026-10-19 the lanes, the cadence, the
latching, the UNOBSERVED transition, the declines and the refusal persistence
all belong to the library, and the library's own suite tests them — 153 tests
against a real bus. Copies here would be two suites asserting the same thing
about code this repository does not own.

What stays is what the library cannot know: this venue's geometry (`PlazaSim`
and its mask), this venue's *configuration* of the library, and the
integration — that the record the demo publishes is the record its page
expects, and that two services sharing a command lane each handle a command
exactly once.

The classes that left: `Ownership`, `WhatItPublishes`,
`ARefusalReachesWhoeverAsked`, `GoingQuiet`, `Goto`, `OneWriterPerKey`,
`NavigatorDeclines`. Each is now in `spatialdds-ros`'s `test/test_adapter.py`.
"""

import re
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# `yaw_quaternion` now comes from the library, which owns the convention; the
# demo's copy retired with `RobotBridge`.
from spatialdds_ros.frames import yaw_quaternion                  # noqa: E402

from spatialdds_demo.keepout import build_mask                    # noqa: E402
from spatialdds_demo.model_service import venue_frame             # noqa: E402
from spatialdds_demo.robot_bridge import ENTITY_ID, PlazaSim      # noqa: E402
from spatialdds.dds.types.builtin import Time                         # noqa: E402
from spatialdds.dds.types.spatial.core import Aabb3, PoseSE3          # noqa: E402
from spatialdds.dds.types.spatial.owm import (                        # noqa: E402
    Basis, Entity, LifecycleState, ModelLayer,
)


def _declared_box(entity_id: str, lo, hi) -> Entity:
    """A DECLARED entity with an extent --- the only kind that legislates.

    Local rather than imported from `test_keepout.py`: a test module is not a
    fixture library, and the pond's real numbers live in
    `spatialdds_demo.model_service`, not here. These are a *fixture* shaped
    like it on purpose, so enlarging the real pond cannot silently change what
    this test means.
    """
    return Entity(
        entity_id=entity_id, basis=Basis.DECLARED, type_uris=[],
        layer=ModelLayer.STATIC, frame_ref=venue_frame(), has_pose=True,
        pose=PoseSE3(t=[(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0.0],
                     q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=True,
        extent=Aabb3(min_xyz=[lo[0], lo[1], -1.0], max_xyz=[hi[0], hi[1], 1.0]),
        properties=[], external_refs=[], content_refs=[],
        state=LifecycleState.ACTIVE, state_reason="", source_id="svc:test",
        stamp=Time(sec=0, nanosec=0))

DOMAIN = 64


def _participant(domain_id: int):
    try:
        from cyclonedds.domain import DomainParticipant
        return DomainParticipant(domain_id)
    except Exception as exc:
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")










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
        # `goal is None` does not mean "arrived": it is also how the sim
        # records giving up. Asserted explicitly because this test read as an
        # arrival test while being satisfied by either outcome -- it only
        # passed for the right reason because no keep-out was set.
        self.assertIsNone(sim.abandoned,
                          "a clear path must not report an abandoned goal")
        self.assertLess(abs(sim.x - 14.0), 0.2)
        self.assertLess(abs(sim.y - -14.0), 0.2)

    def test_a_goal_behind_the_water_is_abandoned_out_loud(self):
        """
        The silence a web client met: 202 accepted, then a motionless robot.

        `handle_command` declines a goal nobody will act on so the requester
        is never left guessing, and `step` used to undo that by dropping a
        blocked goal without a word. The reason cannot be known when the
        command is accepted -- it depends on where the robot is when it meets
        the boundary -- so it has to arrive from the mover.
        """
        sim = PlazaSim(x=12.0, y=-22.0)      # south of the declared pond
        sim.keepout = build_mask([_declared_box("ent:pond:test",
                                                (-1.5, -20.0), (25.5, -4.75))],
                                 exclude_ids=(ENTITY_ID,))
        self.assertIsNotNone(sim.keepout, "the fixture must declare water")
        sim.goal = (12.0, -2.0)              # due north, straight through it
        for _ in range(200):
            sim.step(0.2)
            if sim.goal is None:
                break
        self.assertIsNone(sim.goal, "the blocked goal is dropped")
        self.assertIsNotNone(sim.abandoned,
                             "and dropping it must be reported, not silent")
        self.assertIn("declared keep-out", sim.abandoned)
        self.assertIn("run the nav2 tier", sim.abandoned,
                      "the note should say what does plan around it")
        # Where it stopped, stated as the property rather than a number:
        # it is standing somewhere the declaration permits. Checking a
        # hand-computed y bound got this wrong once already --- the margin is
        # the water's edge *plus the robot's footprint*, which `blocked`
        # already knows and a literal in a test has to be kept in step with.
        self.assertFalse(sim.blocked(sim.x, sim.y),
                         "it must come to rest somewhere it is allowed to be")
        self.assertGreater(sim.y, -22.0,
                           "it should have made progress before stopping")
        self.assertLess(sim.y, -2.0, "and nowhere near the goal it gave up on")


class TheVenueConfiguresTheLibrary(unittest.TestCase):
    """
    `venue_config` is the whole difference between "the library's behaviour"
    and "this demo's", so it is asserted field by field.
    """

    def setUp(self):
        from spatialdds_demo.robot_bridge import venue_config
        self.config = venue_config(domain_id=7)

    def test_the_catalog_reference_is_configured(self):
        """
        **The field the adoption waited for.** `web/src/spatialdds_bridge.ts`
        resolves a `catalog:` reference to an asset and renders a model; with
        no reference it renders a marker. The library had nowhere to carry
        this until 0.2.0, which is why the swap was deferred — see
        `docs/ros-adoption-blocker.md`.
        """
        from spatialdds_demo.robot_bridge import ROBOT_CONTENT_ID
        self.assertEqual(self.config.content_refs,
                         (f"catalog:{ROBOT_CONTENT_ID}",))

    def test_it_runs_anchorless_so_the_scene_keeps_the_claim(self):
        """
        `model_service` publishes this frame's `FrameTransform`. A second
        publisher would be two services asserting where the venue sits.
        """
        self.assertFalse(self.config.publishes_georeference)
        self.assertIsNone(self.config.anchor)

    def test_the_frame_is_the_venue_frame(self):
        from spatialdds_demo.model_service import VENUE_FRAME_FQN
        self.assertEqual(self.config.frame_id, VENUE_FRAME_FQN)

    def test_the_frame_ref_is_identical_to_the_venues_own(self):
        """
        Both derive the uuid from the fqn, so the robot's pose is in the
        *same* frame the venue describes rather than one that shares a name.
        Checked field by field because a difference here would be invisible:
        two frames with one name place their contents in two worlds.
        """
        import dataclasses
        from spatialdds_demo.model_service import venue_frame
        from spatialdds_ros.frames import robot_frame
        theirs, ours = venue_frame(), robot_frame(self.config.frame_id)
        for field in dataclasses.fields(theirs):
            with self.subTest(field=field.name):
                a, b = getattr(theirs, field.name), getattr(ours, field.name)
                self.assertEqual(getattr(a, "name", a), getattr(b, "name", b))

    def test_the_identity_is_what_the_demo_has_always_published(self):
        from spatialdds_demo.robot_bridge import (
            ENTITY_ID, SOURCE_ID, TYPE_MOBILE_ROBOT)
        self.assertEqual(self.config.entity_id, ENTITY_ID)
        self.assertEqual(self.config.effective_source_id, SOURCE_ID)
        self.assertEqual(self.config.type_uri, TYPE_MOBILE_ROBOT)
        self.assertEqual(self.config.effective_label, "TurtleBot")

    def test_the_ground_height_is_the_plazas(self):
        from spatialdds_demo.plaza import GROUND_Z
        self.assertEqual(self.config.ground_z, GROUND_Z)

    def test_the_domain_is_passed_through(self):
        """The demo runs on domain 1; the library defaults to 0."""
        self.assertEqual(self.config.domain, 7)


class OneCommandIsHandledExactlyOnce(unittest.TestCase):
    """
    Seam 4, proven both ways.

    `model_service` owns the venue and the library owns the robot; both read
    every command. The ruling asked for a test in both directions: handled
    exactly once when the lane is up, and demonstrably nobody's when it is
    down — the latter being pre-existing and filed rather than fixed, but
    worth pinning so it is a known property instead of a surprise.

    No bus needed: both handlers are pure functions of a command, which is
    what makes this cheap enough to assert exhaustively.
    """

    @staticmethod
    def _command(verb: str, subject: str):
        from spatialdds.dds.types.builtin import Time
        from spatialdds.dds.types.spatial.core import Aabb3, PoseSE3
        from spatialdds.dds.types.spatial.owm import ModelCommand
        return ModelCommand(
            command_id="cmd:test:1", verb=verb, subject_id=subject,
            reason="", requester_id="test", has_pose=True,
            pose=PoseSE3(t=[1.0, 2.0, 0.0], q=[0.0, 0.0, 0.0, 1.0]),
            has_extent=False,
            extent=Aabb3(min_xyz=[0.0] * 3, max_xyz=[0.0] * 3),
            stamp=Time(sec=0, nanosec=0))

    def _venue_answer(self, command) -> bool:
        """Does `model_service` claim this command? Without a bus."""
        from spatialdds_demo import model_service as ms

        class _Owning:
            # `owns` is the whole of the filtering; everything else about the
            # publisher is irrelevant to which service answers. `retire` is a
            # stub because a `retire` command reaches it *after* the filter —
            # what is under test here is which service answers, not what the
            # answer does.
            def owns(self, entity_id):
                return entity_id in ("ent:pond:littlefield", "ent:duck:east")

            def retire(self, entity_id, reason):
                return []
        note = ms.ModelPublisher.handle_command(_Owning(), command)
        return note is not None

    def _robot_answer(self, command) -> bool:
        """And does the library's adapter? Also without a bus."""
        from spatialdds_ros.adapter import RobotAdapter
        from spatialdds_demo.robot_bridge import venue_config

        class _Standin:
            config = venue_config(1)
            _entity = object()        # it has reported a pose
            _silent = False
            _navigator = None
            _goal = None
            _nav_status = None
        note = RobotAdapter.handle_command(_Standin(), command)
        return note is not None

    def test_a_goto_for_the_robot_is_the_librarys_alone(self):
        command = self._command("goto", "ent:robot:tb3")
        self.assertFalse(self._venue_answer(command),
                         "the venue does not own the robot, so it is silent")
        self.assertTrue(self._robot_answer(command))

    def test_a_venue_verb_is_the_venues_alone(self):
        command = self._command("set_extent", "ent:pond:littlefield")
        self.assertTrue(self._venue_answer(command))
        self.assertFalse(self._robot_answer(command),
                         "not this robot's subject, so the library is silent")

    def test_a_goto_for_a_venue_entity_is_the_venues_alone(self):
        """It declines with "unknown verb" — one answer, and a reason."""
        command = self._command("goto", "ent:duck:east")
        self.assertTrue(self._venue_answer(command))
        self.assertFalse(self._robot_answer(command))

    def test_every_case_is_answered_by_exactly_one_service(self):
        """
        The property, stated as a property. Each row must sum to one — two
        answers is the system contradicting itself, zero is a command that
        vanished.
        """
        cases = [("goto", "ent:robot:tb3"),
                 ("set_extent", "ent:pond:littlefield"),
                 ("goto", "ent:duck:east"),
                 ("retire", "ent:duck:east")]
        for verb, subject in cases:
            with self.subTest(verb=verb, subject=subject):
                command = self._command(verb, subject)
                answers = (int(self._venue_answer(command))
                           + int(self._robot_answer(command)))
                self.assertEqual(answers, 1,
                                 f"{verb} {subject} was answered {answers} "
                                 f"times")

    def test_with_the_robot_lane_down_a_goto_is_nobodys(self):
        """
        **Pre-existing, filed, not fixed.** The gateway's 202 honestly means
        "published"; whether an unconsumed command deserves a watchdog is a
        design question for another day. Pinned here so it is a known
        property rather than a surprise, and so a future watchdog has a test
        to change.
        """
        command = self._command("goto", "ent:robot:tb3")
        self.assertFalse(self._venue_answer(command),
                         "the venue is silent about a robot it does not own, "
                         "and with no robot lane running nothing else answers")


if __name__ == "__main__":
    unittest.main()






