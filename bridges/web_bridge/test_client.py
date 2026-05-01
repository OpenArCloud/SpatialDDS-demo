"""Unit tests for client_manager. No FastAPI dependency.

We supply our own ``send_json`` async callable and inspect what would have
been pushed to a real WebSocket.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any, List

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from client_manager import ClientManager  # noqa: E402
from topic_router import TopicRouter  # noqa: E402


class _Recorder:
    """Captures everything ``ClientManager`` would have sent to a client."""

    def __init__(self):
        self.messages: List[dict] = []
        self.fail = False

    async def send_json(self, payload: dict) -> None:
        if self.fail:
            raise ConnectionResetError("simulated drop")
        self.messages.append(payload)


def _run(coro):
    # Use a fresh loop per test (Python 3.10+ deprecates ``get_event_loop``
    # when there isn't a running one).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestSubscribeFlow(unittest.TestCase):
    def setUp(self):
        self.router = TopicRouter()
        self.cm = ClientManager(self.router)
        self.rec = _Recorder()
        self.session = self.cm.add_client(self.rec.send_json, client_id="cA")

    def test_subscribe_creates_subscription(self):
        resp = self.cm.handle_subscribe(self.session, {
            "id": "s1", "pattern": "spatialdds/*"})
        self.assertEqual(resp["type"], "subscribed")
        self.assertEqual(resp["id"], "s1")
        self.assertEqual(self.session.subscriptions["s1"].pattern, "spatialdds/*")

    def test_subscribe_missing_pattern_errors(self):
        resp = self.cm.handle_subscribe(self.session, {"id": "s1"})
        self.assertEqual(resp["type"], "error")
        self.assertIn("pattern", resp["message"].lower())

    def test_subscribe_with_msg_types_and_rate(self):
        self.cm.handle_subscribe(self.session, {
            "id": "s1", "pattern": "*",
            "msg_types": ["Detection3DSet", "FramedPose"],
            "max_rate_hz": 5.0,
        })
        sub = self.session.subscriptions["s1"]
        self.assertEqual(sub.msg_types, {"Detection3DSet", "FramedPose"})
        self.assertEqual(sub.max_rate_hz, 5.0)

    def test_unsubscribe(self):
        self.cm.handle_subscribe(self.session, {"id": "s1", "pattern": "*"})
        resp = self.cm.handle_unsubscribe(self.session, {"id": "s1"})
        self.assertEqual(resp["type"], "unsubscribed")
        self.assertNotIn("s1", self.session.subscriptions)

    def test_unsubscribe_unknown_errors(self):
        resp = self.cm.handle_unsubscribe(self.session, {"id": "ghost"})
        self.assertEqual(resp["type"], "error")

    def test_invalid_max_rate_errors(self):
        resp = self.cm.handle_subscribe(self.session, {
            "id": "s1", "pattern": "*", "max_rate_hz": "fast"})
        self.assertEqual(resp["type"], "error")


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.router = TopicRouter()
        self.cm = ClientManager(self.router)
        self.rec_a = _Recorder()
        self.rec_b = _Recorder()
        self.s_a = self.cm.add_client(self.rec_a.send_json, client_id="cA")
        self.s_b = self.cm.add_client(self.rec_b.send_json, client_id="cB")

    def test_dispatch_routes_only_to_matching_clients(self):
        self.cm.handle_subscribe(self.s_a,
                                  {"id": "a1", "pattern": "spatialdds/*/ego/pose/v1"})
        self.cm.handle_subscribe(self.s_b,
                                  {"id": "b1", "pattern": "spatialdds/*/sensing/*"})
        sent = _run(self.cm.dispatch(
            "spatialdds/op/ego/pose/v1", "FramedPose",
            {"x": 1}, 1_000_000_000))
        self.assertEqual(sent, 1)
        self.assertEqual(len(self.rec_a.messages), 1)
        self.assertEqual(len(self.rec_b.messages), 0)
        msg = self.rec_a.messages[0]
        self.assertEqual(msg["type"], "data")
        self.assertEqual(msg["sub_id"], "a1")
        self.assertEqual(msg["msg_type"], "FramedPose")
        self.assertEqual(msg["payload"], {"x": 1})

    def test_dispatch_one_data_per_session_even_with_multi_match(self):
        """A client with two subscriptions that both match a message gets
        ONE data message (not two) — server bandwidth optimization."""
        self.cm.handle_subscribe(self.s_a,
                                  {"id": "a1", "pattern": "spatialdds/*"})
        self.cm.handle_subscribe(self.s_a,
                                  {"id": "a2", "pattern": "spatialdds/op/*"})
        _run(self.cm.dispatch(
            "spatialdds/op/x/v1", "T", {"x": 1}, 1_000_000_000))
        self.assertEqual(len(self.rec_a.messages), 1)

    def test_dispatch_dead_client_is_pruned(self):
        self.cm.handle_subscribe(self.s_a,
                                  {"id": "a1", "pattern": "*"})
        self.rec_a.fail = True
        _run(self.cm.dispatch("topic", "T", {}, 1))
        self.assertEqual(self.cm.client_count, 1)  # 'cB' remains
        self.assertNotIn("cA", self.cm._clients)

    def test_topic_stats_updated_even_without_subscribers(self):
        _run(self.cm.dispatch("orphan/topic", "T", {}, 1_000_000_000))
        topics = self.router.get_topics(stale_threshold_s=999_999,
                                          now_ns=2_000_000_000)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].logical_topic, "orphan/topic")


if __name__ == "__main__":
    unittest.main()
