"""Unit tests for topic_router. No FastAPI, no DDS."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from topic_router import Subscription, TopicRouter  # noqa: E402


class TestGlobMatching(unittest.TestCase):
    def setUp(self):
        self.r = TopicRouter()

    def _sub(self, pattern, msg_types=None, max_rate_hz=None):
        return Subscription(id="s", pattern=pattern, msg_types=msg_types,
                            max_rate_hz=max_rate_hz)

    def test_exact_match(self):
        sub = self._sub("spatialdds/op_a/ego/pose/v1")
        self.assertTrue(self.r.match("spatialdds/op_a/ego/pose/v1", "FramedPose", sub, 0))
        self.assertFalse(self.r.match("spatialdds/op_b/ego/pose/v1", "FramedPose", sub, 0))

    def test_wildcard_segment(self):
        sub = self._sub("spatialdds/*/sensing/detection3d/v1")
        self.assertTrue(self.r.match(
            "spatialdds/operator_a/sensing/detection3d/v1", "Detection3DSet", sub, 0))
        self.assertTrue(self.r.match(
            "spatialdds/platform/sensing/detection3d/v1", "Detection3DSet", sub, 0))
        self.assertFalse(self.r.match(
            "spatialdds/operator_a/ego/pose/v1", "FramedPose", sub, 0))

    def test_wildcard_all(self):
        sub = self._sub("*")
        self.assertTrue(self.r.match("anything/at/all", "AnyType", sub, 0))

    def test_msg_type_filter(self):
        sub = self._sub("spatialdds/*", msg_types={"Detection3DSet"})
        self.assertTrue(self.r.match("spatialdds/x", "Detection3DSet", sub, 0))
        self.assertFalse(self.r.match("spatialdds/x", "FramedPose", sub, 0))

    def test_msg_type_set_with_multiple(self):
        sub = self._sub("*", msg_types={"FramedPose", "GeoPose"})
        self.assertTrue(self.r.match("topic", "FramedPose", sub, 0))
        self.assertTrue(self.r.match("topic", "GeoPose", sub, 0))
        self.assertFalse(self.r.match("topic", "ImuSample", sub, 0))


class TestRateLimiting(unittest.TestCase):
    def test_2hz_throttle(self):
        sub = Subscription(id="s", pattern="*", max_rate_hz=2.0)
        r = TopicRouter()
        t0 = 1_000_000_000  # use a realistic non-zero timestamp
        # First message: last_sent_ns == 0 (default), bypasses throttle.
        self.assertTrue(r.match("t", "T", sub, t0))
        sub.last_sent_ns = t0  # caller would set this after dispatching
        # 100ms later — below 500ms minimum interval, throttled
        self.assertFalse(r.match("t", "T", sub, t0 + 100_000_000))
        # 600ms later — passes
        self.assertTrue(r.match("t", "T", sub, t0 + 600_000_000))

    def test_first_message_bypasses_throttle(self):
        """A freshly-created subscription with a low rate (1 Hz) should still
        receive its first matching message immediately — not wait a second."""
        sub = Subscription(id="s", pattern="*", max_rate_hz=1.0)
        r = TopicRouter()
        self.assertTrue(r.match("t", "T", sub, 1_000_000_000))

    def test_no_throttle_when_none(self):
        sub = Subscription(id="s", pattern="*", max_rate_hz=None)
        r = TopicRouter()
        self.assertTrue(r.match("t", "T", sub, 0))
        sub.last_sent_ns = 0
        self.assertTrue(r.match("t", "T", sub, 1_000_000))  # 1ms later, no throttle

    def test_zero_rate_treated_as_unlimited(self):
        sub = Subscription(id="s", pattern="*", max_rate_hz=0.0)
        r = TopicRouter()
        self.assertTrue(r.match("t", "T", sub, 0))
        sub.last_sent_ns = 0
        self.assertTrue(r.match("t", "T", sub, 1_000_000))


class TestTopicStats(unittest.TestCase):
    def test_first_seen_recorded(self):
        r = TopicRouter()
        r.update_stats("a", "TypeA", 1_000_000_000)
        topics = r.get_topics(stale_threshold_s=999_999, now_ns=1_000_000_000)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].first_seen_ns, 1_000_000_000)
        self.assertEqual(topics[0].message_count, 1)

    def test_message_count_accumulates(self):
        r = TopicRouter()
        for i in range(5):
            r.update_stats("a", "TypeA", 1_000_000_000 + i * 100_000_000)
        topics = r.get_topics(stale_threshold_s=999_999,
                                now_ns=1_500_000_000)
        self.assertEqual(topics[0].message_count, 5)

    def test_rate_hz(self):
        r = TopicRouter()
        # 5 messages over 1s → ~5 Hz
        for i in range(5):
            r.update_stats("a", "T", 1_000_000_000 + i * 250_000_000)
        topics = r.get_topics(stale_threshold_s=999_999,
                                now_ns=2_000_000_000)
        # last - first = 1s; 5/1 = 5 Hz
        self.assertAlmostEqual(topics[0].rate_hz, 5.0, places=1)

    def test_staleness_filter(self):
        r = TopicRouter()
        r.update_stats("recent", "T", 100_000_000_000)
        r.update_stats("old", "T", 10_000_000_000)
        topics = r.get_topics(stale_threshold_s=10.0, now_ns=100_500_000_000)
        names = {t.logical_topic for t in topics}
        self.assertIn("recent", names)
        self.assertNotIn("old", names)

    def test_msg_type_drift_keeps_latest(self):
        r = TopicRouter()
        r.update_stats("a", "TypeA", 1)
        r.update_stats("a", "TypeB", 2)
        topics = r.get_topics(stale_threshold_s=999_999, now_ns=2)
        self.assertEqual(topics[0].msg_type, "TypeB")


if __name__ == "__main__":
    unittest.main()
