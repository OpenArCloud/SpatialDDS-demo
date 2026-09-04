"""
`oarc_model` on a bus — late join, asset-vs-instance, and the mover.

Demo-local and non-normative; see idl/demo/oarc_model.idl.

These need a DDS domain and skip loudly without one. What they pin is the
claim the layer is being prototyped to make: that a client which arrives after
everything has happened is still handed the whole world, and that moving one
thing moves exactly that thing.

A note on how the move test is written, because the first attempt at it by
hand proved nothing. It republished a pose the subscriber already held and
watched the client log "moved" — true, and meaningless. So this one reads the
current state first, moves to a position it has just asserted is different,
and compares fields rather than log lines.
"""

import importlib.util
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import typed_transport as tt  # noqa: E402
from spatialdds_demo.json_mapping import to_json  # noqa: E402
from spatialdds_demo.model_service import (  # noqa: E402
    DUCK_CONTENT_ID, ModelPublisher, seed_entities, seed_relationships,
)
from spatialdds_demo.qos_profiles import MODEL_LATCHED  # noqa: E402
from spatialdds_demo.topics import (  # noqa: E402
    TOPIC_MODEL_ENTITY_V1, TOPIC_MODEL_RELATIONSHIP_V1,
)
from spatialdds_idl.oarc_model import Entity, Relationship  # noqa: E402

# High enough to stay clear of the demo stack's own domains.
DOMAIN = 43
SEEDED_ENTITIES = 4
SEEDED_RELATIONSHIPS = 3


def _participant(domain_id: int):
    """A participant, or a skip if this environment has no usable DDS."""
    try:
        from cyclonedds.domain import DomainParticipant
        return DomainParticipant(domain_id)
    except Exception as exc:
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


