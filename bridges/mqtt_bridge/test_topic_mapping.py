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
    get_qos,
    infer_msg_type,
    matches_any,
    to_broker_filter,
)


class TestMsgTypeInference(unittest.TestCase):
    """
    The type is the name's type segment (§3.3.1), which this deployment makes
    the lane's registered type. The table of guessing globs this replaced
    returned "Unknown" for any name it had not been taught.
    """

    CASES = [
        ("spatialdds/op_a/sensing/detection3d/v1",        "detection3d"),
        ("spatialdds/op_a/ego/framed_pose/v1",            "framed_pose"),
        ("spatialdds/op_a/gnss_0/geopose/v1",             "geopose"),
        ("spatialdds/infra/cam_0/video_frame/v1",         "video_frame"),
        ("spatialdds/infra/cam_0/video_meta/v1",          "video_meta"),
        ("spatialdds/infra/unit1/rf_beam/v1",             "rf_beam"),
        ("spatialdds/op/plan/robot_1/trajectory/v1",   "planned_trajectory"),
        ("spatialdds/platform/fusion/fused_track/v1",     "fused_track"),
        ("spatialdds/platform/fusion/oarc.fusion_coverage/v1",
         "oarc.fusion_coverage"),
        ("spatialdds/platform/entity/binding/v1",  "entity_binding"),
        ("spatialdds/platform/events/spatial_event/v1",   "spatial_event"),
    ]

    def test_the_type_is_the_type_segment(self):
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
            from spatialdds_demo import lanes
        except Exception as exc:                       # pragma: no cover
            self.skipTest(f"generated bindings unavailable: {exc}")
        for _, name in self.CASES:
            with self.subTest(type=name):
                self.assertIsNotNone(lanes.class_for(name))

    def test_announce_is_its_own_case(self):
        # Discovery is not a TopicMeta lane, so it has no registry name.
        for topic in ("spatialdds/discovery/announce/v1",
                      "spatialdds/svc_a/discovery/announce/v1"):
            with self.subTest(topic=topic):
                self.assertEqual(infer_msg_type(topic),
                                 "spatialdds/discovery/announce")

    def test_the_spec_templates_name_their_own_types(self):
        """§2 gives PlannedTrajectory and EntityBinding lane templates of
        their own; their type is not their type segment."""
        self.assertEqual(
            infer_msg_type("spatialdds/op/plan/robot_1/trajectory/v1"),
            "planned_trajectory")
        self.assertEqual(
            infer_msg_type("spatialdds/platform/entity/binding/v1"),
            "entity_binding")
        # A `*` in a template is one segment, never several.
        self.assertEqual(
            infer_msg_type("spatialdds/a/b/plan/c/trajectory/v1"), "Unknown")

    def test_a_name_that_is_not_a_lane_is_unknown(self):
        # Six segments, the pre-adoption sensor lanes' shape: not §3.3.1.
        for topic in ("spatialdds/op/vision/cam_0/frame/v1",
                      "spatialdds/x/y/z", "totally/random/topic/x/v1"):
            with self.subTest(topic=topic):
                self.assertEqual(infer_msg_type(topic), "Unknown")


class TestQosMapping(unittest.TestCase):
    def test_meta_retained(self):
        self.assertEqual(get_qos("spatialdds/op/cam_0/video_meta/v1"), (1, True))

    def test_binding_retained(self):
        self.assertEqual(
            get_qos("spatialdds/platform/entity/binding/v1"), (1, True))

    def test_announce_retained(self):
        self.assertEqual(
            get_qos("spatialdds/svc_a/discovery/announce/v1"), (1, True))

    def test_detection3d_reliable(self):
        self.assertEqual(
            get_qos("spatialdds/op/sensing/detection3d/v1"), (1, False))

    def test_pose_best_effort(self):
        self.assertEqual(get_qos("spatialdds/op/ego/framed_pose/v1"), (0, False))

    def test_frame_best_effort(self):
        self.assertEqual(get_qos("spatialdds/infra/cam_0/video_frame/v1"),
                         (0, False))

    def test_track_reliable(self):
        self.assertEqual(
            get_qos("spatialdds/platform/fusion/fused_track/v1"), (1, False))

    def test_default(self):
        self.assertEqual(get_qos("totally/random/topic"), DEFAULT_QOS)


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
            "spatialdds/operator_a/ego/framed_pose/v1",
            ["spatialdds/operator_a/#"],
        ))

    def test_non_matching(self):
        self.assertFalse(matches_any(
            "spatialdds/platform/fusion/fused_track/v1",
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
            "spatialdds/operator_b/ego/framed_pose/v1", patterns))
        self.assertFalse(matches_any(
            "spatialdds/infrastructure/unit1/radar_detection/v1", patterns))

    def test_outbound_pattern(self):
        patterns = [
            "spatialdds/platform/fusion/#",
            "spatialdds/infrastructure/#",
        ]
        self.assertTrue(matches_any(
            "spatialdds/platform/fusion/fused_track/v1", patterns))
        self.assertTrue(matches_any(
            "spatialdds/infrastructure/unit1/rf_beam/v1", patterns))
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
            to_broker_filter("spatialdds/op_+/cam_+/video_frame/v1"),
            "spatialdds/+/+/video_frame/v1",
        )


if __name__ == "__main__":
    unittest.main()
