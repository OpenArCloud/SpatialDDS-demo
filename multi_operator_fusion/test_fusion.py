#!/usr/bin/env python3
"""Unit tests for the cross-operator track fusion algorithm.

Covers:
  * Single-detection lifecycle (tentative -> confirmed -> lost).
  * Cross-operator merging of concurrent observations.
  * Provenance accumulation across modalities.
  * Confidence boost via independent confirmation.
  * Uncertainty-weighted position fusion.
  * Gating (distance + velocity) rejects spurious associations.
  * Coverage metrics reflect multi-source confirmation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SELF_DIR = Path(__file__).resolve().parent
if str(SELF_DIR) not in sys.path:
    sys.path.insert(0, str(SELF_DIR))

from fusion import (  # noqa: E402
    Detection3D,
    Position,
    TrackFusion,
    Velocity,
    coverage_metrics,
)


def _det(x, y, z, op="operator_a", mod="lidar", cls="car", conf=0.8, sigma=1.0,
         vx=0.0, vy=0.0, vz=0.0) -> Detection3D:
    return Detection3D(
        position=Position(x=x, y=y, z=z),
        velocity=Velocity(vx=vx, vy=vy, vz=vz),
        source_operator=op, source_modality=mod,
        object_class=cls, confidence=conf, position_uncertainty=sigma,
    )


class Lifecycle(unittest.TestCase):
    def test_tentative_not_emitted_on_first_frame(self):
        f = TrackFusion(confirm_frames=2)
        f.on_detection(_det(10, 0, 0))
        out = f.tick(t=0.0)
        self.assertEqual(out, [])

    def test_confirms_after_n_frames(self):
        f = TrackFusion(confirm_frames=2)
        f.on_detection(_det(10, 0, 0)); f.tick(t=0.0)
        f.on_detection(_det(10, 0, 0)); out = f.tick(t=0.5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source_count, 1)

    def test_track_drops_after_lost_frames(self):
        f = TrackFusion(confirm_frames=1, lost_frames=3)
        f.on_detection(_det(10, 0, 0)); f.tick(t=0.0)  # confirmed
        for i in range(3):
            f.tick(t=1.0 + i)  # no detection
        self.assertEqual(f.tick(t=5.0), [])


class CrossOperatorMerge(unittest.TestCase):
    def test_two_operators_same_tick_merge_into_one_track(self):
        f = TrackFusion(confirm_frames=1)
        f.on_detection(_det(100, 200, 0, op="operator_a", mod="lidar"))
        f.on_detection(_det(101, 199, 0, op="operator_b", mod="camera"))
        out = f.tick(t=0.0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source_count, 2)
        self.assertIn("operator_a", out[0].source_operators)
        self.assertIn("operator_b", out[0].source_operators)

    def test_provenance_accumulates_across_ticks(self):
        f = TrackFusion(confirm_frames=1)
        f.on_detection(_det(50, 50, 0, op="operator_a", mod="lidar"))
        f.tick(t=0.0)
        f.on_detection(_det(50, 50, 0, op="operator_a", mod="lidar"))
        f.on_detection(_det(50.5, 50.2, 0, op="infrastructure", mod="radar"))
        out = f.tick(t=0.5)
        t = out[0]
        self.assertEqual(sorted(t.source_operators), ["infrastructure", "operator_a"])
        self.assertEqual(sorted(t.source_modalities), ["lidar", "radar"])

    def test_two_operators_beyond_gate_stay_separate(self):
        f = TrackFusion(confirm_frames=1, gate_distance_m=5.0)
        f.on_detection(_det(0, 0, 0, op="operator_a"))
        f.on_detection(_det(50, 0, 0, op="operator_b"))  # 50m away, outside gate
        out = f.tick(t=0.0)
        self.assertEqual(len(out), 2)

    def test_velocity_gate_rejects_crossing_objects(self):
        f = TrackFusion(confirm_frames=1, gate_distance_m=5.0, gate_velocity_mps=2.0)
        f.on_detection(_det(0, 0, 0, op="operator_a", vx=10.0))
        f.on_detection(_det(1, 0, 0, op="operator_b", vx=-10.0))
        out = f.tick(t=0.0)
        self.assertEqual(len(out), 2, "Opposing velocities must not fuse")


class ConfidenceAndUncertainty(unittest.TestCase):
    def test_confidence_boost_exceeds_any_single_source(self):
        f = TrackFusion(confirm_frames=1)
        f.on_detection(_det(0, 0, 0, op="operator_a", conf=0.5))
        f.on_detection(_det(0, 0, 0, op="operator_b", conf=0.6))
        out = f.tick(t=0.0)
        self.assertGreater(out[0].confidence, 0.6)

    def test_position_weighted_toward_lower_uncertainty(self):
        f = TrackFusion(confirm_frames=1)
        # Both within gate; low-sigma at x=0 should dominate the high-sigma at x=3
        f.on_detection(_det(3, 0, 0, op="operator_a", sigma=10.0))
        f.on_detection(_det(0, 0, 0, op="operator_b", sigma=0.1))
        out = f.tick(t=0.0)
        self.assertEqual(len(out), 1, "Observations should merge within gate")
        self.assertLess(abs(out[0].position.x), 0.5,
                        f"Fused x={out[0].position.x} should be ~0 (low-sigma dominates)")

    def test_fused_uncertainty_is_reduced(self):
        f = TrackFusion(confirm_frames=1)
        f.on_detection(_det(0, 0, 0, sigma=2.0, op="operator_a"))
        f.on_detection(_det(0, 0, 0, sigma=2.0, op="operator_b"))
        out = f.tick(t=0.0)
        # Two equal 1-sigma=2 observations -> fused sigma = 2/sqrt(2) ~= 1.41
        self.assertLess(out[0].position_uncertainty, 2.0)

    def test_prefers_known_class_over_unknown(self):
        f = TrackFusion(confirm_frames=1)
        f.on_detection(_det(0, 0, 0, op="operator_a", cls="unknown"))
        f.on_detection(_det(0, 0, 0, op="operator_b", cls="pedestrian"))
        out = f.tick(t=0.0)
        self.assertEqual(out[0].object_class, "pedestrian")


class CoverageMetrics(unittest.TestCase):
    def test_multi_source_pct_one_shared_one_solo(self):
        f = TrackFusion(confirm_frames=1)
        # Track 1: shared by A+B
        f.on_detection(_det(0, 0, 0, op="operator_a"))
        f.on_detection(_det(0, 0, 0, op="operator_b"))
        # Track 2: solo A, far enough to stay separate
        f.on_detection(_det(100, 100, 0, op="operator_a"))
        out = f.tick(t=0.0)
        m = coverage_metrics(out)
        self.assertEqual(m["track_count"], 2)
        self.assertEqual(m["multi_source_count"], 1)
        self.assertAlmostEqual(m["multi_source_pct"], 0.5)
        # operator_a appears in both tracks (2), operator_b in one (1)
        self.assertEqual(m["best_single_operator_count"], 2)
        self.assertAlmostEqual(m["coverage_improvement"], 1.0)

    def test_empty_metrics_are_zero_not_nan(self):
        m = coverage_metrics([])
        self.assertEqual(m["track_count"], 0)
        self.assertEqual(m["multi_source_pct"], 0.0)
        self.assertEqual(m["coverage_improvement"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
