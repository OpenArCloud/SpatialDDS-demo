#!/usr/bin/env python3
"""Unit tests for the fusion_service adapter layer.

Exercises the pure-Python envelope-handling path (no DDS, no threads).
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
    FusionService,
    _parse_detection,
)


def _envelope(msg_type: str, logical_topic: str, payload: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        msg_type=msg_type,
        logical_topic=logical_topic,
        payload_json=json.dumps(payload),
        stamp_ns=0,
        request_id="",
    )


class _FakeTransport:
    """Stand-in for EnvelopeTransport that records publishes in memory."""

    def __init__(self):
        self.sent = []

    def publish(self, logical_topic, msg_type, payload_json, request_id=""):
        self.sent.append((logical_topic, msg_type, json.loads(payload_json)))


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


class EnvelopeDispatch(unittest.TestCase):
    def _make_service(self):
        transport = _FakeTransport()
        svc = FusionService(
            transport=transport, fuser=TrackFusion(confirm_frames=1),
            tick_hz=2.0, default_sigma=0.5, quiet=True,
        )
        return svc, transport

    def test_ignores_wrong_topic_suffix(self):
        svc, _ = self._make_service()
        env = _envelope("NUSC_VISION_FRAME",
                        "spatialdds/operator_a/vision/CAM_FRONT/frame/v1",
                        {"source_operator": "operator_a"})
        svc.on_envelope(env)
        self.assertEqual(svc._fuser.tick(t=0.0), [])

    def test_ignores_payload_without_source_operator(self):
        svc, _ = self._make_service()
        env = _envelope("NUSC_DET3D_SET",
                        "spatialdds/operator_a/sensing/detection3d/v1",
                        {"detections": [{"center": {"x": 0, "y": 0, "z": 0}, "score": 1.0}]})
        svc.on_envelope(env)
        self.assertEqual(svc._fuser.tick(t=0.0), [])

    def test_detections_are_fed_to_fuser(self):
        svc, _ = self._make_service()
        env = _envelope("NUSC_DET3D_SET",
                        "spatialdds/operator_a/sensing/detection3d/v1",
                        {"source_operator": "operator_a",
                         "detections": [
                             {"center": {"x": 10, "y": 20, "z": 0},
                              "velocity": {"x": 0, "y": 0, "z": 0},
                              "has_velocity": True,
                              "class_id": "vehicle.car", "score": 0.9},
                         ]})
        svc.on_envelope(env)
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertIn("operator_a", tracks[0].source_operators)

    def test_accepts_infrastructure_topic(self):
        """Any source publishing to a */sensing/detection3d/v1 topic should be accepted."""
        svc, _ = self._make_service()
        env = _envelope("INFRA_DET3D_SET",
                        "spatialdds/infrastructure/sensing/detection3d/v1",
                        {"source_operator": "infrastructure",
                         "detections": [
                             {"center": {"x": 5, "y": 5, "z": 0},
                              "velocity": {"x": 0, "y": 0, "z": 0},
                              "has_velocity": True,
                              "class_id": "vehicle", "score": 0.8},
                         ]})
        svc.on_envelope(env)
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].source_operators, ["infrastructure"])

    def test_publish_tracks_emits_on_platform_topic(self):
        svc, transport = self._make_service()
        svc._publish_tracks([], t=123.5)
        self.assertEqual(len(transport.sent), 1)
        topic, msg_type, payload = transport.sent[0]
        self.assertEqual(topic, "spatialdds/platform/fusion/track/v1")
        self.assertEqual(msg_type, "NUSC_FUSED_TRACK_SET")
        self.assertEqual(payload["source_operator"], "platform")
        self.assertEqual(payload["tracks"], [])

    def test_publish_coverage_includes_metrics(self):
        svc, transport = self._make_service()
        svc._publish_coverage([], t=0.0)
        topic, msg_type, payload = transport.sent[0]
        self.assertEqual(topic, "spatialdds/platform/fusion/coverage/v1")
        self.assertEqual(msg_type, "NUSC_FUSION_COVERAGE")
        self.assertIn("metrics", payload)
        self.assertIn("multi_source_pct", payload["metrics"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
