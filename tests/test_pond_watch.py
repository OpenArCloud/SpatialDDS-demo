"""
Two accounts of one pond, and nothing in the model crowning either.

`svc:fusion:demo/pondwatch` publishes its own entity for the same water the
venue declares. The model carries both. What it does *not* carry is any
statement that they are the same thing -- an edge that said so honestly would
have to say who asserted it and on what evidence, and `Relationship` has
neither a basis nor a state. Inventing one would assert a fact the type
cannot qualify.

So the disagreement is surfaced, attributed, and left standing. A consumer
picks: the mover takes `--bounds declared|derived`, which is the point.
"""

import random
import re
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo.duck_mover import BOUNDS_ENTITIES  # noqa: E402
from spatialdds_demo.model_service import (  # noqa: E402
    POND_MAX, POND_MIN, TYPE_BASIN, seed_entities,
)
from spatialdds_demo.pond_watch import (  # noqa: E402
    ENTITY_ID, MARGIN_M, SOURCE_ID, WOBBLE_M, observation,
)


class TwoAccounts(unittest.TestCase):
    def test_it_is_a_different_entity_from_a_different_source(self):
        observed = observation(random.Random(1))
        declared = next(e for e in seed_entities()
                        if e.entity_id == "ent:pond:littlefield")
        self.assertNotEqual(observed.entity_id, declared.entity_id)
        self.assertNotEqual(observed.source_id, declared.source_id)
        self.assertEqual(observed.source_id, SOURCE_ID)

    def test_they_agree_about_what_it_is_and_disagree_about_where(self):
        """
        The disagreement has to be about bounds, not about kind.

        Two services describing different things would be uninteresting; the
        demonstration needs them to be talking about the same water.
        """
        observed = observation(random.Random(2))
        declared = next(e for e in seed_entities()
                        if e.entity_id == "ent:pond:littlefield")
        self.assertEqual(observed.type_uris, declared.type_uris)
        self.assertEqual(observed.type_uris, [TYPE_BASIN])
        self.assertEqual(observed.frame_ref.uuid, declared.frame_ref.uuid)
        self.assertNotEqual(list(observed.extent.min_xyz),
                            list(declared.extent.min_xyz))

    def test_the_bases_are_different_and_that_is_the_whole_point(self):
        observed = observation(random.Random(3))
        declared = next(e for e in seed_entities()
                        if e.entity_id == "ent:pond:littlefield")
        self.assertEqual(observed.basis.name, "DERIVED")
        self.assertEqual(declared.basis.name, "DECLARED")

    def test_the_observed_pond_is_the_larger_one(self):
        """
        Legible rather than arbitrary.

        The venue declared bounds deliberately inside the waterline so that
        anything trusting them stays wet. A service measuring the water finds
        more of it. A viewer can see which account is cautious.
        """
        for seed in range(8):
            observed = observation(random.Random(seed)).extent
            with self.subTest(seed=seed):
                self.assertLess(observed.min_xyz[0], POND_MIN[0])
                self.assertLess(observed.min_xyz[1], POND_MIN[1])
                self.assertGreater(observed.max_xyz[0], POND_MAX[0])
                self.assertGreater(observed.max_xyz[1], POND_MAX[1])

    def test_it_wobbles_because_it_is_observing_rather_than_remembering(self):
        rng = random.Random(4)
        widths = {round(observation(rng).extent.max_xyz[0]
                        - observation(rng).extent.min_xyz[0], 4)
                  for _ in range(6)}
        self.assertGreater(len(widths), 1,
                           "identical bounds forever is memory, not observation")

    def test_the_wobble_stays_within_its_stated_bound(self):
        for seed in range(20):
            extent = observation(random.Random(seed)).extent
            with self.subTest(seed=seed):
                self.assertLessEqual(
                    abs(extent.min_xyz[0] - (POND_MIN[0] - MARGIN_M)),
                    WOBBLE_M + 1e-9)

    def test_the_height_is_the_venue_s_because_this_service_measures_edges(self):
        extent = observation(random.Random(5)).extent
        self.assertEqual(extent.min_xyz[2], POND_MIN[2])
        self.assertEqual(extent.max_xyz[2], POND_MAX[2])


