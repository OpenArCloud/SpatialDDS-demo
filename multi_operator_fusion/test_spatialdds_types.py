"""Unit tests for the payload builders.

Pure-Python — no DDS, no FastAPI. Verifies that each builder produces a dict
that ``json_mapping.from_json`` can build into its IDL type: complete, with
every presence flag beside its value. That check is what JSON-
serialisability used to stand in for, and it is strictly stronger — a
payload that is not a well-formed sample fails here rather than at some
consumer three processes away.
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
    circle_coverage,
    make_announce,
    make_component_ref,
    make_detection,
    make_entity_binding,
    make_framed_pose,
    make_planned_trajectory,
    make_planned_waypoint,
    topic_meta,
)


class TestPlannedWaypoint(unittest.TestCase):
    def test_minimal(self):
        wp = make_planned_waypoint(1.0, 2.0, 3.0, timestamp_s=10.5)
        # Vec3 is an IDL array, not an {x,y,z} object.
        self.assertEqual(wp["pose"]["t"], [1.0, 2.0, 3.0])
        self.assertEqual(wp["pose"]["q"], [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(wp["stamp"], {"sec": 10, "nanosec": 500_000_000})
        self.assertFalse(wp["has_velocity"])
        self.assertFalse(wp["has_uncertainty"])
        self.assertFalse(wp["has_confidence"])
        # Presence-flag pattern: the value field is always on the wire, and the
        # has_* flag is what says whether to read it. It is never omitted and
        # never null.
        self.assertEqual(wp["velocity"], [0.0, 0.0, 0.0])
        self.assertEqual(wp["position_uncertainty_m"], 0.0)

    def test_with_velocity_and_uncertainty(self):
        wp = make_planned_waypoint(
            0, 0, 0, timestamp_s=1.0,
            vx=2.5, vy=-1.0, uncertainty_m=0.7, confidence=0.85,
        )
        self.assertTrue(wp["has_velocity"])
        self.assertEqual(wp["velocity"], [2.5, -1.0, 0.0])
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
        # _frame_ref derives a stable UUIDv5 from the fqn when none is given.
        self.assertEqual(traj["frame_ref"]["fqn"], "op_a/map")
        self.assertTrue(traj["frame_ref"]["uuid"])
        self.assertEqual(traj["frame_ref"]["coord_convention"], "ENU")
        _unused = ({
            "uuid": "", "fqn": "op_a/map",
            "has_coord_convention": True, "coord_convention": "ENU",
        })
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
        b = make_entity_binding("e", "vehicle", [], position=(1.0, 2.0, 0.0),
                                frame_ref_fqn="scene/intersection")
        self.assertTrue(b["has_pose"])
        # A FramedPose: the pose, and the frame it means something in.
        self.assertEqual(b["pose"]["pose"]["t"], [1.0, 2.0, 0.0])
        self.assertEqual(b["pose"]["frame_ref"]["fqn"], "scene/intersection")

    def test_pose_is_present_but_flagged_off_when_omitted(self):
        b = make_entity_binding("e", "vehicle", [])
        self.assertFalse(b["has_pose"])
        self.assertEqual(b["pose"]["pose"]["t"], [0.0, 0.0, 0.0])

    def test_builds_into_the_real_type(self):
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.spatial.core import EntityBinding

        b = make_entity_binding("e", "vehicle",
                                [make_component_ref("t/v1", "k")],
                                position=(0.0, 0.0, 0.0),
                                frame_ref_fqn="scene/intersection")
        self.assertEqual(from_json(EntityBinding, b).entity_class, "vehicle")


if __name__ == "__main__":
    unittest.main()


class TestBuildersMatchTheIdl(unittest.TestCase):
    """
    A builder must not emit a field its type does not have.

    ``from_json`` ignores keys it does not recognise — it has to, because
    edge clients legitimately send extra ones — so a builder that invents a
    field produces a payload that looks right, builds without complaint, and
    silently loses that field on the wire. This is where to be strict: the
    demo's own builders are not an edge, and a name here that the IDL does
    not have is a bug.

    Found exactly that: `make_detection` wrote `tile_key.map_id`, which
    spatial::core::TileKey has never had.
    """

    def _assert_no_unknown_fields(self, cls, payload, path=""):
        import dataclasses

        from spatialdds_demo.json_mapping import _resolved_hints, _typename, _unwrap

        typename = _typename(cls)
        known = {f.name for f in dataclasses.fields(cls)}
        from spatialdds_idl._field_aliases import FIELD_ALIASES
        known |= set(FIELD_ALIASES.get(typename, {}).values())
        for key, value in payload.items():
            where = f"{path}.{key}" if path else key
            self.assertIn(key, known,
                          f"{where}: {typename} has no field {key!r}")

        hints = _resolved_hints(cls)
        for field in dataclasses.fields(cls):
            wire = FIELD_ALIASES.get(typename, {}).get(field.name, field.name)
            if wire not in payload:
                continue
            target = _unwrap(hints.get(field.name))
            value = payload[wire]
            if isinstance(target, type) and dataclasses.is_dataclass(target) \
                    and isinstance(value, dict):
                self._assert_no_unknown_fields(target, value,
                                               f"{path}.{wire}" if path else wire)
            elif isinstance(value, list):
                element = _unwrap(getattr(target, "subtype", None))
                if isinstance(element, type) and dataclasses.is_dataclass(element):
                    for i, item in enumerate(value):
                        if isinstance(item, dict):
                            self._assert_no_unknown_fields(
                                element, item, f"{path}.{wire}[{i}]")

    def test_detection_has_no_invented_fields(self):
        from spatialdds_idl.spatial.semantics import Detection3D

        det = make_detection(
            det_id="d", class_id="vehicle.car", score=0.5,
            center=(0.0, 0.0, 0.0), size=(1.0, 1.0, 1.0), q=(0.0, 0.0, 0.0, 1.0),
            frame_ref_fqn="scene", timestamp_s=1.0, source_id="op")
        self._assert_no_unknown_fields(Detection3D, det)

    def test_framed_pose_has_no_invented_fields(self):
        from spatialdds_idl.spatial.core import FramedPose

        self._assert_no_unknown_fields(FramedPose, make_framed_pose(
            0.0, 0.0, 0.0, q=(0.0, 0.0, 0.0, 1.0), frame_ref_fqn="scene",
            timestamp_s=1.0))

    def test_announce_has_no_invented_fields(self):
        from spatialdds_idl.spatial.disco import Announce

        self._assert_no_unknown_fields(Announce, make_announce(
            operator="op", service_kind="SENSING",
            topics=[topic_meta("t/v1", "framed_pose", "POSE_RT")],
            coverage=circle_coverage(0.0, 0.0, 10.0), timestamp_s=1.0))
