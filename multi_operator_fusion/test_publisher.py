#!/usr/bin/env python3
"""Unit tests for multi_operator_fusion.publisher helpers.

Tests the pure functions — topic naming, spatial offset, operator
provenance stamping, and sensor-filter configuration — without loading
the nuScenes dataset or starting DDS.

Run: python multi_operator_fusion/test_publisher.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SELF_DIR = Path(__file__).resolve().parent
if str(SELF_DIR) not in sys.path:
    sys.path.insert(0, str(SELF_DIR))

from publisher import (  # noqa: E402
    SENSOR_FILTERS,
    _offset_xyz,
    _stamp_operator,
    _topic,
)


class TopicNaming(unittest.TestCase):
    def test_detection3d_topic(self):
        self.assertEqual(
            _topic("operator_a", "sensing", "detection3d", "v1"),
            "spatialdds/operator_a/sensing/detection3d/v1",
        )

    def test_vision_topic_includes_channel(self):
        self.assertEqual(
            _topic("operator_b", "vision", "CAM_FRONT", "frame", "v1"),
            "spatialdds/operator_b/vision/CAM_FRONT/frame/v1",
        )

    def test_ego_topic(self):
        self.assertEqual(
            _topic("operator_c", "ego", "pose", "v1"),
            "spatialdds/operator_c/ego/pose/v1",
        )


class OperatorStamping(unittest.TestCase):
    def test_adds_source_operator_key(self):
        payload = {"frame_seq": 1}
        _stamp_operator(payload, "operator_a")
        self.assertEqual(payload["source_operator"], "operator_a")
        self.assertEqual(payload["frame_seq"], 1)

    def test_overwrites_existing_source(self):
        payload = {"source_operator": "stale"}
        _stamp_operator(payload, "infrastructure")
        self.assertEqual(payload["source_operator"], "infrastructure")


class OffsetApplication(unittest.TestCase):
    def test_applies_to_nested_xyz(self):
        payload = {"pose_se3": {"t": {"x": 1.0, "y": 2.0, "z": 3.0}}}
        _offset_xyz(payload, [("pose_se3", "t")], (10.0, 20.0, 30.0))
        self.assertEqual(payload["pose_se3"]["t"], {"x": 11.0, "y": 22.0, "z": 33.0})

    def test_applies_to_list_of_detections(self):
        payload = {"detections": [
            {"center": {"x": 0.0, "y": 0.0, "z": 0.0}},
            {"center": {"x": 5.0, "y": -5.0, "z": 1.0}},
        ]}
        for det in payload["detections"]:
            _offset_xyz(det, [("center",)], (100.0, 0.0, 0.0))
        self.assertEqual(payload["detections"][0]["center"]["x"], 100.0)
        self.assertEqual(payload["detections"][1]["center"]["x"], 105.0)
        self.assertEqual(payload["detections"][1]["center"]["y"], -5.0)

    def test_missing_path_is_noop(self):
        payload = {"other": 1}
        _offset_xyz(payload, [("pose_se3", "t")], (1.0, 2.0, 3.0))
        self.assertEqual(payload, {"other": 1})

    def test_zero_offset_leaves_values_unchanged(self):
        payload = {"xyz_m": {"x": 1.5, "y": 2.5, "z": 3.5}}
        _offset_xyz(payload, [("xyz_m",)], (0.0, 0.0, 0.0))
        self.assertEqual(payload["xyz_m"], {"x": 1.5, "y": 2.5, "z": 3.5})


class SensorFilterSpec(unittest.TestCase):
    """Matches design spec: Partner A=full, B=camera-only, C=lidar+radar."""

    def test_partner_a_full_suite(self):
        self.assertEqual(SENSOR_FILTERS["full"], {"camera", "lidar", "radar"})

    def test_partner_b_camera_only(self):
        self.assertEqual(SENSOR_FILTERS["camera"], {"camera"})

    def test_partner_c_lidar_radar(self):
        self.assertEqual(SENSOR_FILTERS["lidar_radar"], {"lidar", "radar"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
