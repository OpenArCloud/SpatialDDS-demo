#!/usr/bin/env python3
"""Unit tests for the infrastructure publisher's pure helpers.

Covers GPS->ENU conversion, Detection3D payload shape (must match what
the fusion service's ``_parse_detection`` expects), velocity from
frame-to-frame history, and offset application.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SELF_DIR = Path(__file__).resolve().parent
if str(SELF_DIR) not in sys.path:
    sys.path.insert(0, str(SELF_DIR))

from fusion_service import _parse_detection  # noqa: E402
from infrastructure_publisher import (  # noqa: E402
    SOURCE_OPERATOR,
    _apply_offset,
    _stamp_from_index,
    _velocity_from_history,
    gps_to_enu,
    make_detection3d_payload,
)


class GpsToEnu(unittest.TestCase):
    def test_same_point_is_origin(self):
        east, north = gps_to_enu(37.0, -122.0, 37.0, -122.0)
        self.assertAlmostEqual(east, 0.0, places=6)
        self.assertAlmostEqual(north, 0.0, places=6)

    def test_one_degree_north(self):
        _, north = gps_to_enu(37.0, -122.0, 38.0, -122.0)
        self.assertAlmostEqual(north, 111_000.0, delta=1.0)

    def test_east_shrinks_with_latitude(self):
        east_eq, _ = gps_to_enu(0.0, 0.0, 0.0, 1.0)
        east_hi, _ = gps_to_enu(60.0, 0.0, 60.0, 1.0)
        self.assertAlmostEqual(east_hi / east_eq, 0.5, delta=0.01)

    def test_vehicle_west_of_bs_is_negative_east(self):
        east, _ = gps_to_enu(33.0, -112.0, 33.0, -112.001)
        self.assertLess(east, 0.0)


class VelocityFromHistory(unittest.TestCase):
    def test_first_sample_has_zero_velocity(self):
        self.assertEqual(_velocity_from_history(None, (10.0, 20.0, 1)),
                         (0.0, 0.0, 0.0))

    def test_finite_difference_at_10hz(self):
        # Δseq=10 -> dt=1s, Δx=5m, Δy=-3m -> v=(5, -3, 0)
        v = _velocity_from_history((0.0, 0.0, 1), (5.0, -3.0, 11))
        self.assertAlmostEqual(v[0], 5.0)
        self.assertAlmostEqual(v[1], -3.0)
        self.assertEqual(v[2], 0.0)

    def test_same_frame_seq_does_not_divide_by_zero(self):
        v = _velocity_from_history((0.0, 0.0, 5), (1.0, 1.0, 5))
        self.assertTrue(all(map(lambda x: x == x, v)))  # no NaNs


class Detection3DPayload(unittest.TestCase):
    def test_payload_is_parseable_by_fusion_service(self):
        payload = make_detection3d_payload(
            frame_seq=42, stamp=_stamp_from_index(42),
            east=12.5, north=-3.0, up=0.5,
            velocity=(1.0, -1.0, 0.0),
        )
        self.assertEqual(payload["source_operator"], SOURCE_OPERATOR)
        self.assertEqual(len(payload["dets"]), 1)
        det = _parse_detection(payload["dets"][0], "infrastructure",
                               "det3d", default_sigma=0.5)
        self.assertIsNotNone(det, "Infra payload must parse through fusion service")
        self.assertAlmostEqual(det.position.x, 12.5)
        self.assertAlmostEqual(det.velocity.vx, 1.0)
        self.assertEqual(det.object_class, "vehicle")

    def test_offset_shifts_detection_center(self):
        payload = make_detection3d_payload(
            frame_seq=1, stamp=_stamp_from_index(1),
            east=0.0, north=0.0, up=0.0,
            velocity=(0.0, 0.0, 0.0),
        )
        _apply_offset(payload["dets"][0], (100.0, 50.0, 0.0))
        self.assertEqual(payload["dets"][0]["detection"]["center"],
                         [100.0, 50.0, 0.0])

    def test_payload_is_a_real_operator_detection_set(self):
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.oarc_demo import OperatorDetectionSet

        payload = make_detection3d_payload(
            frame_seq=7, stamp=_stamp_from_index(7),
            east=1.0, north=2.0, up=0.0, velocity=(0.5, 0.0, 0.0),
        )
        det_set = from_json(OperatorDetectionSet, payload)
        self.assertEqual(det_set.source_operator, SOURCE_OPERATOR)
        self.assertEqual(det_set.dets[0].source_modality, "radar")

    def test_stamp_from_index_matches_10hz(self):
        self.assertEqual(_stamp_from_index(0), {"sec": 0, "nanosec": 0})
        self.assertEqual(_stamp_from_index(15), {"sec": 1, "nanosec": 500_000_000})


if __name__ == "__main__":
    unittest.main(verbosity=2)