class NothingJoinsThem(unittest.TestCase):
    def test_no_relationship_ties_the_two_ponds(self):
        """
        Deliberate, and the reason is in the type.

        Relationship carries source_id, so an edge could say who claimed
        identity. It has no basis, so it could not say whether identity was
        asserted, computed or assumed -- which for an identity claim is the
        half that decides what a consumer should do with it. Recorded for R10
        rather than improvised.
        """
        source = (REPO / "spatialdds_demo" / "pond_watch.py").read_text()
        # The claim is about what it publishes, not about what it mentions:
        # the module explains at length *why* there is no edge, and a guard
        # that banned the word would have banned the explanation.
        writers = re.findall(r"make_writer\(\s*\w+,\s*(\w+)", source)
        self.assertEqual(writers, ["TOPIC_MODEL_ENTITY_V1"],
                         f"pondwatch should publish one entity and no edges; "
                         f"found writers on {writers}")
        self.assertIn("R10", source, "and should say where the question went")


class ConsumerPolicy(unittest.TestCase):
    def test_the_mover_can_be_pointed_at_either_account(self):
        self.assertEqual(sorted(BOUNDS_ENTITIES), ["declared", "derived"])
        self.assertEqual(BOUNDS_ENTITIES["declared"], "ent:pond:littlefield")
        self.assertEqual(BOUNDS_ENTITIES["derived"], ENTITY_ID)

    def test_choosing_is_a_flag_rather_than_a_constant(self):
        """If which pond to trust were baked in, the demo would be asserting
        an answer the model deliberately does not give."""
        source = (REPO / "spatialdds_demo" / "duck_mover.py").read_text()
        self.assertRegex(source, r'--bounds"?,\s*choices=')


