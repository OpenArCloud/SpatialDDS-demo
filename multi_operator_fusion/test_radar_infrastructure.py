"""Unit tests for the synthetic publisher's radar-plausible infrastructure
detection model.

No DDS, no transport — drives the helpers directly with a seeded RNG and
asserts the detection probability, noise envelope, and false-alarm count
behave the way the fusion pipeline expects.
"""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
# bridges/ has to be importable for synthetic_publisher's top-level
# ``from envelope_io import EnvelopePublisher`` — but that import is only
# reached if cyclonedds is installed. Tier-1 unit tests run on hosts
# without DDS, so we shim the import.
import types as _types  # noqa: E402

if "envelope_io" not in sys.modules:
    stub = _types.ModuleType("envelope_io")
    class _StubPublisher:  # noqa: D401
        def __init__(self, *_a, **_kw): ...
        def publish(self, *_a, **_kw): ...
        def close(self): ...
    stub.EnvelopePublisher = _StubPublisher  # type: ignore[attr-defined]
    sys.modules["envelope_io"] = stub

from synthetic_publisher import (  # noqa: E402
    INFRA_BS_POSITION,
    _build_infra_set,
    _radar_observe,
)


class TestRadarObserve(unittest.TestCase):

    def test_close_target_almost_always_detected(self):
        """A target 10 m away has SNR ~40 dB and P_d ~ 1.0; over 200
        trials we expect every one to detect (probability of ≥ 1 miss
        < 1e-12)."""
        rng = random.Random(0)
        target = (INFRA_BS_POSITION["x"] + 10.0,
                  INFRA_BS_POSITION["y"], 0.0)
        misses = sum(1 for _ in range(200)
                     if _radar_observe(target, INFRA_BS_POSITION, rng) is None)
        self.assertEqual(misses, 0)

    def test_far_target_almost_always_missed(self):
        """At 5000 m the SNR ≈ −14 dB and P_d ≈ 6e−6 — over 200 trials
        the expected hit count is far below 1, so a hit-count > 1 is a
        meaningful regression."""
        rng = random.Random(0)
        target = (INFRA_BS_POSITION["x"] + 5000.0,
                  INFRA_BS_POSITION["y"], 0.0)
        hits = sum(1 for _ in range(200)
                   if _radar_observe(target, INFRA_BS_POSITION, rng) is not None)
        self.assertLessEqual(hits, 1)

    def test_noise_grows_with_range(self):
        """Range/angle noise sigma scales with range. Take many
        observations of a far-but-detectable target and confirm spread
        is larger than for a close one."""
        # Force detection by patching: pick a range where P_d ≈ 1
        close = (INFRA_BS_POSITION["x"] + 30.0, INFRA_BS_POSITION["y"], 0.0)
        far = (INFRA_BS_POSITION["x"] + 200.0, INFRA_BS_POSITION["y"], 0.0)

        def _spread(target, n=300):
            rng = random.Random(7)
            xs = []
            for _ in range(n):
                obs = _radar_observe(target, INFRA_BS_POSITION, rng)
                if obs is not None:
                    xs.append(obs["x"])
            mu = sum(xs) / len(xs)
            return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5

        self.assertGreater(_spread(far), _spread(close))

    def test_observation_carries_metadata(self):
        rng = random.Random(0)
        target = (INFRA_BS_POSITION["x"] + 50.0, INFRA_BS_POSITION["y"], 1.0)
        obs = _radar_observe(target, INFRA_BS_POSITION, rng)
        self.assertIsNotNone(obs)
        for key in ("x", "y", "z", "range_m", "snr_db", "p_detect"):
            self.assertIn(key, obs)
        self.assertGreater(obs["p_detect"], 0.5)


class TestInfraSet(unittest.TestCase):

    def test_payload_shape(self):
        rng = random.Random(0)
        s = _build_infra_set(t=1.0, frame_seq=10, n_operators=3,
                              n_objects_per_operator=5,
                              bs=INFRA_BS_POSITION, rng=rng)
        self.assertEqual(s["source_operator"], "infrastructure")
        self.assertEqual(s["frame_seq"], 10)
        self.assertEqual(s["stamp"], {"sec": 1, "nanosec": 0})
        self.assertIn("detections", s)
        for det in s["detections"]:
            self.assertIn("det_id", det)
            self.assertIn("center", det)
            self.assertIn("score", det)

    def test_false_alarms_have_low_score(self):
        """False alarms (det_id starts with infra_fa_) should land in
        the low-confidence band so the fusion service can filter them
        out via its confirm-frames logic."""
        rng = random.Random(1)
        det_count = 0
        for _ in range(50):
            s = _build_infra_set(t=2.0, frame_seq=1, n_operators=3,
                                  n_objects_per_operator=5,
                                  bs=INFRA_BS_POSITION, rng=rng)
            for det in s["detections"]:
                if det["det_id"].startswith("infra_fa_"):
                    det_count += 1
                    self.assertLess(det["score"], 0.4)
        self.assertGreater(det_count, 5)  # at least some FAs over 50 frames

    def test_false_alarm_count_bounded(self):
        """Per-frame false alarm count is in [0, 2]."""
        rng = random.Random(2)
        for f in range(40):
            s = _build_infra_set(t=float(f), frame_seq=f, n_operators=1,
                                  n_objects_per_operator=1,
                                  bs=INFRA_BS_POSITION, rng=rng)
            fa = [d for d in s["detections"] if d["det_id"].startswith("infra_fa_")]
            self.assertLessEqual(len(fa), 2)

    def test_seed_makes_run_deterministic(self):
        rng_a = random.Random(99)
        rng_b = random.Random(99)
        s_a = _build_infra_set(1.0, 0, 3, 5, INFRA_BS_POSITION, rng_a)
        s_b = _build_infra_set(1.0, 0, 3, 5, INFRA_BS_POSITION, rng_b)
        self.assertEqual(s_a, s_b)


if __name__ == "__main__":
    unittest.main()
