#!/usr/bin/env python3
"""In-process end-to-end test: envelope shapes from publishers round-trip
correctly through the fusion service.

No DDS, no data files — synthesizes the exact JSON payloads each
publisher emits and drives them through ``FusionService.on_envelope``,
then asserts on the fuser's output and the ``NUSC_FUSED_TRACK_SET`` /
``NUSC_FUSION_COVERAGE`` payloads on the platform topics.

Catches contract drift between publisher (operator/infra) and the
fusion parser — the kind of bug that only surfaces at wire time.
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
from fusion_service import COVERAGE_TOPIC, FusionService, TRACK_TOPIC  # noqa: E402
from infrastructure_publisher import (  # noqa: E402
    _apply_offset,
    _stamp_from_index,
    make_detection3d_payload,
)


def _envelope(msg_type: str, topic: str, payload: dict):
    return types.SimpleNamespace(
        msg_type=msg_type, logical_topic=topic,
        payload_json=json.dumps(payload), stamp_ns=0, request_id="",
    )


class _FakeTransport:
    """Mirrors bridges/envelope_io.EnvelopePublisher.publish (payload is a dict)."""

    def __init__(self):
        self.sent = []

    def publish(self, logical_topic, msg_type, payload, request_id="", stamp_ns=None):
        self.sent.append((logical_topic, msg_type, payload))


def _operator_det3d_envelope(operator: str, x: float, y: float, z: float = 0.0,
                             vx: float = 0.0, vy: float = 0.0,
                             cls: str = "vehicle.car", score: float = 0.9):
    """Shape matches what multi_operator_fusion/publisher.py emits."""
    payload = {
        "frame_seq": 1,
        "stamp": {"sec": 0, "nanosec": 0},
        "source_operator": operator,
        "detections": [{
            "det_id": f"{operator}-1",
            "center": {"x": x, "y": y, "z": z},
            "size": {"x": 2.0, "y": 1.6, "z": 4.5},
            "q": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "class_id": cls, "score": score,
            "has_velocity": True,
            "velocity": {"x": vx, "y": vy, "z": 0.0},
        }],
    }
    return _envelope("NUSC_DET3D_SET",
                     f"spatialdds/{operator}/sensing/detection3d/v1", payload)


def _infra_det3d_envelope(x: float, y: float, z: float = 0.0):
    """Shape matches infrastructure_publisher.make_detection3d_payload."""
    payload = make_detection3d_payload(
        frame_seq=1, stamp=_stamp_from_index(1),
        east=x, north=y, up=z, velocity=(0.0, 0.0, 0.0),
    )
    return _envelope("INFRA_DET3D_SET",
                     "spatialdds/infrastructure/sensing/detection3d/v1", payload)


class RoundTrip(unittest.TestCase):
    def _make(self, confirm_frames=1):
        transport = _FakeTransport()
        svc = FusionService(
            transport=transport, fuser=TrackFusion(confirm_frames=confirm_frames),
            tick_hz=2.0, default_sigma=0.5, quiet=True,
        )
        return svc, transport

    def test_three_operators_at_distinct_positions_yield_three_tracks(self):
        svc, transport = self._make()
        svc.on_envelope(_operator_det3d_envelope("operator_a", x=0, y=-60))
        svc.on_envelope(_operator_det3d_envelope("operator_b", x=60, y=0))
        svc.on_envelope(_operator_det3d_envelope("operator_c", x=-60, y=0))
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 3)
        for t in tracks:
            self.assertEqual(t.source_count, 1)

    def test_co_located_sources_merge_into_single_multi_source_track(self):
        svc, _ = self._make()
        # Operator A, Operator B, and infrastructure all report the same object
        # near (10, 5) — the fuser should produce exactly one 3-source track.
        svc.on_envelope(_operator_det3d_envelope("operator_a", x=10.0, y=5.0))
        svc.on_envelope(_operator_det3d_envelope("operator_b", x=10.2, y=5.1))
        svc.on_envelope(_infra_det3d_envelope(x=9.8, y=4.9))
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].source_count, 3)
        self.assertEqual(
            sorted(tracks[0].source_operators),
            ["infrastructure", "operator_a", "operator_b"],
        )

    def test_platform_topics_receive_fused_tracks_and_coverage(self):
        svc, transport = self._make()
        svc.on_envelope(_operator_det3d_envelope("operator_a", x=0, y=0))
        svc.on_envelope(_infra_det3d_envelope(x=0.3, y=0.1))
        tracks = svc._fuser.tick(t=1.0)
        svc._publish_tracks(tracks, t=1.0)
        svc._publish_coverage(tracks, t=1.0)

        topics = {topic for topic, _, _ in transport.sent}
        self.assertIn(TRACK_TOPIC, topics)
        self.assertIn(COVERAGE_TOPIC, topics)

        track_payload = next(p for topic, _, p in transport.sent if topic == TRACK_TOPIC)
        self.assertEqual(len(track_payload["tracks"]), 1)
        self.assertEqual(track_payload["tracks"][0]["source_count"], 2)

        cov_payload = next(p for topic, _, p in transport.sent if topic == COVERAGE_TOPIC)
        self.assertEqual(cov_payload["metrics"]["track_count"], 1)
        self.assertEqual(cov_payload["metrics"]["multi_source_count"], 1)
        self.assertAlmostEqual(cov_payload["metrics"]["multi_source_pct"], 1.0)

    def test_published_offset_preserved_end_to_end(self):
        """Offset baked into a publisher's Detection3D survives the round trip."""
        svc, _ = self._make()
        # Simulate operator_b applying a (+60, 0, 0) offset to a detection at origin
        env = _operator_det3d_envelope("operator_b", x=60.0, y=0.0)
        svc.on_envelope(env)
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertAlmostEqual(tracks[0].position.x, 60.0)
        self.assertAlmostEqual(tracks[0].position.y, 0.0)

    def test_infra_offset_applied_before_publish(self):
        """The launcher's infra offset is baked in by _apply_offset — verify
        the offset Detection3D still parses and ends up in the fused track."""
        svc, _ = self._make()
        payload = make_detection3d_payload(
            frame_seq=1, stamp=_stamp_from_index(1),
            east=0.0, north=0.0, up=0.0, velocity=(0.0, 0.0, 0.0),
        )
        _apply_offset(payload["detections"][0], (-30.0, 30.0, 0.0))
        env = _envelope("INFRA_DET3D_SET",
                        "spatialdds/infrastructure/sensing/detection3d/v1", payload)
        svc.on_envelope(env)
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertAlmostEqual(tracks[0].position.x, -30.0)
        self.assertAlmostEqual(tracks[0].position.y, 30.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
