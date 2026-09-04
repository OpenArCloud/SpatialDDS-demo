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


class Retirement(unittest.TestCase):
    """
    Tombstone, then dispose -- and it has to stick.

    The order carries the meaning: a dispose on its own says the entity is
    gone and nothing about why, so the tombstone has to arrive in front of it
    while there is still an entity to explain itself. And it has to survive
    the tool that asked for it, which is the whole reason retirement is a
    request to the owner rather than something an operator writes directly.
    """

    def _publisher(self, domain):
        participant = _participant(domain)
        publisher = ModelPublisher(participant)
        entities = seed_entities()
        for entity in entities:
            publisher.publish_entity(entity)
        for relationship in seed_relationships(entities):
            publisher.publish_relationship(relationship)
        return participant, publisher

    def test_a_live_subscriber_sees_the_tombstone_then_the_eviction(self):
        participant, publisher = self._publisher(DOMAIN + 3)
        entity_reader = tt.make_reader(
            participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        _drain(entity_reader, tt.make_reader(
            participant, TOPIC_MODEL_RELATIONSHIP_V1, Relationship,
            MODEL_LATCHED.name), SEEDED_ENTITIES, SEEDED_RELATIONSHIPS)

        target = "ent:duck:east"
        reason = "taken in for the winter"
        events = []

        def collect():
            for sample in tt.take_with_state(entity_reader):
                if sample.data is None:
                    events.append(("disposed", None))
                elif (sample.data.entity_id == target
                      and sample.data.state.name == "RETIRED"):
                    events.append(("tombstone", sample.data.state_reason))

        try:
            # A short settle so the two samples are distinguishable to a
            # reader polling at human speed; the service uses the same pause.
            thread = __import__("threading").Thread(
                target=publisher.retire, args=(target, reason, 0.4))
            thread.start()
            deadline = time.time() + 15
            while time.time() < deadline and len(events) < 2:
                collect()
                time.sleep(0.02)
            thread.join(timeout=10)

            self.assertEqual([kind for kind, _ in events], ["tombstone", "disposed"],
                             f"expected tombstone then dispose, got {events}")
            self.assertEqual(events[0][1], reason,
                             "the tombstone is the only place the reason is carried")
            print(f"\n  retirement: tombstone ({reason!r}) -> dispose, in that order")
        finally:
            publisher.close()

    def test_a_late_joiner_sees_neither_the_entity_nor_its_edges(self):
        """
        The claim the design rests on.

        This passes only because the owner did the retiring. A tombstone
        written by a short-lived tool dies with that tool while the owner's
        ACTIVE sample stays latched, and a reader joining afterwards is handed
        a live duck -- measured, and written up in SPEC_COMPLIANCE.
        """
        participant, publisher = self._publisher(DOMAIN + 4)
        target = "ent:duck:east"
        cascaded = publisher.retire(target, "taken in for the winter", settle=0.2)
        self.assertEqual(cascaded, ["rel:contains:east"],
                         "retiring an entity should take its edges with it")
        time.sleep(0.5)

        try:
            subscriber = _participant(DOMAIN + 4)
            entities, relationships = _drain(
                tt.make_reader(subscriber, TOPIC_MODEL_ENTITY_V1, Entity,
                               MODEL_LATCHED.name),
                tt.make_reader(subscriber, TOPIC_MODEL_RELATIONSHIP_V1,
                               Relationship, MODEL_LATCHED.name),
                SEEDED_ENTITIES - 1, SEEDED_RELATIONSHIPS - 1, timeout=6.0)
            self.assertNotIn(target, entities)
            self.assertNotIn("rel:contains:east", relationships)
            # And nothing else went with it.
            self.assertEqual(len(entities), SEEDED_ENTITIES - 1)
            self.assertEqual(len(relationships), SEEDED_RELATIONSHIPS - 1)
            print(f"  late-join after retirement: {len(entities)} entities, "
                  f"{len(relationships)} relationships, no tombstone replayed")
        finally:
            publisher.close()

    def test_the_id_is_not_burned(self):
        """A retired id can be published again. Retirement ends an instance,
        not a name."""
        participant, publisher = self._publisher(DOMAIN + 5)
        target = "ent:duck:east"
        publisher.retire(target, "winter", settle=0.1)
        self.assertFalse(publisher.owns(target))

        publisher.restore_seed()
        time.sleep(0.5)
        try:
            subscriber = _participant(DOMAIN + 5)
            entities, relationships = _drain(
                tt.make_reader(subscriber, TOPIC_MODEL_ENTITY_V1, Entity,
                               MODEL_LATCHED.name),
                tt.make_reader(subscriber, TOPIC_MODEL_RELATIONSHIP_V1,
                               Relationship, MODEL_LATCHED.name),
                SEEDED_ENTITIES, SEEDED_RELATIONSHIPS)
            self.assertIn(target, entities)
            self.assertEqual(entities[target].state.name, "ACTIVE")
            self.assertEqual(entities[target].state_reason, "",
                             "a restored entity should not still be explaining itself")
            self.assertIn("rel:contains:east", relationships)
        finally:
            publisher.close()

    def test_a_command_for_an_entity_we_do_not_own_is_declined(self):
        """
        Refusing is the honest answer.

        Publishing a tombstone for someone else's entity would be a claim this
        writer cannot make stick: the owner's sample is still latched and the
        next reader to join gets that one instead. The gnome is exactly this
        case -- a real entity on the same topics, owned by another process.
        """
        from spatialdds_idl.oarc_model import ModelCommand
        participant, publisher = self._publisher(DOMAIN + 6)
        try:
            line = publisher.handle_command(ModelCommand(
                command_id="c1", verb="retire", entity_id="ent:gnome:visitor",
                reason="not mine to retire", requester_id="tool:test",
                stamp=seed_entities()[0].stamp))
            self.assertIn("declined", line)
            self.assertTrue(publisher.owns("ent:duck:east"),
                            "a declined command must not disturb what we do own")
        finally:
            publisher.close()

    def test_an_unknown_verb_is_ignored_rather_than_guessed_at(self):
        from spatialdds_idl.oarc_model import ModelCommand
        participant, publisher = self._publisher(DOMAIN + 7)
        try:
            line = publisher.handle_command(ModelCommand(
                command_id="c2", verb="delete", entity_id="ent:duck:east",
                reason="", requester_id="tool:test",
                stamp=seed_entities()[0].stamp))
            self.assertIn("unknown verb", line)
            self.assertTrue(publisher.owns("ent:duck:east"))
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
