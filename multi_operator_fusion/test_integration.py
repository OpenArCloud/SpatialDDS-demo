#!/usr/bin/env python3
"""In-process end-to-end test: publisher payloads round-trip through fusion.

No DDS, no data files — builds payloads with the *same* helpers the
publishers use, routes them through ``FusionService.on_message``, and asserts
both on the fuser's output and on the platform payloads, each of which is
built into its announced type.

Using the real builders rather than a hand-copied JSON shape is the point:
the old version of this test restated the payload shape by hand, so it went
on passing while the publishers and the parser drifted apart. Now a drift in
either one fails here.
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
    COVERAGE_TOPIC, DET3D_TYPE, FusionService, TRACK_TOPIC,
)
from infrastructure_publisher import (  # noqa: E402
    _apply_offset,
    _stamp_from_index,
    make_detection3d_payload,
)
from spatialdds_demo.json_mapping import from_json
from spatialdds_idl.spatial.semantics import (  # noqa: E402
    Detection3DSet, FusedTrackSet,
)
from spatialdds_idl.oarc_demo import FusionCoverage  # noqa: E402
from spatialdds_types import (  # noqa: E402
    make_detection, make_detection_set,
)

SCENE = "scene/intersection"


class _FakeTransport:
    """Mirrors ``stream.StreamPublisher.publish``."""

    def __init__(self):
        self.sent = []

    def publish(self, topic, payload):
        self.sent.append((topic, payload))


def _operator_det3d(operator: str, x: float, y: float, z: float = 0.0,
                    vx: float = 0.0, vy: float = 0.0,
                    cls: str = "vehicle.car", score: float = 0.9) -> dict:
    """Built exactly as the operator publishers build it."""
    det = make_detection(
        det_id=f"{operator}-1", class_id=cls, score=score,
        center=(x, y, z), size=(2.0, 1.6, 4.5), q=(0.0, 0.0, 0.0, 1.0),
        frame_ref_fqn=SCENE, timestamp_s=0.0, source_id=operator,
    )
    return make_detection_set(
        set_id=f"{operator}-1", source_operator=operator, frame_ref_fqn=SCENE,
        dets=[det],
        frame_seq=1, timestamp_s=0.0,
    )


def _infra_det3d(x: float, y: float, z: float = 0.0) -> dict:
    return make_detection3d_payload(
        frame_seq=1, stamp=_stamp_from_index(1),
        east=x, north=y, up=z, velocity=(0.0, 0.0, 0.0),
    )


def _feed(svc, operator: str, payload: dict) -> None:
    """
    Route one detection set in, having first proved it is a real sample.

    Building it here is what makes this an end-to-end contract test: if a
    publisher helper drifts out of the IDL, `from_json` raises before the
    fuser is ever reached.
    """
    from_json(Detection3DSet, payload)
    svc.on_message(DET3D_TYPE,
                   f"spatialdds/{operator}/sensing/detection3d/v1", payload, 0)


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
        _feed(svc, "operator_a", _operator_det3d("operator_a", x=0, y=-60))
        _feed(svc, "operator_b", _operator_det3d("operator_b", x=60, y=0))
        _feed(svc, "operator_c", _operator_det3d("operator_c", x=-60, y=0))
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 3)
        for t in tracks:
            self.assertEqual(t.source_count, 1)

    def test_co_located_sources_merge_into_single_multi_source_track(self):
        svc, _ = self._make()
        # Operator A, Operator B, and infrastructure all report the same object
        # near (10, 5) — the fuser should produce exactly one 3-source track.
        _feed(svc, "operator_a", _operator_det3d("operator_a", x=10.0, y=5.0))
        _feed(svc, "operator_b", _operator_det3d("operator_b", x=10.2, y=5.1))
        _feed(svc, "infrastructure", _infra_det3d(x=9.8, y=4.9))
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].source_count, 3)
        self.assertEqual(
            sorted(tracks[0].source_operators),
            ["infrastructure", "operator_a", "operator_b"],
        )

    def test_platform_topics_receive_fused_tracks_and_coverage(self):
        svc, transport = self._make()
        _feed(svc, "operator_a", _operator_det3d("operator_a", x=0, y=0))
        _feed(svc, "infrastructure", _infra_det3d(x=0.3, y=0.1))
        tracks = svc._fuser.tick(t=1.0)
        svc._publish_tracks(tracks, t=1.0)
        svc._publish_coverage(tracks, t=1.0)

        topics = {topic for topic, _ in transport.sent}
        self.assertIn(TRACK_TOPIC, topics)
        self.assertIn(COVERAGE_TOPIC, topics)

        track_set = from_json(FusedTrackSet, next(
            p for topic, p in transport.sent if topic == TRACK_TOPIC))
        self.assertEqual(len(track_set.tracks), 1)
        self.assertEqual(track_set.tracks[0].source_count, 2)

        coverage = from_json(FusionCoverage, next(
            p for topic, p in transport.sent if topic == COVERAGE_TOPIC))
        self.assertEqual(coverage.track_count, 1)
        self.assertEqual(coverage.multi_source_count, 1)
        self.assertAlmostEqual(coverage.multi_source_pct, 1.0)

    def test_published_offset_preserved_end_to_end(self):
        """Offset baked into a publisher's Detection3D survives the round trip."""
        svc, _ = self._make()
        # Simulate operator_b applying a (+60, 0, 0) offset to a detection at origin
        _feed(svc, "operator_b", _operator_det3d("operator_b", x=60.0, y=0.0))
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
        _apply_offset(payload["dets"][0], (-30.0, 30.0, 0.0))
        _feed(svc, "infrastructure", payload)
        tracks = svc._fuser.tick(t=0.0)
        self.assertEqual(len(tracks), 1)
        self.assertAlmostEqual(tracks[0].position.x, -30.0)
        self.assertAlmostEqual(tracks[0].position.y, 30.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
