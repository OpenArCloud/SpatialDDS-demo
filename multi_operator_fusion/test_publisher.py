#!/usr/bin/env python3
"""Unit tests for multi_operator_fusion.publisher helpers.

Tests the pure functions — topic naming, spatial offset, and sensor-filter
configuration — without loading the nuScenes dataset or starting DDS.

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
    LANES,
    SENSOR_FILTERS,
    _offset_vec,
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


class OperatorIdentity(unittest.TestCase):
    """
    The operator lives in the topic name, not in the payload.

    Every payload used to carry a stamped `source_operator`, including spec
    types that have no such field — where it was simply dropped on the wire.
    DDS expects that kind of identity in the topic, and it is there.
    """

    def test_operator_is_in_every_topic(self):
        for parts, _type_name, _profile in (
                lane for lane in LANES.values() if lane is not None):
            with self.subTest(lane="/".join(parts)):
                self.assertTrue(
                    _topic("operator_a", *parts).startswith(
                        "spatialdds/operator_a/"))


class OffsetApplication(unittest.TestCase):
    """Vec3 is ``double[3]``, so an offset shifts an array, not a dict."""

    def test_applies_to_nested_vec(self):
        payload = {"pose": {"t": [1.0, 2.0, 3.0]}}
        _offset_vec(payload, ("pose", "t"), (10.0, 20.0, 30.0))
        self.assertEqual(payload["pose"]["t"], [11.0, 22.0, 33.0])

    def test_applies_to_each_detection(self):
        dets = [{"center": [0.0, 0.0, 0.0]}, {"center": [5.0, -5.0, 1.0]}]
        for det in dets:
            _offset_vec(det, ("center",), (100.0, 0.0, 0.0))
        self.assertEqual(dets[0]["center"], [100.0, 0.0, 0.0])
        self.assertEqual(dets[1]["center"], [105.0, -5.0, 1.0])

    def test_missing_path_is_noop(self):
        payload = {"other": 1}
        _offset_vec(payload, ("pose", "t"), (1.0, 2.0, 3.0))
        self.assertEqual(payload, {"other": 1})

    def test_dict_form_is_still_accepted(self):
        """The nuScenes converters are migrated, but be forgiving of input."""
        payload = {"xyz_m": {"x": 1.5, "y": 2.5, "z": 3.5}}
        _offset_vec(payload, ("xyz_m",), (1.0, 0.0, 0.0))
        self.assertEqual(payload["xyz_m"], [2.5, 2.5, 3.5])

    def test_zero_offset_leaves_values_unchanged(self):
        payload = {"xyz_m": [1.5, 2.5, 3.5]}
        _offset_vec(payload, ("xyz_m",), (0.0, 0.0, 0.0))
        self.assertEqual(payload["xyz_m"], [1.5, 2.5, 3.5])


class LaneTable(unittest.TestCase):
    def test_every_lane_names_a_resolvable_type_and_a_real_profile(self):
        from spatialdds_demo import qos_profiles, topic_types

        for key, lane in LANES.items():
            if lane is None:
                continue                   # per-camera, built at publish time
            _parts, type_name, profile = lane
            with self.subTest(lane=key):
                self.assertIsNotNone(topic_types.try_resolve(type_name))
                self.assertIsNotNone(qos_profiles.get(profile))

    def test_detection_lane_carries_velocity(self):
        """
        This lane feeds the fusion service, which gates association on
        velocity. nuScenes has it (box_velocity) and semantics::Detection3D
        has no field for it, so the lane carries the composed type.
        """
        self.assertEqual(LANES["detection3d"][1], "detection3d")


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
