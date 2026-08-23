#!/usr/bin/env python3
"""Unit tests for the fusion_service adapter layer.

Exercises the pure-Python routing and payload-building path (no DDS, no
threads). Every published payload is built into its announced type, which is
the check that matters: under the envelope a payload only had to be JSON.
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

SELF_DIR = Path(__file__).resolve().parent
if str(SELF_DIR) not in sys.path:
    sys.path.insert(0, str(SELF_DIR))

from fusion import TrackFusion  # noqa: E402
from fusion_service import (  # noqa: E402
    DET3D_TYPE,
    FusionService,
    PLAN_TYPE,
    _parse_detection,
)


class _FakeTransport:
    """
    Stand-in for ``stream.StreamPublisher`` that records publishes in memory.

    It deliberately does *not* build the payload into its type — the tests do
    that explicitly, so a failure names the type that could not be built.
    """

    def __init__(self):
        self.sent = []

    def publish(self, topic, payload):
        self.sent.append((topic, payload))


class ParseDetection(unittest.TestCase):
    def test_parses_full_record(self):
        raw = {
            "center": {"x": 10.0, "y": 20.0, "z": 1.0},
            "velocity": {"x": 1.0, "y": -1.0, "z": 0.0},
            "has_velocity": True,
            "class_id": "vehicle.car",
            "score": 0.9,
        }
        det = _parse_detection(raw, source_operator="operator_a", modality="det3d",
                               default_sigma=0.5)
        self.assertIsNotNone(det)
        self.assertEqual(det.source_operator, "operator_a")
        self.assertEqual(det.object_class, "vehicle.car")
        self.assertAlmostEqual(det.position.x, 10.0)
        self.assertAlmostEqual(det.velocity.vy, -1.0)

    def test_missing_center_returns_none(self):
        det = _parse_detection({"velocity": {"x": 0, "y": 0, "z": 0}},
                               source_operator="op", modality="m", default_sigma=1.0)
        self.assertIsNone(det)

    def test_has_velocity_false_zeros_velocity(self):
        raw = {
            "center": {"x": 0, "y": 0, "z": 0},
            "velocity": {"x": 5.0, "y": 5.0, "z": 5.0},
            "has_velocity": False,
        }
        det = _parse_detection(raw, "op", "m", 1.0)
        self.assertEqual((det.velocity.vx, det.velocity.vy, det.velocity.vz), (0.0, 0.0, 0.0))


class MessageRouting(unittest.TestCase):
    def _make_service(self):
        transport = _FakeTransport()
        svc = FusionService(
            transport=transport, fuser=TrackFusion(confirm_frames=1),
            tick_hz=2.0, default_sigma=0.5, quiet=True,
        )
        return svc, transport

    @staticmethod
    def _det(x, y, cls="vehicle.car", score=0.9):
        return {"detection": {"det_id": "d", "class_id": cls, "score": score,
                              "center": [x, y, 0.0]},
                "has_velocity": True, "velocity": [0.0, 0.0, 0.0],
                "source_modality": "det3d"}

    def test_ignores_a_type_it_does_not_fuse(self):
        """
        Routing is on the announced type, so a vision frame is ignored
        because of what it is — not because its topic name looks wrong.
        """
        svc, _ = self._make_service()
        svc.on_message("video_frame",
                       "spatialdds/operator_a/vision/CAM_FRONT/frame/v1",
                       {"source_operator": "operator_a"}, 0)
        self.assertEqual(svc._fuser.tick(t=0.0), [])

    def test_ignores_payload_without_source_operator(self):
        svc, _ = self._make_service()
        svc.on_message(DET3D_TYPE,
                       "spatialdds/operator_a/sensing/detection3d/v1",
                       {"dets": [self._det(0, 0)]}, 0)
        self.assertEqual(svc._fuser.tick(t=0.0), [])

    def test_detections_are_fed_to_fuser(self):
        svc, _ = self._make_service()
        svc.on_message(DET3D_TYPE,
                       "spatialdds/operator_a/sensing/detection3d/v1",
                       {"source_operator": "operator_a",
                        "dets": [self._det(10, 20)]}, 0)
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertIn("operator_a", tracks[0].source_operators)

    def test_accepts_infrastructure_source(self):
        """The infrastructure radar publishes the same type; nothing special."""
        svc, _ = self._make_service()
        svc.on_message(DET3D_TYPE,
                       "spatialdds/infrastructure/sensing/detection3d/v1",
                       {"source_operator": "infrastructure",
                        "dets": [self._det(5, 5, cls="vehicle", score=0.8)]}, 0)
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].source_operators, ["infrastructure"])

    def test_publish_tracks_emits_a_real_fused_track_set(self):
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.oarc_demo import FusedTrackSet

        svc, transport = self._make_service()
        svc.on_message(DET3D_TYPE,
                       "spatialdds/operator_a/sensing/detection3d/v1",
                       {"source_operator": "operator_a",
                        "dets": [self._det(10, 20)]}, 0)
        svc._publish_tracks(svc._fuser.tick(t=123.5), t=123.5)
        self.assertEqual(len(transport.sent), 1)
        topic, payload = transport.sent[0]
        self.assertEqual(topic, "spatialdds/platform/fusion/track/v1")
        track_set = from_json(FusedTrackSet, payload)
        self.assertEqual(track_set.source_operator, "platform")
        self.assertEqual(len(track_set.tracks), 1)
        self.assertEqual(track_set.tracks[0].source_operators, ["operator_a"])

    def test_publish_coverage_flattens_metrics_into_the_struct(self):
        """
        `metrics` used to be a nested free-form object. FusionCoverage names
        each metric as a field, and the per-operator counts — which were a
        JSON object keyed by operator — become a typed sequence.
        """
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.oarc_demo import FusionCoverage

        svc, transport = self._make_service()
        svc.on_message(DET3D_TYPE,
                       "spatialdds/operator_a/sensing/detection3d/v1",
                       {"source_operator": "operator_a",
                        "dets": [self._det(10, 20)]}, 0)
        svc._publish_coverage(svc._fuser.tick(t=0.0), t=0.0)
        topic, payload = transport.sent[0]
        self.assertEqual(topic, "spatialdds/platform/fusion/coverage/v1")
        coverage = from_json(FusionCoverage, payload)
        self.assertEqual(coverage.track_count, 1)
        self.assertEqual(
            [(r.operator_id, r.track_count) for r in coverage.per_operator_track_count],
            [("operator_a", 1)])

    def test_conflict_event_builds_into_a_spatial_event(self):
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.spatial.events import SpatialEvent

        svc, transport = self._make_service()
        for agent, y in (("op_a_ego", 0.0), ("op_b_ego", 0.5)):
            svc.on_message(PLAN_TYPE, f"spatialdds/x/plan/{agent}/trajectory/v1", {
                "agent_id": agent,
                "waypoints": [{"pose": {"t": [0.0, y, 0.0],
                                        "q": [0.0, 0.0, 0.0, 1.0]},
                               "stamp": {"sec": 100, "nanosec": 0}}],
            }, 0)
        svc._publish_trajectory_conflicts(t=1.0)
        self.assertTrue(transport.sent, "no conflict published")
        topic, payload = transport.sent[0]
        self.assertEqual(topic, "spatialdds/platform/events/trajectory_conflict/v1")
        event = from_json(SpatialEvent, payload)
        self.assertTrue(event.has_measured_distance_m)
        # MetaKV is a JSON string, so nothing rides in `attributes`.
        self.assertEqual(event.attributes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
