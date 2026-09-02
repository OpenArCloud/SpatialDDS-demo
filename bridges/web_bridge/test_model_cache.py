"""
The bridge's model mirror, and the shape `GET /v1/model` promises.

No DDS: the cache is fed directly, because what is under test is the contract
the endpoint documents in the bridge README — field names, presence flags,
enums as identifiers — not the transport that fills it.

The cache is a mirror, not a store. These tests are written to keep it that
way: what goes in comes out, dispose removes, and nothing is invented.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (str(REPO), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from model_cache import ModelCache  # noqa: E402
from spatialdds_demo.model_service import seed_entities, seed_relationships  # noqa: E402

STAMP = {"sec": 1788400000, "nanosec": 0}


def _loaded() -> ModelCache:
    cache = ModelCache()
    entities = seed_entities()
    for i, entity in enumerate(entities):
        cache.admit_entity(entity, instance_handle=100 + i)
    for i, relationship in enumerate(seed_relationships(entities)):
        cache.admit_relationship(relationship, instance_handle=200 + i)
    return cache


class SnapshotShape(unittest.TestCase):
    def test_top_level_shape(self):
        snapshot = _loaded().snapshot(STAMP)
        self.assertEqual(set(snapshot), {"entities", "relationships", "stamp"})
        self.assertEqual(len(snapshot["entities"]), 4)
        self.assertEqual(len(snapshot["relationships"]), 3)
        self.assertEqual(snapshot["stamp"], STAMP)

    def test_entity_fields_match_the_documented_shape(self):
        entity = _loaded().snapshot(STAMP)["entities"][0]
        for field in ("entity_id", "basis", "type_uris", "layer", "frame_ref",
                      "has_pose", "pose", "has_extent", "extent", "properties",
                      "external_refs", "content_refs", "state", "state_reason",
                      "source_id", "stamp"):
            with self.subTest(field=field):
                self.assertIn(field, entity)

    def test_relationship_fields_match_the_documented_shape(self):
        relationship = _loaded().snapshot(STAMP)["relationships"][0]
        for field in ("rel_id", "kind", "from_entity_id", "to_entity_id",
                      "properties", "source_id", "stamp"):
            with self.subTest(field=field):
                self.assertIn(field, relationship)

    def test_enums_are_identifiers_and_flags_guard_their_member(self):
        """The JSON conventions the README tells a client to rely on."""
        by_id = {e["entity_id"]: e for e in _loaded().snapshot(STAMP)["entities"]}
        duck = by_id["ent:duck:west"]
        self.assertEqual(duck["basis"], "AUTHORED")
        self.assertEqual(duck["layer"], "SLOW")
        self.assertEqual(duck["state"], "ACTIVE")
        # A guarded member is present and zeroed rather than absent, which is
        # exactly why a client has to read the flag.
        self.assertFalse(duck["has_extent"])
        self.assertIn("extent", duck)
        self.assertEqual(duck["extent"]["min_xyz"], [0.0, 0.0, 0.0])

    def test_output_is_ordered_so_two_calls_are_comparable(self):
        cache = _loaded()
        first = cache.snapshot(STAMP)
        second = cache.snapshot(STAMP)
        self.assertEqual(first, second)
        self.assertEqual([e["entity_id"] for e in first["entities"]],
                         sorted(e["entity_id"] for e in first["entities"]))


class AssetVersusInstance(unittest.TestCase):
    def test_the_snapshot_shows_one_asset_and_three_instances(self):
        entities = _loaded().snapshot(STAMP)["entities"]
        ducks = [e for e in entities if e["entity_id"].startswith("ent:duck")]
        self.assertEqual(len(ducks), 3)
        self.assertEqual(len({tuple(d["content_refs"]) for d in ducks}), 1)
        self.assertEqual(len({d["entity_id"] for d in ducks}), 3)
        self.assertEqual(len({tuple(d["pose"]["t"]) for d in ducks}), 3)


class Mirroring(unittest.TestCase):
    """It holds nothing the bus does not."""

    def test_dispose_evicts(self):
        cache = _loaded()
        removed = cache.dispose_entity(101)
        self.assertIsNotNone(removed)
        snapshot = cache.snapshot(STAMP)
        self.assertEqual(len(snapshot["entities"]), 3)
        self.assertNotIn(removed, [e["entity_id"] for e in snapshot["entities"]])
        self.assertEqual(cache.stats()["evicted"], 1)

    def test_disposing_an_unknown_handle_is_harmless(self):
        cache = _loaded()
        self.assertIsNone(cache.dispose_entity(9999))
        self.assertEqual(len(cache.snapshot(STAMP)["entities"]), 4)

    def test_an_empty_cache_still_answers_with_the_documented_shape(self):
        """No publisher running is not an error; it is an empty world."""
        snapshot = ModelCache().snapshot(STAMP)
        self.assertEqual(snapshot, {"entities": [], "relationships": [],
                                    "stamp": STAMP})

    def test_latest_wins_per_key(self):
        cache = _loaded()
        entity = seed_entities()[1]
        entity.pose.t = [1.0, 2.0, 3.0]
        cache.admit_entity(entity, instance_handle=101)
        by_id = {e["entity_id"]: e for e in cache.snapshot(STAMP)["entities"]}
        self.assertEqual(by_id[entity.entity_id]["pose"]["t"], [1.0, 2.0, 3.0])
        self.assertEqual(len(cache.snapshot(STAMP)["entities"]), 4)


if __name__ == "__main__":
    unittest.main()