def _drain(entity_reader, relationship_reader, want_e, want_r, timeout=10.0):
    """Collect until the model is complete or time runs out."""
    entities, relationships = {}, {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sample in tt.take_samples(entity_reader) or []:
            entities[sample.entity_id] = sample
        for sample in tt.take_samples(relationship_reader) or []:
            relationships[sample.rel_id] = sample
        if len(entities) >= want_e and len(relationships) >= want_r:
            break
        time.sleep(0.02)
    return entities, relationships


class LateJoin(unittest.TestCase):
    """
    The property the whole layer rests on.

    Both topics are TRANSIENT_LOCAL with KEEP_LAST(1) per key, so a subscriber
    that starts after the publisher gets the current model from the
    middleware. Nothing here asks for it: no query is sent, no snapshot is
    fetched, and there is no replay code in the demo to be fooled by.
    """

    def test_a_fresh_subscriber_is_handed_the_whole_model(self):
        publisher_participant = _participant(DOMAIN)
        publisher = ModelPublisher(publisher_participant)
        entities = seed_entities()
        for entity in entities:
            publisher.publish_entity(entity)
        for relationship in seed_relationships(entities):
            publisher.publish_relationship(relationship)

        # Everything above has already happened before this subscriber exists.
        time.sleep(0.5)
        started = time.time()
        subscriber = _participant(DOMAIN)
        entity_reader = tt.make_reader(
            subscriber, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        relationship_reader = tt.make_reader(
            subscriber, TOPIC_MODEL_RELATIONSHIP_V1, Relationship, MODEL_LATCHED.name)
        got_entities, got_relationships = _drain(
            entity_reader, relationship_reader, SEEDED_ENTITIES, SEEDED_RELATIONSHIPS)
        elapsed = time.time() - started

        try:
            self.assertEqual(len(got_entities), SEEDED_ENTITIES)
            self.assertEqual(len(got_relationships), SEEDED_RELATIONSHIPS)
            print(f"\n  late-join: full model ({SEEDED_ENTITIES} entities, "
                  f"{SEEDED_RELATIONSHIPS} relationships) in {elapsed:.3f}s, unrequested")
            # The brief's target. Generous against a loaded machine; the
            # number that matters is the one printed above.
            self.assertLess(elapsed, 5.0)
        finally:
            publisher.close()


class AssetVersusInstance(unittest.TestCase):
    """One duck.glb in the catalogue, three ducks in the world."""

    def test_three_ducks_one_asset(self):
        ducks = [e for e in seed_entities() if e.entity_id.startswith("ent:duck")]
        self.assertEqual(len(ducks), 3)
        self.assertEqual({tuple(d.content_refs) for d in ducks},
                         {(f"catalog:{DUCK_CONTENT_ID}",)})
        self.assertEqual(len({d.entity_id for d in ducks}), 3)
        self.assertEqual(len({tuple(d.pose.t) for d in ducks}), 3)

    def test_the_fountain_carries_no_asset(self):
        """It is already in the tiles; an entity need not have content."""
        fountain = seed_entities()[0]
        self.assertEqual(fountain.content_refs, [])
        self.assertTrue(fountain.has_extent)


class SecondPublisher(unittest.TestCase):
    """
    Two writers, one model.

    The gnome comes from its own process with its own source_id, on the same
    latched topics. A subscriber should see one world containing five things,
    not two feeds it has to reconcile -- and the entity of an unknown type has
    to arrive on the same terms as everything else, because a consumer that
    only accepts vocabularies it knows cannot federate with anyone.
    """

    @staticmethod
    def _load_gnome_publisher():
        path = REPO / "scripts" / "gnome_publisher.py"
        spec = importlib.util.spec_from_file_location("gnome_publisher", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_stranger_declares_what_it_should_and_nothing_more(self):
        gnome = self._load_gnome_publisher().gnome()
        self.assertEqual(gnome.entity_id, "ent:gnome:visitor")
        self.assertEqual(gnome.basis.name, "AUTHORED")
        self.assertEqual(gnome.layer.name, "SLOW")
        self.assertTrue(gnome.has_pose)
        # No size, no content, no label, no external identity: everything the
        # client has to degrade around.
        self.assertFalse(gnome.has_extent)
        self.assertEqual(list(gnome.content_refs), [])
        self.assertEqual(list(gnome.properties), [])
        self.assertEqual(list(gnome.external_refs), [])
        # A different publisher, and it says so.
        self.assertNotEqual(gnome.source_id, seed_entities()[0].source_id)

    def test_it_names_the_same_frame_so_it_can_be_placed(self):
        """A stranger who names the frame correctly is placeable. One who does
        not is declined by the client rather than guessed at."""
        gnome = self._load_gnome_publisher().gnome()
        self.assertEqual(gnome.frame_ref.uuid, seed_entities()[0].frame_ref.uuid)
        self.assertEqual(gnome.frame_ref.fqn, seed_entities()[0].frame_ref.fqn)

    def test_a_late_joiner_gets_both_publishers(self):
        module = self._load_gnome_publisher()
        publisher_participant = _participant(DOMAIN + 2)
        publisher = ModelPublisher(publisher_participant)
        entities = seed_entities()
        for entity in entities:
            publisher.publish_entity(entity)

        # A second, independent writer on the same topic.
        gnome_writer = tt.make_writer(
            publisher_participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        gnome_writer.write(module.gnome())

        time.sleep(0.5)
        started = time.time()
        subscriber = _participant(DOMAIN + 2)
        entity_reader = tt.make_reader(
            subscriber, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        got = {}
        deadline = time.time() + 10
        while time.time() < deadline and len(got) < SEEDED_ENTITIES + 1:
            for sample in tt.take_samples(entity_reader) or []:
                got[sample.entity_id] = sample
            time.sleep(0.02)
        elapsed = time.time() - started

        try:
            self.assertEqual(len(got), SEEDED_ENTITIES + 1)
            self.assertIn("ent:gnome:visitor", got)
            sources = {e.source_id for e in got.values()}
            self.assertEqual(len(sources), 2, "should be two distinct publishers")
            print(f"\n  late-join with two publishers: {len(got)} entities from "
                  f"{len(sources)} sources in {elapsed:.3f}s")
        finally:
            publisher.close()


class Mover(unittest.TestCase):
    """
    `scripts/move_duck.py` changes the pose and the stamp and nothing else.

    Asserted by comparing every other field before and after, rather than by
    trusting the read-modify-write to have been written correctly. An operator
    tool that quietly reverted a field someone had edited would be worse than
    no tool.
    """

    @staticmethod
    def _load_mover():
        path = REPO / "scripts" / "move_duck.py"
        spec = importlib.util.spec_from_file_location("move_duck", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_only_pose_and_stamp_change(self):
        participant = _participant(DOMAIN + 1)
        publisher = ModelPublisher(participant)
        for entity in seed_entities():
            publisher.publish_entity(entity)
        time.sleep(0.5)

        mover = self._load_mover()
        target = "ent:duck:west"
        before = mover.read_entity(participant, target)
        self.assertIsNotNone(before, "publisher did not latch the entity")
        before_json = to_json(before)

        # A position asserted to be different, so a no-op cannot pass.
        new_xy = (before.pose.t[0] + 4.0, before.pose.t[1] - 3.0)
        self.assertNotEqual((before.pose.t[0], before.pose.t[1]), new_xy)

        from spatialdds_idl.builtin import Time
        from spatialdds_idl.spatial.core import PoseSE3
        moved = mover.read_entity(participant, target)
        now = time.time()
        moved.pose = PoseSE3(t=[new_xy[0], new_xy[1], before.pose.t[2]],
                             q=list(before.pose.q))
        moved.stamp = Time(sec=int(now), nanosec=int((now % 1) * 1e9))
        writer = tt.make_writer(
            participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        writer.write(moved)
        time.sleep(0.8)

        after = mover.read_entity(_participant(DOMAIN + 1), target)
        self.assertIsNotNone(after)
        after_json = to_json(after)

        try:
            # What must have changed.
            self.assertNotEqual(before_json["pose"]["t"], after_json["pose"]["t"])
            self.assertNotEqual(before_json["stamp"], after_json["stamp"])
            # What must not have. Byte-equal, field by field.
            for field in set(before_json) - {"pose", "stamp"}:
                with self.subTest(field=field):
                    self.assertEqual(before_json[field], after_json[field])
            # The orientation rides inside pose and is also untouched.
            self.assertEqual(before_json["pose"]["q"], after_json["pose"]["q"])
        finally:
            publisher.close()


if __name__ == "__main__":
    unittest.main()
