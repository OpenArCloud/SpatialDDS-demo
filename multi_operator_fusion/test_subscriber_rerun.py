"""Smoke tests for the Rerun subscriber's sample dispatcher.

Every type the demo can produce flows through ``handle_sample`` without
raising. We use a recording-only Rerun stream (``rr.init`` without spawn /
connect / serve) so the test doesn't pop up a viewer.

The payloads here are not hand-written shapes: each is built with the same
helper its publisher uses and then round-tripped through its IDL type, so a
payload that could not go on the wire cannot be tested against either. That
is what the old version of this file could not do — it asserted the
dispatcher survived shapes that no publisher would ever emit.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import rerun as rr  # noqa: E402

from spatialdds_demo.json_mapping import from_json, to_json  # noqa: E402
from spatialdds_idl.oarc_demo import (  # noqa: E402
    FusedTrackSet, FusionCoverage, OperatorDetectionSet,
)
from spatialdds_idl.spatial.core import (  # noqa: E402
    EntityBinding, FramedPose, PlannedTrajectory,
)
from spatialdds_idl.spatial.disco import Announce  # noqa: E402
from spatialdds_idl.spatial.events import SpatialEvent  # noqa: E402
from spatialdds_types import (  # noqa: E402
    circle_coverage, make_announce, make_detection, make_detection_set,
    make_detection_with_velocity, make_entity_binding, make_framed_pose,
    make_fusion_coverage, make_planned_trajectory, make_planned_waypoint,
    make_trajectory_conflict_event, make_component_ref, topic_meta,
)
from subscriber_rerun import (  # noqa: E402
    ANNOUNCE_TYPE, RerunMultiOpSubscriber, handle_sample,
)

SCENE = "scene/intersection"


def _wire(cls, payload):
    """The payload as it would arrive: built into its type, then serialised."""
    return to_json(from_json(cls, payload))


def _ego_payload(operator: str, x: float = 0.0, y: float = 0.0):
    return _wire(FramedPose, make_framed_pose(
        x, y, 0.0, q=(0.0, 0.0, 0.0, 1.0),
        frame_ref_fqn=f"{operator}/map", timestamp_s=1.0))


def _det_payload(operator: str):
    det = make_detection(
        det_id="obj_00", class_id="vehicle.car", score=0.85,
        center=(1.0, 2.0, 0.5), size=(4.5, 1.8, 1.6), q=(0.0, 0.0, 0.0, 1.0),
        frame_ref_fqn=SCENE, timestamp_s=1.0, source_id=operator,
    )
    return _wire(OperatorDetectionSet, make_detection_set(
        set_id="set-1", source_operator=operator, frame_ref_fqn=SCENE,
        dets=[make_detection_with_velocity(det, velocity=(1.0, 0.0, 0.0),
                                           source_modality="det3d")],
        frame_seq=1, timestamp_s=1.0))


def _fused_tracks_payload():
    from fusion import FusedTrack, Position, Velocity
    from spatialdds_types import make_fused_track_set

    track = FusedTrack(
        track_id="fused-1", position=Position(0.0, 0.0, 0.0),
        velocity=Velocity(0.0, 0.0, 0.0), position_uncertainty=0.3,
        object_class="vehicle.car", confidence=0.9,
        source_operators=["operator_a", "operator_b"],
        source_modalities=["det3d"], source_count=2,
        timestamp=1.0, track_age=2.0,
    )
    return _wire(FusedTrackSet, make_fused_track_set([track], timestamp_s=1.0))


def _plan_payload(agent: str = "operator_a_ego"):
    waypoints = [
        make_planned_waypoint(float(i), 0.0, 0.0, timestamp_s=float(i),
                              uncertainty_m=0.5 + 0.3 * i)
        for i in range(5)
    ]
    return _wire(PlannedTrajectory, make_planned_trajectory(
        agent, "plan-1", 0, SCENE, waypoints=waypoints,
        horizon_sec=5.0, replan_rate_hz=2.0, timestamp_s=1.0))


def _conflict_payload():
    return _wire(SpatialEvent, make_trajectory_conflict_event(
        {"agents": ["operator_a_ego", "operator_b_ego"],
         "min_distance_m": 1.4, "time_to_conflict": 2.5,
         "conflict_position": {"x": 0.0, "y": 0.0, "z": 0.0}},
        timestamp_s=1.0, frame_ref_fqn=SCENE))


def _binding_payload():
    return _wire(EntityBinding, make_entity_binding(
        "entity_fused-1", "vehicle.car",
        [make_component_ref("spatialdds/platform/fusion/track/v1", "fused-1"),
         make_component_ref("spatialdds/operator_a/sensing/detection3d/v1",
                            "obj_00")],
        position=(0.0, 0.0, 0.0), frame_ref_fqn=SCENE, timestamp_s=1.0))


def _announce_payload(operator: str):
    return _wire(Announce, make_announce(
        operator=operator, service_kind="SENSING",
        topics=[topic_meta(f"spatialdds/{operator}/ego/pose/v1",
                           "oarc.framed_pose", "POSE_RT")],
        coverage=circle_coverage(0.0, 0.0, 50.0), timestamp_s=1.0))


def _coverage_payload():
    return _wire(FusionCoverage, make_fusion_coverage(
        {"track_count": 5, "multi_source_count": 2, "multi_source_pct": 0.4,
         "best_single_operator_count": 3, "coverage_improvement": 1.6,
         "best_av_operator_count": 3, "coverage_improvement_excl_infra": 1.6,
         "per_operator_track_count": {"operator_a": 3, "operator_b": 2}},
        timestamp_s=1.0))


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
        handle_sample(self.sub, msg_type, topic, operator, payload,
                      frame_num=1)

    def test_module_imports(self):
        import subscriber_rerun  # noqa: F401

    def test_ego_pose(self):
        self._drive("oarc.framed_pose",
                     "spatialdds/operator_a/ego/pose/v1",
                     _ego_payload("operator_a"))

    def test_detection3d_set(self):
        self._drive("oarc.detection3d_velocity",
                     "spatialdds/operator_a/sensing/detection3d/v1",
                     _det_payload("operator_a"))

    def test_infrastructure_detection_set(self):
        self._drive("oarc.detection3d_velocity",
                     "spatialdds/infrastructure/sensing/detection3d/v1",
                     _det_payload("infrastructure"),
                     operator="infrastructure")

    def test_fused_tracks(self):
        self._drive("oarc.fused_track",
                     "spatialdds/platform/fusion/track/v1",
                     _fused_tracks_payload(),
                     operator="platform")

    def test_planned_trajectory(self):
        self._drive("planned_trajectory",
                     "spatialdds/operator_a/plan/operator_a_ego/trajectory/v1",
                     _plan_payload())

    def test_spatial_event_conflict(self):
        self._drive("spatial_event",
                     "spatialdds/platform/events/trajectory_conflict/v1",
                     _conflict_payload(),
                     operator="platform")

    def test_spatial_event_unknown_kind_does_not_raise(self):
        payload = dict(_conflict_payload())
        payload["type"] = "ANOMALY"
        self._drive("spatial_event",
                    "spatialdds/platform/events/whatever/v1",
                    payload, operator="platform")

    def test_entity_binding(self):
        self._drive("entity_binding",
                     "spatialdds/platform/entity/binding/v1",
                     _binding_payload(),
                     operator="platform")

    def test_announce(self):
        self._drive(ANNOUNCE_TYPE,
                     "spatialdds/operator_a/discovery/announce/v1",
                     _announce_payload("operator_a"))

    def test_coverage_metrics(self):
        self._drive("oarc.fusion_coverage",
                     "spatialdds/platform/fusion/coverage/v1",
                     _coverage_payload(),
                     operator="platform")

    def test_every_announced_type_the_demo_publishes_has_a_handler(self):
        """
        The dispatcher used to carry three spellings of "a detection set"
        because msg_type was a demo-private label each publisher chose for
        itself. There is now one name per type — the registry's — so the
        check that matters is coverage of what the demo actually announces.
        """
        from subscriber_rerun import _HANDLERS

        for type_name in ("oarc.framed_pose", "oarc.detection3d_velocity",
                          "planned_trajectory", "oarc.fused_track",
                          "oarc.fusion_coverage", "spatial_event",
                          "entity_binding"):
            with self.subTest(type=type_name):
                self.assertIn(type_name, _HANDLERS)

    def test_unknown_type_is_silent(self):
        # 3.3.2 treats unregistered type names as extension points, so an
        # unknown one is skipped rather than fatal.
        self._drive("some.future.type",
                     "spatialdds/some_op/future/v1",
                     {"foo": "bar"})

    def test_ego_trail_accumulates_and_bounds(self):
        # Sanity check on the trail buffer cap.
        self.sub._trail_max = 5
        for i in range(20):
            self._drive("oarc.framed_pose",
                         "spatialdds/operator_a/ego/pose/v1",
                         _ego_payload("operator_a", x=float(i), y=0.0))
        self.assertLessEqual(len(self.sub._trails["operator_a"]), 5)


if __name__ == "__main__":
    unittest.main()
