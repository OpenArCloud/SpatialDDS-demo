"""WebSocket integration test for the generic protocol.

We don't spin up a real CycloneDDS bus here — the conversion + routing logic
between an envelope arriving and a WebSocket client receiving a message is
purely in-process. We drive ``ClientManager.dispatch(...)`` directly from
the test (the same coroutine the live server schedules from the DDS poll
thread) and assert the WebSocket sees what we expect.

Running this test requires ``fastapi`` and ``httpx``; both are already in the
web_bridge requirements.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for p in (str(_HERE), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.testclient import TestClient
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi/httpx not installed")
class TestGenericWebSocket(unittest.TestCase):
    """End-to-end /ws protocol test — no DDS, no envelope transport.

    We build a minimal FastAPI app that re-uses the real ``TopicRouter`` +
    ``ClientManager`` and the same WebSocket handler logic from server.py.
    Then we drive ``ClientManager.dispatch(...)`` directly and check that
    the WebSocket client receives the right ``data`` messages.
    """

    @classmethod
    def setUpClass(cls):
        import json
        from client_manager import ClientManager
        from topic_router import TopicRouter

        cls.router = TopicRouter()
        cls.manager = ClientManager(cls.router)

        app = FastAPI()

        @app.websocket("/ws")
        async def ws(socket: WebSocket):
            await socket.accept()

            async def send_json(payload):
                await socket.send_json(payload)

            session = cls.manager.add_client(send_json)
            try:
                while True:
                    raw = await socket.receive_text()
                    msg = json.loads(raw)
                    mt = msg.get("type")
                    if mt == "subscribe":
                        await send_json(cls.manager.handle_subscribe(session, msg))
                    elif mt == "unsubscribe":
                        await send_json(cls.manager.handle_unsubscribe(session, msg))
                    elif mt == "list_topics":
                        topics = cls.router.get_topics()
                        await send_json({
                            "type": "topics",
                            "topics": [
                                {"logical_topic": t.logical_topic,
                                 "msg_type": t.msg_type,
                                 "rate_hz": round(t.rate_hz, 2),
                                 "message_count": t.message_count,
                                 "last_seen_ns": t.last_seen_ns}
                                for t in topics
                            ],
                        })
                    elif mt == "ping":
                        await send_json({"type": "pong"})
                    elif mt == "_test_dispatch":
                        # Test-only escape hatch: drive ClientManager.dispatch
                        # from inside the server's event loop so the data
                        # message reaches THIS websocket. Real production
                        # dispatch comes from the DDS poll thread via
                        # call_soon_threadsafe — same coroutine, just a
                        # different scheduling source.
                        await cls.manager.dispatch(
                            msg.get("logical_topic", ""),
                            msg.get("msg_type", ""),
                            msg.get("payload", {}),
                            int(msg.get("timestamp_ns", 0)),
                        )
            except WebSocketDisconnect:
                pass
            finally:
                cls.manager.remove_client(session.client_id)

        cls.app = app

    def setUp(self):
        # Reset state between tests so they don't leak.
        self.manager._clients.clear()
        self.router._stats.clear()

    def test_subscribe_then_receive_data(self):
        client = TestClient(self.app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "subscribe", "id": "s1",
                           "pattern": "spatialdds/*/sensing/detection3d/v1"})
            ack = ws.receive_json()
            self.assertEqual(ack, {"type": "subscribed", "id": "s1",
                                     "pattern": "spatialdds/*/sensing/detection3d/v1",
                                     "status": "ok"})

            # Server side: simulate a DDS envelope arriving (via the test
            # escape hatch so dispatch runs in the server's event loop).
            ws.send_json({
                "type": "_test_dispatch",
                "logical_topic": "spatialdds/op_a/sensing/detection3d/v1",
                "msg_type": "Detection3DSet",
                "payload": {"detections": [{"det_id": "d1"}], "source_operator": "op_a"},
                "timestamp_ns": 12345,
            })

            data = ws.receive_json()
            self.assertEqual(data["type"], "data")
            self.assertEqual(data["sub_id"], "s1")
            self.assertEqual(data["msg_type"], "Detection3DSet")
            self.assertEqual(data["logical_topic"],
                              "spatialdds/op_a/sensing/detection3d/v1")
            self.assertEqual(data["payload"]["detections"][0]["det_id"], "d1")

    def test_msg_type_filter_excludes_non_matches(self):
        client = TestClient(self.app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "subscribe", "id": "s1",
                           "pattern": "spatialdds/*",
                           "msg_types": ["FramedPose"]})
            ws.receive_json()  # ack

            # Should NOT match (wrong msg_type)
            ws.send_json({"type": "_test_dispatch",
                           "logical_topic": "spatialdds/op/x",
                           "msg_type": "Detection3DSet",
                           "payload": {"x": 1}, "timestamp_ns": 1})
            # Should match
            ws.send_json({"type": "_test_dispatch",
                           "logical_topic": "spatialdds/op/x",
                           "msg_type": "FramedPose",
                           "payload": {"x": 2}, "timestamp_ns": 2})

            data = ws.receive_json()
            self.assertEqual(data["msg_type"], "FramedPose")
            self.assertEqual(data["payload"]["x"], 2)

    def test_unsubscribe_stops_data(self):
        client = TestClient(self.app)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "subscribe", "id": "s1", "pattern": "*"})
            ws.receive_json()
            ws.send_json({"type": "unsubscribe", "id": "s1"})
            ws.receive_json()

            # No subs → no client message after dispatch.
            ws.send_json({"type": "_test_dispatch",
                           "logical_topic": "topic", "msg_type": "T",
                           "payload": {}, "timestamp_ns": 1})

            # Send a ping; if we received an unwanted data message before this,
            # the next receive would surface it instead of "pong".
            ws.send_json({"type": "ping"})
            reply = ws.receive_json()
            self.assertEqual(reply["type"], "pong")

    def test_list_topics_after_dispatch(self):
        import time
        client = TestClient(self.app)
        # Use a recent timestamp so the topic isn't filtered by the 30s
        # staleness window in get_topics().
        now_ns = time.time_ns()
        with client.websocket_connect("/ws") as ws:
            for i in range(3):
                ws.send_json({"type": "_test_dispatch",
                               "logical_topic": "spatialdds/op/x/v1",
                               "msg_type": "T",
                               "payload": {"i": i},
                               "timestamp_ns": now_ns + i * 100_000_000})
            ws.send_json({"type": "list_topics"})
            reply = ws.receive_json()
            self.assertEqual(reply["type"], "topics")
            names = {t["logical_topic"] for t in reply["topics"]}
            self.assertIn("spatialdds/op/x/v1", names)
            top = next(t for t in reply["topics"]
                        if t["logical_topic"] == "spatialdds/op/x/v1")
            self.assertEqual(top["message_count"], 3)


if __name__ == "__main__":
    unittest.main()