class WhomYouTrustChangesTheWorld(unittest.TestCase):
    """
    The same mover, the same ducks, two answers -- because the consumer
    picked a different account of the water.

    Asserted where the accounts actually disagree, rather than by watching a
    random walk and hoping it wanders somewhere telling. A duck placed in the
    margin between the two ponds is inside one and outside the other, so a
    single mover tick settles the question: trusting the venue pulls it in,
    trusting the observation leaves it alone.
    """

    DOMAIN = 61

    def _participant(self, domain):
        try:
            from cyclonedds.domain import DomainParticipant
            return DomainParticipant(domain)
        except Exception as exc:
            raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")

    def _stack(self, domain):
        from spatialdds_demo import typed_transport as tt
        from spatialdds_demo.model_service import ModelPublisher
        from spatialdds_demo.qos_profiles import MODEL_COMMAND, MODEL_LATCHED
        from spatialdds_demo.topics import (
            TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1,
        )
        from spatialdds_idl.oarc_model import Entity, ModelCommand

        participant = self._participant(domain)
        publisher = ModelPublisher(participant)
        for entity in seed_entities():
            publisher.publish_entity(entity)
        # The second opinion, published by its own writer as it is in life.
        # Held on the instance, not in a local. A writer that goes out of
        # scope takes its TRANSIENT_LOCAL history with it, so the observed
        # pond simply stopped existing when this helper returned -- and the
        # mover, finding no bounds, moved nothing and asserted nothing.
        #
        # That is the repo's own headline finding ("Two writers, one
        # instance") reproduced by accident inside a test written by the
        # person who wrote it up. The rule is not "remember to keep a
        # reference"; it is that latched state lives exactly as long as its
        # writer, in tests as much as in services.
        self._watcher = tt.make_writer(participant, TOPIC_MODEL_ENTITY_V1,
                                       Entity, MODEL_LATCHED.name)
        observed = observation(random.Random(0))
        self._watcher.write(observed)
        commands = tt.make_reader(participant, TOPIC_MODEL_COMMAND_V1,
                                  ModelCommand, MODEL_COMMAND.name)
        return participant, publisher, commands, observed

    @staticmethod
    def _place(publisher, start):
        """
        Put the duck at a known position *on the bus*, not just in the
        service's head.

        A duck is FAST, so `move` publishes a pose and does not rewrite the
        latched record until the fifth move or the idle flush. The mover
        reads the latch, so without the flush it starts from wherever the
        record last said -- which in this test was the previous run's ending
        position, and made the second half assert nothing. The fast tier is
        correct; the test was reading the wrong lane, the same mistake
        move_duck.py made in P3.3.
        """
        from spatialdds_demo.model_service import IDLE_FLUSH_S
        from spatialdds_idl.spatial.core import PoseSE3

        publisher.move("ent:duck:west", PoseSE3(t=list(start), q=[0, 0, 0, 1]))
        time.sleep(IDLE_FLUSH_S + 0.1)
        publisher.flush_idle()
        time.sleep(0.4)

    def _run_mover(self, domain, bounds_entity, publisher, commands):
        from spatialdds_demo import typed_transport as tt
        from spatialdds_demo.duck_mover import DuckMover

        mover = DuckMover(self._participant(domain), bounds_entity,
                          rng=random.Random(5))
        time.sleep(0.6)
        applied = 0
        for _ in range(30):
            mover.tick()
            for command in tt.take_samples(commands) or []:
                publisher.handle_command(command)
                if command.subject_id == "ent:duck:west":
                    applied += 1
            time.sleep(0.02)
        self.assertGreater(applied, 0,
                           f"the mover following {bounds_entity} never moved the duck")
        return publisher._published["ent:duck:west"].pose.t[:2]

    def test_trusting_the_venue_pulls_a_duck_in_that_the_observer_would_leave(self):
        from spatialdds_demo.duck_mover import BOUNDS_ENTITIES, INSET_M
        from spatialdds_idl.spatial.core import PoseSE3

        participant, publisher, commands, observed = self._stack(self.DOMAIN)
        try:
            # Where the two accounts actually disagree, once the mover's
            # inset is accounted for. The venue will not put a duck west of
            # (declared edge + inset); the observer will, down to (its own
            # edge + inset). Everything in that band is legal under one
            # account and not the other, and nothing outside it distinguishes
            # them -- an earlier version of this test started the duck below
            # both limits, where the two movers agree that it must come in,
            # and proved nothing while looking like it proved something.
            venue_limit = POND_MIN[0] + INSET_M
            observer_limit = observed.extent.min_xyz[0] + INSET_M
            self.assertLess(observer_limit, venue_limit,
                            "the accounts must differ or there is nothing to choose")

            start = [observer_limit - 1.5, -14.0, -1.423]      # outside both
            self._place(publisher, start)
            trusting_venue = self._run_mover(
                self.DOMAIN, BOUNDS_ENTITIES["declared"], publisher, commands)
            self.assertGreaterEqual(
                trusting_venue[0], venue_limit - 1e-6,
                "trusting the venue keeps the duck inside the declared water")

            self._place(publisher, start)
            trusting_observer = self._run_mover(
                self.DOMAIN, BOUNDS_ENTITIES["derived"], publisher, commands)
            self.assertGreaterEqual(
                trusting_observer[0], observer_limit - 1e-6,
                "trusting the observation keeps it inside the observed water")
            self.assertLess(
                trusting_observer[0], venue_limit,
                "and lets it stand where the venue would not have allowed it")

            print(f"\n  same duck, same mover, different account of the water:")
            print(f"    trusting the venue       -> x={trusting_venue[0]:.2f} "
                  f"(may not go west of {venue_limit:.2f})")
            print(f"    trusting the observation -> x={trusting_observer[0]:.2f} "
                  f"(may go to {observer_limit:.2f})")
        finally:
            publisher.close()


if __name__ == "__main__":
    unittest.main()
