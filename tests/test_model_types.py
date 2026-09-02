"""
`oarc_model` — the demo-local world model layer, at the type level.

Demo-local and non-normative: this is not SpatialDDS 1.7 and has no registry
row. See idl/demo/oarc_model.idl for the guard rails.

What these cover is the seam the rest of the layer is built on: an Entity or
Relationship has to survive both hops without losing anything — CDR onto the
bus and back, and typed-to-JSON for the bridge's `/ws` and snapshot clients.

The guard fields get their own tests because they are where this shape can go
wrong quietly. `has_pose` false still carries a `pose` member on the wire, so
"absent" and "present but zero" are the same bytes and differ only by a flag a
consumer has to remember to read. A fountain with no extent and a fountain
with a zero-sized extent are not the same claim.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo.json_mapping import from_json, to_json  # noqa: E402
from spatialdds_idl.builtin import Time  # noqa: E402
from spatialdds_idl.oarc_model import (  # noqa: E402
    Basis, Entity, LifecycleState, ModelLayer, Relationship,
)
from spatialdds_idl.spatial.common import CoordConvention, FrameRef, KV  # noqa: E402
from spatialdds_idl.spatial.core import Aabb3, PoseSE3  # noqa: E402

STAMP = Time(sec=1788300000, nanosec=250000000)
VENUE = FrameRef(uuid="b1afa008", fqn="map/ut-littlefield-fountain",
                 has_coord_convention=True, coord_convention=CoordConvention.ENU)
ZERO_POSE = PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0])
ZERO_EXTENT = Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0])

DUCK = Entity(
    entity_id="ent:duck:1",
    basis=Basis.AUTHORED,
    # Borrowed vocabulary, per the proposal -- the layer mints no types
    # of its own. Q851478 is Wikidata's rubber duck; Q483453 is fountain.
    type_uris=["http://www.wikidata.org/entity/Q851478"],
    layer=ModelLayer.SLOW,
    frame_ref=VENUE,
    has_pose=True,
    pose=PoseSE3(t=[11.708, -14.273, -1.423],
                 q=[0.0, 0.0, -0.7071067811865475, 0.7071067811865476]),
    has_extent=False,
    extent=ZERO_EXTENT,
    properties=[KV(key="demo.note", value="floats")],
    external_refs=[],
    content_refs=["catalog:89f2d953-076d-5c7d-9b74-1193f71685a6"],
    state=LifecycleState.ACTIVE,
    state_reason="",
    source_id="svc:model:demo/venue",
    stamp=STAMP,
)

FOUNTAIN = Entity(
    entity_id="ent:fountain",
    basis=Basis.OBSERVED,
    type_uris=["http://www.wikidata.org/entity/Q483453"],
    layer=ModelLayer.STATIC,
    frame_ref=VENUE,
    has_pose=True,
    pose=ZERO_POSE,
    has_extent=True,
    extent=Aabb3(min_xyz=[-14.0, -16.0, -2.0], max_xyz=[14.0, 16.0, 4.0]),
    properties=[],
    external_refs=[],
    content_refs=[],
    state=LifecycleState.ACTIVE,
    state_reason="",
    source_id="svc:model:demo/venue",
    stamp=STAMP,
)

CONTAINS = Relationship(
    rel_id="rel:fountain-contains-duck-1",
    kind="contains",
    from_entity_id="ent:fountain",
    to_entity_id="ent:duck:1",
    properties=[],
    source_id="svc:model:demo/venue",
    stamp=STAMP,
)

CASES = {"Entity(duck)": DUCK, "Entity(fountain)": FOUNTAIN,
         "Relationship(contains)": CONTAINS}


class JsonRoundTrip(unittest.TestCase):
    def test_every_case_round_trips(self):
        for name, value in CASES.items():
            with self.subTest(type=name):
                self.assertEqual(from_json(type(value), to_json(value)), value)

    def test_output_is_json_serialisable(self):
        for name, value in CASES.items():
            with self.subTest(type=name):
                json.dumps(to_json(value))

    def test_enums_are_identifiers(self):
        """House convention, same as the spec types: names, not integers."""
        data = to_json(DUCK)
        self.assertEqual(data["basis"], "AUTHORED")
        self.assertEqual(data["layer"], "SLOW")
        self.assertEqual(data["state"], "ACTIVE")


class CdrRoundTrip(unittest.TestCase):
    """The bus hop, not just the bridge hop."""

    def test_every_case_survives_serialisation(self):
        for name, value in CASES.items():
            with self.subTest(type=name):
                self.assertEqual(type(value).deserialize(value.serialize()), value)


class GuardFields(unittest.TestCase):
    """
    An absent pose and a zero pose are different claims and identical bytes.

    The flag is the only thing separating them, so it has to survive every hop
    on its own -- a consumer that reads `pose` without reading `has_pose` gets
    the origin of the venue frame and no indication that it was told nothing.
    """

    def test_absent_pose_survives_json(self):
        unplaced = Entity(**{**DUCK.__dict__, "has_pose": False, "pose": ZERO_POSE})
        back = from_json(Entity, to_json(unplaced))
        self.assertFalse(back.has_pose)
        self.assertEqual(back.pose, ZERO_POSE)

    def test_absent_pose_survives_cdr(self):
        unplaced = Entity(**{**DUCK.__dict__, "has_pose": False, "pose": ZERO_POSE})
        self.assertFalse(Entity.deserialize(unplaced.serialize()).has_pose)

    def test_extent_flag_is_independent_of_pose(self):
        """The fountain is placed and sized; the duck is placed and unsized."""
        self.assertTrue(FOUNTAIN.has_pose and FOUNTAIN.has_extent)
        self.assertTrue(DUCK.has_pose)
        self.assertFalse(DUCK.has_extent)
        for value in (FOUNTAIN, DUCK):
            back = from_json(Entity, to_json(value))
            self.assertEqual((back.has_pose, back.has_extent),
                             (value.has_pose, value.has_extent))

    def test_zero_extent_is_not_absent_extent(self):
        sized_zero = Entity(**{**FOUNTAIN.__dict__, "has_extent": True,
                               "extent": ZERO_EXTENT})
        unsized = Entity(**{**FOUNTAIN.__dict__, "has_extent": False,
                            "extent": ZERO_EXTENT})
        self.assertEqual(to_json(sized_zero)["extent"], to_json(unsized)["extent"])
        self.assertNotEqual(to_json(sized_zero)["has_extent"],
                            to_json(unsized)["has_extent"])


class AssetVersusInstance(unittest.TestCase):
    """
    The point of the layer: one asset, many instances.

    The catalogue row is the asset. Entities are the things in the world, and
    several of them may render from the same row -- which is precisely what a
    catalogue carrying its own pose cannot express.
    """

    def test_many_entities_may_share_one_content_ref(self):
        ducks = [Entity(**{**DUCK.__dict__, "entity_id": f"ent:duck:{i}",
                          "pose": PoseSE3(t=[float(i), 0.0, 0.0],
                                          q=[0.0, 0.0, 0.0, 1.0])})
                 for i in (1, 2, 3)]
        self.assertEqual(len({d.entity_id for d in ducks}), 3)
        self.assertEqual(len({tuple(d.pose.t) for d in ducks}), 3)
        self.assertEqual(len({tuple(d.content_refs) for d in ducks}), 1)


if __name__ == "__main__":
    unittest.main()
