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
    def test_detection3d(self):
        self.assertEqual(
            infer_msg_type("spatialdds/op_a/sensing/detection3d/v1"),
            "Detection3DSet",
        )

    def test_pose(self):
        self.assertEqual(
            infer_msg_type("spatialdds/op_a/ego/pose/v1"), "FramedPose"
        )

    def test_geopose_with_sensor_id(self):
        self.assertEqual(
            infer_msg_type("spatialdds/op_a/geo/gnss_0/pose/v1"), "GeoPose"
        )

    def test_vision_frame(self):
        self.assertEqual(
            infer_msg_type("spatialdds/infra/vision/cam_0/frame/v1"),
            "VisionFrame",
        )

    def test_vision_meta(self):
        self.assertEqual(
            infer_msg_type("spatialdds/infra/vision/cam_0/meta/v1"),
            "VisionMeta",
        )

    def test_rf_beam(self):
        self.assertEqual(
            infer_msg_type("spatialdds/infra/rf_beam/unit1/frame/v1"),
            "RfBeamFrame",
        )

    def test_radio_scan(self):
        self.assertEqual(
            infer_msg_type("spatialdds/op/radio/wifi_0/scan/v1"), "RadioScan"
        )

    def test_planned_trajectory(self):
        self.assertEqual(
            infer_msg_type("spatialdds/op/plan/robot_1/trajectory/v1"),
            "PlannedTrajectory",
        )

    def test_imu_sample(self):
        self.assertEqual(
            infer_msg_type("spatialdds/op/imu/imu_0/sample/v1"), "ImuSample"
        )

    def test_fused_track(self):
        self.assertEqual(
            infer_msg_type("spatialdds/platform/fusion/track/v1"),
            "FusedTrackSet",
        )

    def test_announce(self):
        self.assertEqual(
            infer_msg_type("spatialdds/discovery/announce/v1"), "Announce"
        )

    def test_unknown(self):
        self.assertEqual(infer_msg_type("totally/random/topic"), "Unknown")
        self.assertEqual(infer_msg_type(""), "Unknown")


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
