"""Smoke tests for the Rerun subscriber's envelope dispatcher.

Verifies every msg_type the demo can produce flows through
``handle_envelope`` without raising. We use a recording-only Rerun
stream (``rr.MemoryRecording``-style — actually just ``rr.init`` without
spawn / connect / serve) so the test doesn't pop up a viewer.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import rerun as rr  # noqa: E402

from subscriber_rerun import RerunMultiOpSubscriber, handle_envelope  # noqa: E402


def _ego_payload(operator: str, x: float = 0.0, y: float = 0.0):
    return {
        "stamp": {"sec": 1, "nanosec": 0},
        "pose": {"t": {"x": x, "y": y, "z": 0.0},
                  "q": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}},
    }


def _det_payload(operator: str):
    return {
        "stamp": {"sec": 1, "nanosec": 0},
        "source_operator": operator,
        "detections": [{
            "det_id": "obj_00",
            "center": {"x": 1.0, "y": 2.0, "z": 0.5},
            "size": {"x": 4.5, "y": 1.8, "z": 1.6},
            "q": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "class_id": "vehicle.car",
            "score": 0.85,
            "has_velocity": True,
            "velocity": {"x": 1.0, "y": 0.0, "z": 0.0},
        }],
    }


def _fused_tracks_payload():
    return {
        "stamp": {"sec": 1, "nanosec": 0},
        "tracks": [{
            "track_id": "fused-1",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "velocity": {"vx": 0.0, "vy": 0.0, "vz": 0.0},
            "object_class": "vehicle.car",
            "confidence": 0.9,
            "source_operators": ["operator_a", "operator_b"],
            "source_count": 2,
        }],
    }


def _plan_payload(agent: str = "operator_a_ego"):
    return {
        "stamp": {"sec": 1, "nanosec": 0},
        "agent_id": agent,
        "waypoints": [
            {"pose": {"t": {"x": float(i), "y": 0.0, "z": 0.0}},
              "stamp": {"sec": i, "nanosec": 0},
              "has_uncertainty": True,
              "position_uncertainty_m": 0.5 + 0.3 * i}
            for i in range(5)
        ],
    }


def _conflict_payload():
    return {
        "stamp": {"sec": 1, "nanosec": 0},
        "event_type": "trajectory_conflict",
        "agents": ["operator_a_ego", "operator_b_ego"],
        "min_distance_m": 1.4,
        "time_to_conflict": 2.5,
        "conflict_position": {"x": 0.0, "y": 0.0, "z": 0.0},
    }


def _binding_payload():
    return {
        "stamp": {"sec": 1, "nanosec": 0},
        "entity_id": "entity_fused-1",
        "entity_class": "vehicle.car",
        "components": [
            {"topic": "spatialdds/platform/fusion/track/v1", "key": "fused-1"},
            {"topic": "spatialdds/operator_a/sensing/detection3d/v1",
              "key": "obj_00"},
        ],
        "has_pose": True,
        "pose": {"t": {"x": 0.0, "y": 0.0, "z": 0.0},
                  "q": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}},
    }


def _announce_payload(operator: str):
    return {
        "stamp": {"sec": 1, "nanosec": 0},
        "operator": operator,
        "service_kind": "SENSING",
        "topics": [],
        "has_coverage": True,
        "coverage": {"type": "circle",
                       "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                       "radius_m": 50.0},
    }


def _coverage_payload():
    return {
        "stamp": {"sec": 1, "nanosec": 0},
        "metrics": {"track_count": 5, "multi_source_count": 2,
                     "multi_source_pct": 0.4,
                     "best_single_operator_count": 3,
                     "coverage_improvement": 1.6,
                     "per_operator_track_count": {"operator_a": 3,
                                                    "operator_b": 2}},
    }


class TestSubscriberRerun(unittest.TestCase):
    """The subscriber's envelope dispatcher should accept every demo
    msg_type and degrade gracefully on the unknown ones — never raise."""

    @classmethod
    def setUpClass(cls):
        # Recording-only stream; no viewer spawned, no DDS, no display.
        rr.init("test_subscriber_rerun", spawn=False)

    def setUp(self):
        # Build a subscriber but don't actually start the DDS reader —
        # we drive ``handle_envelope`` directly.
        self.sub = RerunMultiOpSubscriber.__new__(RerunMultiOpSubscriber)
        self.sub.dataroot = Path("/tmp")
        self.sub._trails = {}
        self.sub._trail_max = 200
        self.sub._coverage_logged = set()
        self.sub.debug = False

    def _drive(self, msg_type: str, topic: str, payload: dict,
                operator: str = "operator_a"):
        handle_envelope(self.sub, msg_type, topic, operator, payload)

    def test_module_imports(self):
        import subscriber_rerun  # noqa: F401

    def test_ego_pose(self):
        self._drive("NUSC_EGO_POSE",
                     "spatialdds/operator_a/ego/pose/v1",
                     _ego_payload("operator_a"))

    def test_detection3d_set(self):
        self._drive("NUSC_DET3D_SET",
                     "spatialdds/operator_a/sensing/detection3d/v1",
                     _det_payload("operator_a"))

    def test_infrastructure_detection_set(self):
        self._drive("INFRA_DET3D_SET",
                     "spatialdds/infrastructure/sensing/detection3d/v1",
                     _det_payload("infrastructure"),
                     operator="infrastructure")

    def test_fused_tracks(self):
        self._drive("NUSC_FUSED_TRACK_SET",
                     "spatialdds/platform/fusion/track/v1",
                     _fused_tracks_payload(),
                     operator="platform")

    def test_planned_trajectory(self):
        self._drive("PlannedTrajectory",
                     "spatialdds/operator_a/plan/operator_a_ego/trajectory/v1",
                     _plan_payload())

    def test_spatial_event_conflict(self):
        self._drive("SpatialEvent",
                     "spatialdds/platform/events/trajectory_conflict/v1",
                     _conflict_payload(),
                     operator="platform")

    def test_spatial_event_unknown_kind_does_not_raise(self):
        self._drive("SpatialEvent",
                     "spatialdds/platform/events/whatever/v1",
                     {"event_type": "some_other_event",
                      "stamp": {"sec": 1, "nanosec": 0}},
                     operator="platform")

    def test_entity_binding(self):
        self._drive("EntityBinding",
                     "spatialdds/platform/entity/binding/v1",
                     _binding_payload(),
                     operator="platform")

    def test_announce(self):
        self._drive("Announce",
                     "spatialdds/operator_a/discovery/announce/v1",
                     _announce_payload("operator_a"))

    def test_coverage_metrics(self):
        self._drive("NUSC_FUSION_COVERAGE",
                     "spatialdds/platform/fusion/coverage/v1",
                     _coverage_payload(),
                     operator="platform")

    def test_standard_msg_type_aliases_route(self):
        """The SpatialDDS standard names route to the same handlers as
        the in-tree NUSC_* / INFRA_* names."""
        self._drive("FramedPose",
                     "spatialdds/operator_a/ego/pose/v1",
                     _ego_payload("operator_a"))
        self._drive("Detection3DSet",
                     "spatialdds/operator_a/sensing/detection3d/v1",
                     _det_payload("operator_a"))
        self._drive("FusedTrackSet",
                     "spatialdds/platform/fusion/track/v1",
                     _fused_tracks_payload(),
                     operator="platform")
        self._drive("CoverageMetrics",
                     "spatialdds/platform/fusion/coverage/v1",
                     _coverage_payload(),
                     operator="platform")

    def test_unknown_msg_type_is_silent(self):
        # Mid-stream we may see a topic the subscriber has never been
        # taught about (forward-compat). Should not raise.
        self._drive("SomeFutureType",
                     "spatialdds/some_op/future/v1",
                     {"foo": "bar"})

    def test_ego_trail_accumulates_and_bounds(self):
        # Sanity check on the trail buffer cap.
        self.sub._trail_max = 5
        for i in range(20):
            self._drive("NUSC_EGO_POSE",
                         "spatialdds/operator_a/ego/pose/v1",
                         _ego_payload("operator_a", x=float(i), y=0.0))
        self.assertLessEqual(len(self.sub._trails["operator_a"]), 5)


if __name__ == "__main__":
    unittest.main()
