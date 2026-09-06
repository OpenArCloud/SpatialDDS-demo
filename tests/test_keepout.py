"""
Which claims constrain a robot, and which must not.

The keep-out mask is the model's answer to "where may this thing not go". It
is built from `basis`, which is the argument for basis existing: a decoration
placed by anyone must not be able to close a route, and a second party's
opinion about where the water is must not be able to widen one.

The assertions worth being careful about are the negative ones. "AUTHORED
contributes nothing" passes trivially against this venue's AUTHORED entities,
because none of them has an extent -- so the test would pass for the wrong
reason and keep passing if the basis check were deleted. Every negative case
here uses an entity that *would* contribute if its basis were different.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo.keepout import (  # noqa: E402
    FREE, KEEPOUT_BASES, OCCUPIED, build_mask, contributing, describe,
)
from spatialdds_demo.model_service import seed_entities, venue_frame  # noqa: E402
from spatialdds_idl.builtin import Time  # noqa: E402
from spatialdds_idl.oarc_model import (  # noqa: E402
    Basis, Entity, LifecycleState, ModelLayer,
)
from spatialdds_idl.spatial.core import Aabb3, PoseSE3  # noqa: E402

ROBOT_ID = "ent:robot:tb3"


def boxed(entity_id: str, basis: Basis, lo=(0.0, 0.0), hi=(2.0, 2.0),
          state: LifecycleState = LifecycleState.ACTIVE) -> Entity:
    """An entity with an extent, so only its basis decides its fate."""
    return Entity(
        entity_id=entity_id, basis=basis, type_uris=[], layer=ModelLayer.STATIC,
        frame_ref=venue_frame(), has_pose=True,
        pose=PoseSE3(t=[(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0.0],
                     q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=True,
        extent=Aabb3(min_xyz=[lo[0], lo[1], -1.0], max_xyz=[hi[0], hi[1], 1.0]),
        properties=[], external_refs=[], content_refs=[],
        state=state, state_reason="", source_id="svc:test",
        stamp=Time(sec=0, nanosec=0))


class WhichClaimsConstrain(unittest.TestCase):
    def test_only_declared_legislates(self):
        """OBSERVED locates; DECLARED legislates."""
        self.assertEqual(list(KEEPOUT_BASES), ["DECLARED"])

    def test_an_observed_region_contributes_nothing(self):
        """
        The fountain's extent is its bounding box -- "a claim about where the
        fountain is, not a model of its geometry", as the seeder puts it. Read
        as keep-out it forbade 916 m2 of mostly-dry plaza and swallowed the
        pond whole, so reshaping the water changed the robot's world by zero
        cells. Same box, DECLARED, still constrains: see the control below.
        """
        entities = [boxed("ent:fountain:littlefield", Basis.OBSERVED)]
        self.assertEqual(contributing(entities), [])

    def test_an_authored_region_contributes_nothing(self):
        """
        A decoration is not an obstacle.

        Note the box: this entity *would* be keep-out if its basis were
        DECLARED, which is what makes the assertion mean anything. The venue's
        real AUTHORED entities -- ducks, a gnome -- have no extent at all, so
        testing with those would pass whether or not the basis check existed.
        """
        entities = [boxed("ent:prop:banner", Basis.AUTHORED)]
        self.assertEqual(contributing(entities), [])
        self.assertIsNone(build_mask(entities))

    def test_a_derived_region_contributes_nothing_either(self):
        """
        Someone else's opinion about where the water is does not get to widen
        a keep-out. Letting it would mean any publisher could close a route by
        asserting a bigger extent, which is a policy nobody has made.
        """
        entities = [boxed("ent:pond:observed", Basis.DERIVED)]
        self.assertEqual(contributing(entities), [])

    def test_the_same_box_declared_does_constrain(self):
        """The control for both negatives above: geometry identical, basis
        different, outcome opposite."""
        entities = [boxed("ent:pond:littlefield", Basis.DECLARED)]
        self.assertEqual([e.entity_id for e in contributing(entities)],
                         ["ent:pond:littlefield"])

    def test_a_robot_does_not_avoid_itself(self):
        entities = [boxed(ROBOT_ID, Basis.OBSERVED),
                    boxed("ent:pond:littlefield", Basis.DECLARED, hi=(3.0, 3.0))]
        ids = [e.entity_id for e in contributing(entities, exclude_ids=(ROBOT_ID,))]
        self.assertEqual(ids, ["ent:pond:littlefield"])

    def test_a_retired_or_unobserved_region_stops_constraining(self):
        """A bound nobody is asserting any more is not a fact about now."""
        for state in (LifecycleState.RETIRED, LifecycleState.UNOBSERVED):
            with self.subTest(state=state.name):
                entities = [boxed("ent:pond:littlefield", Basis.DECLARED,
                                  state=state)]
                self.assertEqual(contributing(entities), [])

    def test_a_point_is_not_a_region(self):
        point = boxed("ent:duck:west", Basis.DECLARED)
        point.has_extent = False
        self.assertEqual(contributing([point]), [])


class TheMask(unittest.TestCase):
    def test_the_venue_declares_two_keep_outs_and_locates_a_third_thing(self):
        mask = build_mask(seed_entities(), exclude_ids=(ROBOT_ID,))
        self.assertIsNotNone(mask)
        self.assertEqual(list(mask.contributors),
                         ["ent:monument:littlefield", "ent:pond:littlefield"])
        self.assertNotIn("ent:fountain:littlefield", mask.contributors)
        print(f"\n  {describe(mask)}")

    def test_the_robot_starts_outside_every_keep_out(self):
        """
        It could not, under the old policy: (22.0, -8.0) is inside the
        fountain's bounding box, so the robot began life in a forbidden cell.
        """
        from spatialdds_demo.robot_bridge import START_XY
        mask = build_mask(seed_entities(), exclude_ids=(ROBOT_ID,))
        self.assertEqual(mask.cell(*START_XY), FREE,
                         f"the robot starts at {START_XY}, which must be legal")

    def test_the_monument_is_declared_because_avoidance_is_policy_here(self):
        """
        There is no lidar in this demo. A robot planning across a
        perception-free plaza would drive through the sculpture, which reads
        as broken and is dishonest the other way: a real robot's sensors would
        refuse. So the venue declares it.
        """
        monument = next(e for e in seed_entities()
                        if e.entity_id == "ent:monument:littlefield")
        self.assertEqual(monument.basis.name, "DECLARED")
        mask = build_mask(seed_entities())
        self.assertEqual(mask.cell(8.0, -14.0), OCCUPIED)

    def test_points_inside_a_declared_bound_are_occupied(self):
        mask = build_mask(seed_entities())
        # Middle of the pond, and a corner of it.
        self.assertEqual(mask.cell(14.0, -14.0), OCCUPIED)
        self.assertEqual(mask.cell(9.6, -17.9), OCCUPIED)

    def test_points_outside_every_bound_are_free(self):
        mask = build_mask(seed_entities())
        self.assertEqual(mask.cell(40.0, -40.0), FREE)   # off the mask
        self.assertEqual(mask.cell(-5.0, -24.0), FREE)   # on it, in the margin

    def test_where_the_ducks_are_is_free_of_their_doing(self):
        """
        The demonstration, as an assertion.

        The ducks sit inside the pond, so their cells are occupied -- by the
        pond. Removing the ducks changes nothing, which is what "a decoration
        is not an obstacle" means when the decoration is standing in water.
        """
        everything = build_mask(seed_entities())
        without_ducks = build_mask(
            [e for e in seed_entities() if not e.entity_id.startswith("ent:duck")])
        self.assertEqual(everything.data, without_ducks.data)
        self.assertEqual(everything.contributors, without_ducks.contributors)

    def test_no_mask_is_not_an_empty_mask(self):
        """
        Publishing a blank grid would tell a planner everywhere is clear on
        the authority of a model that said nothing. Different statements.
        """
        self.assertIsNone(build_mask([]))
        self.assertIn("nothing in the model constrains", describe(None))

    def test_shrinking_the_pond_shrinks_the_keep_out(self):
        entities = seed_entities()
        before = build_mask(entities).occupied_cells
        pond = next(e for e in entities if e.entity_id == "ent:pond:littlefield")
        pond.extent = Aabb3(min_xyz=[13.0, -15.0, -2.0], max_xyz=[16.0, -12.0, -1.0])
        after = build_mask(entities).occupied_cells
        self.assertLess(after, before)
        print(f"  reshape: {before} occupied cells -> {after}")


if __name__ == "__main__":
    unittest.main()
