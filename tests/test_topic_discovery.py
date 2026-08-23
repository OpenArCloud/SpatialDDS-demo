"""
Discovery-driven typed subscription.

The envelope's real convenience was one reader seeing everything. Typed topics
give that up, and this is what replaces it: a consumer reads announces and
subscribes to whatever they advertise, using TopicMeta.type to pick the class
and TopicMeta.qos_profile to pick the QoS.

The bus part needs a DDS domain and skips loudly without one; the registry and
TopicMeta parsing are pure.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import topic_types  # noqa: E402
from spatialdds_demo.topics import REGISTERED_TOPIC_TYPES, validate_topic_meta  # noqa: E402


class Registry(unittest.TestCase):
    def test_registered_names_resolve_to_classes(self):
        for name in ("geopose", "planned_trajectory", "entity_binding",
                     "spatial_event", "radar_detection", "video_frame"):
            with self.subTest(type=name):
                self.assertTrue(callable(topic_types.resolve(name)))

    def test_radar_detection_is_the_spec_set_type(self):
        self.assertEqual(topic_types.resolve("radar_detection").__name__, "Detection3DSet")

    def test_unknown_type_is_skippable_not_fatal(self):
        """§3.3.2 treats unregistered values as extension points."""
        self.assertIsNone(topic_types.try_resolve("someone.elses.type"))
        with self.assertRaises(topic_types.UnknownTopicType):
            topic_types.resolve("someone.elses.type")

    def test_every_extension_is_accepted_by_the_publish_validator(self):
        """
        The validator's extension list and this registry must agree.

        They disagreed once — a type was added here but not there — and the
        publish-path validator refused the announce, which is exactly what it
        is for. They now share one list; this keeps them sharing it.
        """
        rows = [
            {"name": f"spatialdds/x/{i}/v1", "type": type_name,
             "version": "v1", "qos_profile": "POSE_RT"}
            for i, type_name in enumerate(topic_types.EXTENSIONS)
        ]
        ok, errors = validate_topic_meta(rows)
        self.assertTrue(ok, errors)

    def test_registered_types_are_a_subset_of_the_spec_registry(self):
        unknown = set(topic_types.REGISTERED) - REGISTERED_TOPIC_TYPES
        self.assertEqual(unknown, set(), f"not in the 3.3.2 registry: {unknown}")


class TopicMetaParsing(unittest.TestCase):
    """subscribe_from_topic_meta must tolerate what the bus really carries."""

    def setUp(self):
        from spatialdds_demo.typed_transport import MultiTopicSubscriber
        self.cls = MultiTopicSubscriber

    def _subscriber(self):
        try:
            from cyclonedds.domain import DomainParticipant
            return self.cls(DomainParticipant(37))
        except Exception as exc:
            raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")

    def test_unknown_and_incomplete_rows_are_skipped(self):
        sub = self._subscriber()
        added = sub.subscribe_from_topic_meta([
            {"name": "spatialdds/a/v1", "type": "someone.elses.type",
             "version": "v1", "qos_profile": "POSE_RT"},          # unknown type
            {"name": "", "type": "geopose", "version": "v1",
             "qos_profile": "POSE_RT"},                            # no name
            {"name": "spatialdds/b/v1", "type": "geopose", "version": "v1"},  # no qos
        ])
        self.assertEqual(added, [])
        self.assertEqual(sub.topics, [])

    def test_a_good_row_subscribes_once(self):
        sub = self._subscriber()
        row = [{"name": "spatialdds/test/pose/v1", "type": "geopose",
                "version": "v1", "qos_profile": "POSE_RT"}]
        self.assertEqual(sub.subscribe_from_topic_meta(row),
                         ["spatialdds/test/pose/v1"])
        # Idempotent: a re-announce must not create a second reader.
        self.assertEqual(sub.subscribe_from_topic_meta(row), [])
        self.assertEqual(sub.topics, ["spatialdds/test/pose/v1"])


if __name__ == "__main__":
    unittest.main()
