"""
`spatialdds_demo.lanes` says what the SDK's registry cannot, and nothing else.

The replacement is proven here before anything it replaces is removed
(docs/sdk-adoption-census.md):

* **The three deployment profiles.** The latched and fast lanes keep the
  columns the demo has always run. The command lane takes the depth §3.3.2
  spells for `model_command`, KEEP_LAST(1), because the lane is keyed per
  command. Each profile is accepted against the owm row it is used on.
* **This repository's own types**, registered with the SDK on import. Each
  round-trips through N.1, keys on its own `@key`, and is named by
  `class_for`.
* **The lanes outside the scene.** Each is a name §3.3.1 accepts, which is
  what `topic=` holds them to.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import spatialdds as sd  # noqa: E402
from spatialdds.dds import codec, registry  # noqa: E402

from spatialdds_demo import lanes  # noqa: E402


def columns(p):
    return (p.reliability, p.durability, p.history, p.depth, p.deadline_ms)


class Profiles(unittest.TestCase):

    def test_latched_and_fast_keep_the_columns_the_demo_has_always_run(self):
        self.assertEqual(columns(lanes.MODEL_LATCHED),
                         ("RELIABLE", "TRANSIENT_LOCAL", "KEEP_LAST", 1, None))
        self.assertEqual(columns(lanes.MODEL_FAST),
                         ("BEST_EFFORT", "VOLATILE", "KEEP_LAST", 1, None))

    def test_the_command_lane_takes_the_depth_its_row_spells(self):
        row = registry.registrations()["model_command"].spelled_qos
        self.assertEqual(row["history"], {"kind": "KEEP_LAST", "depth": 1})
        self.assertEqual(columns(lanes.MODEL_COMMAND),
                         ("RELIABLE", "VOLATILE", "KEEP_LAST", 1, None))

    def test_each_profile_is_accepted_on_the_row_it_is_used_on(self):
        for type_name, p in (("model_entity", lanes.MODEL_LATCHED),
                             ("model_pose", lanes.MODEL_FAST),
                             ("model_command", lanes.MODEL_COMMAND)):
            with self.subTest(type_name):
                reg = registry.registrations()[type_name]
                self.assertIs(registry.resolve_profile(reg, p), p)

    def test_a_profile_name_from_an_announce_resolves(self):
        self.assertIs(lanes.profile("MODEL_FAST"), lanes.MODEL_FAST)
        self.assertEqual(lanes.profile("MAP_META").name, "MAP_META")
        with self.assertRaises(registry.RegistryError):
            lanes.profile("NOT_A_PROFILE")


class Extensions(unittest.TestCase):

    def test_each_is_registered_with_the_sdk(self):
        self.assertEqual(sorted(codec.extensions()), sorted(lanes.EXTENSIONS))

    def test_each_round_trips_keys_and_is_named(self):
        from spatialdds_demo_idl18.oarc_demo import BootstrapQuery
        for name, cls in lanes.EXTENSIONS.items():
            with self.subTest(name):
                self.assertIs(sd.dds.class_for(name), cls)
                self.assertIs(sd.dds.class_for(sd.dds.fqn_of(cls)), cls)
                self.assertIsNotNone(codec.key_member(sd.dds.fqn_of(cls)),
                                     f"{name} must be keyed")
        doc = {"client_id": "c1", "client_kind": "AR", "capabilities": ["vps"],
               "location_hint": "9v6kp",
               "stamp": {"sec": "1792890000", "nanosec": 0}}
        q = sd.dds.from_json(BootstrapQuery, doc)
        self.assertEqual(sd.dds.to_json(q), doc)
        self.assertEqual(sd.dds.key_of(q), "c1")


class LanesOutsideTheScene(unittest.TestCase):

    def test_each_is_a_name_section_3_3_1_accepts(self):
        for name, type_name in (
                (lanes.TOPIC_VPS_QUERY, "vps_query"),
                (lanes.TOPIC_VPS_RESULT, "vps_response"),
                (lanes.TOPIC_BOOTSTRAP_QUERY, "oarc.bootstrap_query"),
                (lanes.TOPIC_BOOTSTRAP_RESPONSE, "oarc.bootstrap_response"),
                (lanes.reply_topic("bridge-1"), "coverage_response"),
                (lanes.anchors_delta("fountain"), "anchor_delta")):
            with self.subTest(name):
                self.assertEqual(registry.check_topic(name, type_name), name)


if __name__ == "__main__":
    unittest.main()
