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
from spatialdds_idl.oarc_model import LifecycleState  # noqa: E402

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
        self.assertEqual(len(snapshot["entities"]), 5)
        self.assertEqual(len(snapshot["relationships"]), 4)
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
        self.assertEqual(duck["layer"], "FAST")
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


class ExternalReferences(unittest.TestCase):
    """
    The fountain says what it is in somebody else's namespace.

    Both refs were checked on 2026-09-02 and corroborate each other: OSM way
    201514442 is tagged `wikidata=Q6652941`, and that item's coordinates land
    on this venue. The test pins the values so a later edit cannot quietly
    swap in an unverified id -- the failure mode being guarded against is a
    reference that resolves to the wrong thing, which is worse than none.
    """

    def test_the_fountain_carries_its_verified_identifiers(self):
        entities = {e["entity_id"]: e for e in _loaded().snapshot(STAMP)["entities"]}
        refs = {kv["key"]: kv["value"]
                for kv in entities["ent:fountain:littlefield"]["external_refs"]}
        self.assertEqual(refs, {"wikidata": "Q6652941", "osm": "way/201514442"})

    def test_the_ducks_claim_no_public_identity(self):
        """A rubber duck in a fountain is not a thing the world has an id for."""
        for entity in _loaded().snapshot(STAMP)["entities"]:
            if entity["entity_id"].startswith("ent:duck"):
                self.assertEqual(entity["external_refs"], [])


class BorrowedVocabulary(unittest.TestCase):
    """
    Everything published names a type the client can label.

    Reading the client's table from Python is unusual, but the alternative is
    two lists that drift: the seeder starts publishing a type, the client shows
    a bare URI where a name should be, and nothing fails. The same trick already
    guards the catalogue kinds in test_wellknown_endpoints.py.

    This is deliberately not "every label has a publisher" -- the table may
    carry types nothing here publishes yet. The asymmetry is the point.
    """

    def test_every_published_type_has_a_label(self):
        import re
        bridge_ts = (REPO / "web" / "src" / "spatialdds_bridge.ts").read_text()
        block = re.search(r"TYPE_LABELS[^=]*=\s*Object\.freeze\(\{(.*?)\}\)",
                          bridge_ts, re.S)
        self.assertIsNotNone(block, "could not find TYPE_LABELS in spatialdds_bridge.ts")
        known = set(re.findall(r"'([^']+)':", block.group(1)))
        for entity in seed_entities():
            for uri in entity.type_uris:
                with self.subTest(entity=entity.entity_id, uri=uri):
                    self.assertIn(uri, known)

    def test_published_types_are_borrowed_not_minted(self):
        for entity in seed_entities():
            for uri in entity.type_uris:
                with self.subTest(uri=uri):
                    self.assertTrue(uri.startswith("http"))
                    self.assertNotIn("oarc", uri)
                    self.assertNotIn("spatialdds", uri)


class Mirroring(unittest.TestCase):
    """It holds nothing the bus does not."""

    def test_dispose_evicts(self):
        cache = _loaded()
        removed = cache.dispose_entity(101)
        self.assertIsNotNone(removed)
        snapshot = cache.snapshot(STAMP)
        self.assertEqual(len(snapshot["entities"]), 4)
        self.assertNotIn(removed, [e["entity_id"] for e in snapshot["entities"]])
        self.assertEqual(cache.stats()["evicted"], 1)

    def test_disposing_an_unknown_handle_is_harmless(self):
        cache = _loaded()
        self.assertIsNone(cache.dispose_entity(9999))
        self.assertEqual(len(cache.snapshot(STAMP)["entities"]), 5)

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
        self.assertEqual(len(cache.snapshot(STAMP)["entities"]), 5)


class Retirement(unittest.TestCase):
    """
    A tombstone updates the record; the dispose that follows removes it.

    Both halves matter to the endpoint. A client polling `/v1/model` between
    the two should see the entity still there and saying why it is going --
    that window is short, but it is the only place the reason exists.
    """

    def test_a_retired_sample_updates_the_record_rather_than_removing_it(self):
        cache = _loaded()
        entities = seed_entities()
        duck = next(e for e in entities if e.entity_id == "ent:duck:east")
        duck.state = LifecycleState.RETIRED
        duck.state_reason = "taken in for the winter"
        cache.admit_entity(duck, instance_handle=104)

        snapshot = cache.snapshot(STAMP)
        by_id = {e["entity_id"]: e for e in snapshot["entities"]}
        self.assertEqual(len(snapshot["entities"]), 5, "still present, still counted")
        self.assertEqual(by_id["ent:duck:east"]["state"], "RETIRED")
        self.assertEqual(by_id["ent:duck:east"]["state_reason"],
                         "taken in for the winter")
        # Nothing else about it was touched on the way out.
        self.assertEqual(by_id["ent:duck:east"]["pose"]["t"], [16.5, -10.5, -1.423])

    def test_the_dispose_evicts_and_the_snapshot_forgets_it(self):
        cache = _loaded()
        removed = cache.dispose_entity(104)
        self.assertEqual(removed, "ent:duck:east")
        snapshot = cache.snapshot(STAMP)
        self.assertEqual(len(snapshot["entities"]), 4)
        self.assertNotIn("ent:duck:east",
                         [e["entity_id"] for e in snapshot["entities"]])

    def test_the_cascade_removes_the_edge_and_leaves_the_others(self):
        cache = _loaded()
        # seed_relationships is ordered as the ducks are: catalog-pose, west, east.
        self.assertEqual(cache.dispose_relationship(203), "rel:contains:pond-duck-east")
        rel_ids = [r["rel_id"] for r in cache.snapshot(STAMP)["relationships"]]
        self.assertEqual(rel_ids, ["rel:contains:fountain-pond-littlefield",
                                   "rel:contains:pond-duck-catalog-pose",
                                   "rel:contains:pond-duck-west"])

    def test_a_dispose_reports_what_it_removed_so_clients_can_be_told(self):
        """
        The return value is load-bearing now.

        A dispose carries no sample, so the bridge has only the instance
        handle -- meaningless to a browser. The cache is the one place that
        can turn it back into an entity_id, and if it returns None the client
        is never told and keeps drawing something that no longer exists.
        """
        cache = _loaded()
        self.assertEqual(cache.dispose_entity(100), "ent:fountain:littlefield")
        self.assertIsNone(cache.dispose_entity(100), "a second dispose has nothing to report")


if __name__ == "__main__":
    unittest.main()
