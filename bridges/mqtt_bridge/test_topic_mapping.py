"""Pure unit tests for topic_mapping. No MQTT, no DDS."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from topic_mapping import (  # noqa: E402
    DEFAULT_QOS,
    QOS_MAP,
    TOPIC_TYPE_MAP,
    get_qos,
    infer_msg_type,
    matches_any,
    to_broker_filter,
)


class TestMsgTypeInference(unittest.TestCase):
    """
    Inferred names are §3.3.2 registry names, so a consumer can resolve one
    into a reader. The old table returned invented labels like
    ``"Detection3DSet"`` that no registry knew.
    """

    CASES = [
        ("spatialdds/op_a/sensing/detection3d/v1", "detection3d"),
        ("spatialdds/op_a/ego/pose/v1",            "framed_pose"),
        ("spatialdds/op_a/geo/gnss_0/pose/v1",     "geopose"),
        ("spatialdds/infra/vision/cam_0/frame/v1", "video_frame"),
        ("spatialdds/infra/vision/cam_0/meta/v1",  "video_meta"),
        ("spatialdds/infra/rf_beam/unit1/frame/v1", "rf_beam"),
        ("spatialdds/op/plan/robot_1/trajectory/v1", "planned_trajectory"),
        ("spatialdds/platform/fusion/track/v1",    "fused_track"),
        ("spatialdds/platform/fusion/coverage/v1", "oarc.fusion_coverage"),
        ("spatialdds/platform/entity/binding/v1",  "entity_binding"),
        ("spatialdds/platform/events/conflict/v1", "spatial_event"),
    ]

    def test_inferred_names_are_registry_names(self):
        for topic, expected in self.CASES:
            with self.subTest(topic=topic):
                self.assertEqual(infer_msg_type(topic), expected)

    def test_every_inferred_name_resolves_to_a_class(self):
        """
        The point of inferring a type: the bridge has to build the payload
        into it. A name that resolves to nothing is a name the bridge
        cannot act on.
        """
        try:
            from spatialdds_demo import topic_types
        except Exception as exc:                       # pragma: no cover
            self.skipTest(f"generated bindings unavailable: {exc}")
        for _, name in self.CASES:
            with self.subTest(type=name):
                self.assertIsNotNone(topic_types.try_resolve(name))

    def test_announce_is_its_own_case(self):
        # Discovery is not a TopicMeta lane, so it has no registry name.
        self.assertEqual(infer_msg_type("spatialdds/discovery/announce/v1"),
                         "spatialdds/discovery/announce")

    def test_unmatched_topic_is_unknown(self):
        self.assertEqual(infer_msg_type("spatialdds/op/some/other/v1"),
                         "Unknown")


class TestQosMapping(unittest.TestCase):
    def test_meta_retained(self):
        qos, retain = get_qos("spatialdds/op/vision/cam_0/meta/v1")
        self.assertEqual(qos, 1)
        self.assertTrue(retain)

    def test_binding_retained(self):
        qos, retain = get_qos("spatialdds/platform/entity/binding/v1")
        self.assertEqual(qos, 1)
        self.assertTrue(retain)

    def test_detection3d_reliable(self):
        qos, retain = get_qos("spatialdds/op/sensing/detection3d/v1")
        self.assertEqual(qos, 1)
        self.assertFalse(retain)

    def test_pose_best_effort(self):
        qos, retain = get_qos("spatialdds/op/ego/pose/v1")
        self.assertEqual(qos, 0)
        self.assertFalse(retain)

    def test_frame_best_effort(self):
        qos, retain = get_qos("spatialdds/infra/vision/cam_0/frame/v1")
        self.assertEqual(qos, 0)
        self.assertFalse(retain)

    def test_track_reliable(self):
        qos, retain = get_qos("spatialdds/platform/fusion/track/v1")
        self.assertEqual(qos, 1)
        self.assertFalse(retain)

    def test_default(self):
        qos, retain = get_qos("totally/random/topic")
        self.assertEqual((qos, retain), DEFAULT_QOS)


class TestPatternMatching(unittest.TestCase):
    def test_mqtt_plus_wildcard(self):
        self.assertTrue(matches_any(
            "spatialdds/operator_a/sensing/detection3d/v1",
            ["spatialdds/operator_+/sensing/#"],
        ))

    def test_hash_wildcard_multi_level(self):
        self.assertTrue(matches_any(
            "spatialdds/operator_a/sensing/detection3d/v1",
            ["spatialdds/operator_a/#"],
        ))
        self.assertTrue(matches_any(
            "spatialdds/operator_a/ego/pose/v1",
            ["spatialdds/operator_a/#"],
        ))

    def test_non_matching(self):
        self.assertFalse(matches_any(
            "spatialdds/platform/fusion/track/v1",
            ["spatialdds/operator_+/sensing/#"],
        ))

    def test_empty_topic(self):
        self.assertFalse(matches_any("", ["spatialdds/#"]))

    def test_empty_pattern_list(self):
        self.assertFalse(matches_any("spatialdds/x/y/z", []))

    def test_multi_pattern_any(self):
        patterns = [
            "spatialdds/operator_+/sensing/#",
            "spatialdds/operator_+/ego/#",
        ]
        self.assertTrue(matches_any(
            "spatialdds/operator_a/sensing/detection3d/v1", patterns))
        self.assertTrue(matches_any(
            "spatialdds/operator_b/ego/pose/v1", patterns))
        self.assertFalse(matches_any(
            "spatialdds/infrastructure/rad/unit1/frame/v1", patterns))

    def test_outbound_pattern(self):
        patterns = [
            "spatialdds/platform/fusion/#",
            "spatialdds/infrastructure/#",
        ]
        self.assertTrue(matches_any(
            "spatialdds/platform/fusion/track/v1", patterns))
        self.assertTrue(matches_any(
            "spatialdds/infrastructure/rf_beam/unit1/frame/v1", patterns))
        self.assertFalse(matches_any(
            "spatialdds/operator_a/sensing/detection3d/v1", patterns))


class TestBrokerFilterCoarsening(unittest.TestCase):
    """MQTT requires `+` to be a whole segment; the bridge coarsens
    richer user patterns into valid MQTT subscription filters."""

    def test_mixed_segment_coarsened(self):
        # operator_+ → +
        self.assertEqual(
            to_broker_filter("spatialdds/operator_+/sensing/#"),
            "spatialdds/+/sensing/#",
        )

    def test_pure_plus_unchanged(self):
        self.assertEqual(
            to_broker_filter("spatialdds/+/sensing/#"),
            "spatialdds/+/sensing/#",
        )

    def test_no_wildcards_unchanged(self):
        self.assertEqual(
            to_broker_filter("spatialdds/operator_a/sensing/detection3d/v1"),
            "spatialdds/operator_a/sensing/detection3d/v1",
        )

    def test_hash_only_unchanged(self):
        self.assertEqual(to_broker_filter("spatialdds/#"), "spatialdds/#")

    def test_multiple_mixed_segments(self):
        # Both segments coarsened independently
        self.assertEqual(
            to_broker_filter("spatialdds/op_+/cam_+/frame/v1"),
            "spatialdds/+/+/frame/v1",
        )


class TestPriorityOrdering(unittest.TestCase):
    """First-match wins — verify the table is ordered specific→general."""

    def test_more_specific_meta_beats_generic_pose(self):
        # `*/meta/*` retains; `*/pose/*` does not. A topic ending in /meta/
        # should hit /meta/ even if /pose/ also matches.
        qos, retain = get_qos("spatialdds/op/vision/cam_0/meta/v1")
        self.assertTrue(retain)


if __name__ == "__main__":
    unittest.main()
