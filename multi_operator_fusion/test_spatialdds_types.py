"""Unit tests for the v1.6 dict-builder helpers.

Pure-Python — no DDS, no FastAPI. Verifies shape + presence flags +
JSON-serialisability so the synthetic publisher and fusion service can
rely on these dicts riding cleanly through the envelope's
``payload_json``.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from spatialdds_types import (  # noqa: E402
    SCHEMA_CORE,
    make_component_ref,
    make_entity_binding,
    make_planned_trajectory,
    make_planned_waypoint,
)


class TestPlannedWaypoint(unittest.TestCase):
    def test_minimal(self):
        wp = make_planned_waypoint(1.0, 2.0, 3.0, timestamp_s=10.5)
        self.assertEqual(wp["pose"]["t"], {"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertEqual(wp["pose"]["q"], {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0})
        self.assertEqual(wp["stamp"], {"sec": 10, "nanosec": 500_000_000})
        self.assertFalse(wp["has_velocity"])
        self.assertFalse(wp["has_uncertainty"])
        self.assertFalse(wp["has_confidence"])
        self.assertNotIn("velocity", wp)
        self.assertNotIn("position_uncertainty_m", wp)

    def test_with_velocity_and_uncertainty(self):
        wp = make_planned_waypoint(
            0, 0, 0, timestamp_s=1.0,
            vx=2.5, vy=-1.0, uncertainty_m=0.7, confidence=0.85,
        )
        self.assertTrue(wp["has_velocity"])
        self.assertEqual(wp["velocity"], {"x": 2.5, "y": -1.0, "z": 0.0})
        self.assertEqual(wp["position_uncertainty_m"], 0.7)
        self.assertEqual(wp["confidence"], 0.85)

    def test_stamp_carries_fractional_seconds(self):
        wp = make_planned_waypoint(0, 0, 0, timestamp_s=10.25)
        self.assertEqual(wp["stamp"], {"sec": 10, "nanosec": 250_000_000})

    def test_stamp_zero_nsec(self):
        wp = make_planned_waypoint(0, 0, 0, timestamp_s=42.0)
        self.assertEqual(wp["stamp"], {"sec": 42, "nanosec": 0})

    def test_serialisable(self):
        wp = make_planned_waypoint(1, 2, 3, timestamp_s=0.5,
                                     vx=1, uncertainty_m=0.1, confidence=1.0)
        # round-trip through JSON
        round_tripped = json.loads(json.dumps(wp))
        self.assertEqual(round_tripped, wp)


class TestPlannedTrajectory(unittest.TestCase):
    def test_basic_shape(self):
        wps = [make_planned_waypoint(i, 0, 0, timestamp_s=float(i))
                for i in range(3)]
        traj = make_planned_trajectory(
            agent_id="op_a_ego", plan_id="plan_42", plan_revision=7,
            frame_ref_fqn="op_a/map", waypoints=wps,
            horizon_sec=3.0, replan_rate_hz=2.0, timestamp_s=10.0,
        )
        self.assertEqual(traj["schema_version"], SCHEMA_CORE)
        self.assertEqual(traj["agent_id"], "op_a_ego")
        self.assertEqual(traj["plan_revision"], 7)
        self.assertEqual(traj["frame_ref"], {"uuid": "", "fqn": "op_a/map"})
        self.assertEqual(len(traj["waypoints"]), 3)
        self.assertTrue(traj["has_horizon_sec"])
        self.assertTrue(traj["has_replan_rate_hz"])
        self.assertFalse(traj["has_goal_pose"])
        self.assertEqual(traj["horizon_sec"], 3.0)

    def test_empty_waypoints_allowed(self):
        # Fusion-side code may publish an "intent cleared" trajectory.
        traj = make_planned_trajectory("a", "p", 0, "frame", waypoints=[])
        self.assertEqual(traj["waypoints"], [])

    def test_goal_pose_included(self):
        goal = {"t": {"x": 100, "y": 0, "z": 0},
                "q": {"x": 0, "y": 0, "z": 0, "w": 1}}
        traj = make_planned_trajectory("a", "p", 0, "frame", waypoints=[],
                                         goal_pose=goal)
        self.assertTrue(traj["has_goal_pose"])
        self.assertEqual(traj["goal_pose"], goal)

    def test_serialisable(self):
        wps = [make_planned_waypoint(0, 0, 0, timestamp_s=1.0,
                                       uncertainty_m=0.5, confidence=0.9)]
        traj = make_planned_trajectory("a", "p", 0, "frame", waypoints=wps,
                                          horizon_sec=1.0, replan_rate_hz=2.0)
        round_tripped = json.loads(json.dumps(traj))
        self.assertEqual(round_tripped["waypoints"][0]["confidence"], 0.9)


class TestEntityBinding(unittest.TestCase):
    def test_component_ref_shape(self):
        cr = make_component_ref(topic="spatialdds/op_a/sensing/detection3d/v1",
                                  key="det_42")
        self.assertEqual(cr, {"topic": "spatialdds/op_a/sensing/detection3d/v1",
                                "key": "det_42"})

    def test_binding_minimal(self):
        components = [
            make_component_ref("spatialdds/platform/fusion/track/v1", "t1"),
            make_component_ref("spatialdds/op_a/sensing/detection3d/v1", "det_3"),
        ]
        b = make_entity_binding(entity_id="ent_t1", entity_class="vehicle.car",
                                  components=components, source_id="fusion",
                                  timestamp_s=42.0)
        self.assertEqual(b["schema_version"], SCHEMA_CORE)
        self.assertEqual(b["entity_id"], "ent_t1")
        self.assertEqual(b["entity_class"], "vehicle.car")
        self.assertEqual(len(b["components"]), 2)
        self.assertEqual(b["components"][0]["topic"],
                          "spatialdds/platform/fusion/track/v1")
        self.assertFalse(b["has_pose"])
        self.assertEqual(b["source_id"], "fusion")
        self.assertEqual(b["stamp"]["sec"], 42)

    def test_binding_with_pose(self):
        pose = {"t": {"x": 1, "y": 2, "z": 0},
                "q": {"x": 0, "y": 0, "z": 0, "w": 1}}
        b = make_entity_binding("e", "vehicle", [], pose=pose)
        self.assertTrue(b["has_pose"])
        self.assertEqual(b["pose"], pose)

    def test_serialisable(self):
        b = make_entity_binding("e", "vehicle",
                                  [make_component_ref("t/v1", "k")],
                                  pose={"t": {"x": 0, "y": 0, "z": 0},
                                          "q": {"x": 0, "y": 0, "z": 0, "w": 1}})
        round_tripped = json.loads(json.dumps(b))
        self.assertEqual(round_tripped["entity_class"], "vehicle")


if __name__ == "__main__":
    unittest.main()
