#!/usr/bin/env python3
"""
The relationship lane through the binding: acceptance item 5 of the OWM 0.2
prototype brief, in process.

`bridges/web_bridge/binding.py` as the stack runs it: `build_state`, the
package's `make_app` with live subscriptions, and the real `Pump` on a real
bus. What is asserted is what a browser gets:

* the lane is served, latched and public (no credential);
* an edge held on the bus is in the collection GET, and its instance GET;
* a WebSocket subscriber is handed it in the replay;
* the edge's withdrawal reaches that live subscriber as a dispose carrying
  the asserter's reason, and the collection stops holding it;
* a killed asserter's edge stays in the collection, and the live subscriber
  is told its writer's liveliness ended, with no dispose (item 4, as a
  browser sees it).

Needs a DDS domain, and skips loudly without one.
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import _bus as bus  # noqa: E402
from aiohttp import ClientSession, WSMsgType  # noqa: E402
from aiohttp.test_utils import TestServer  # noqa: E402
from spatialdds.dds.types.spatial.owm import Basis  # noqa: E402
from spatialdds.server import gw_http  # noqa: E402

from bridges.web_bridge import binding  # noqa: E402
from spatialdds_demo import claims  # noqa: E402

DOMAIN = 81
PATH = "/" + "/".join(claims.TOPIC.split("/")[1:])
ROBOT, FOUNTAIN = "ent:robot:tb3", "ent:fountain:littlefield"


class RelationshipLaneThroughTheBinding(unittest.TestCase):

    def test_http_ws_and_a_dispose_with_its_reason(self):
        asserter = bus.participant(DOMAIN, "svc:test:binding-asserter")
        self.addCleanup(asserter.close)
        writer = claims.ClaimWriter(asserter)
        st = binding.build_state()
        app = gw_http.make_app(st, "http://test", "tok", publish_enabled=False,
                               live_subscriptions=True)
        try:
            pump = binding.Pump(st, app, DOMAIN)
        except Exception as exc:
            raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")
        self.addCleanup(lambda: pump.participant.close(reason="test over"))
        thread = threading.Thread(target=pump.run, daemon=True)
        thread.start()
        self.addCleanup(pump.stop)

        res = st.resource_for(PATH)
        self.assertIsNotNone(res, f"the binding serves no {PATH}")
        self.assertTrue(res.latched)
        self.assertEqual(res.access, "public-cacheable")

        edge = writer.hold(ROBOT, claims.SF_WITHIN, FOUNTAIN, Basis.OBSERVED,
                           valid_from=claims.now_time())
        asyncio.run(self._through_http(app, edge, writer))

    async def _through_http(self, app, edge, writer):
        server = TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as http:
                base = str(server.make_url(PATH))
                docs = await self._until(http, base, lambda d: d)
                self.assertEqual([d["id"] for d in docs], [edge.id])
                [doc] = docs
                self.assertEqual(doc["predicate"], claims.SF_WITHIN)
                self.assertEqual(doc["basis"], "OBSERVED")
                self.assertEqual(doc["state"], "CREATED")
                async with http.get(f"{base}/{edge.id}") as r:
                    self.assertEqual(r.status, 200, await r.text())
                    self.assertEqual((await r.json())["id"], edge.id)

                async with http.ws_connect(base) as ws:
                    replay = await self._read_until(
                        ws, lambda e: e.get("event") == "end_of_replay")
                    self.assertEqual(
                        [e["key"] for e in replay if e.get("event") == "sample"],
                        [edge.id])
                    writer.retire(ROBOT, claims.SF_WITHIN, FOUNTAIN,
                                  claims.NO_LONGER_HOLDS, "the robot left",
                                  valid_until=claims.now_time())
                    live = await self._read_until(
                        ws, lambda e: e.get("event") == "dispose")
                    terminal = [e for e in live if e.get("event") == "sample"]
                    self.assertTrue(terminal, live)
                    self.assertEqual(terminal[-1]["data"]["state"], "RETIRED")
                    self.assertTrue(terminal[-1]["data"]["has_valid_until"])
                    dispose = live[-1]
                    self.assertEqual(dispose["key"], edge.id)
                    self.assertEqual(dispose["reason"],
                                     "no longer holds: the robot left")
                self.assertEqual(await self._until(http, base, lambda d: not d),
                                 [])
        finally:
            await server.close()

    async def _until(self, http, url, ok, timeout=8.0):
        deadline = time.time() + timeout
        while True:
            async with http.get(url) as r:
                self.assertEqual(r.status, 200, await r.text())
                self.assertIn("public", r.headers.get("Cache-Control", ""))
                docs = await r.json()
            if ok(docs) or time.time() > deadline:
                return docs
            await asyncio.sleep(0.1)

    async def _read_until(self, ws, done, timeout=8.0):
        events = []
        while True:
            msg = await asyncio.wait_for(ws.receive(), timeout)
            if msg.type != WSMsgType.TEXT:
                self.fail(f"socket ended early: {msg!r}, after {events!r}")
            event = json.loads(msg.data)
            events.append(event)
            if done(event):
                return events


class AKilledAssertersEdgeThroughTheBinding(unittest.TestCase):

    SCRIPT = (
        "import sys, time\n"
        "sys.path.insert(0, {repo!r})\n"
        "import spatialdds as sd\n"
        "from spatialdds.dds.types.spatial.owm import Basis\n"
        "from spatialdds_demo import claims\n"
        "from spatialdds_demo.lanes import SCENE\n"
        "p = sd.participant(SCENE, domain={domain}, service_id='svc:test:doomed')\n"
        "claims.ClaimWriter(p).hold('ent:robot:tb3', claims.SF_WITHIN,\n"
        "    'ent:fountain:littlefield', Basis.OBSERVED,\n"
        "    valid_from=claims.now_time())\n"
        "print('held', flush=True)\n"
        "time.sleep(120)\n")

    def test_liveliness_lost_reaches_a_subscriber_and_nothing_is_disposed(self):
        st = binding.build_state()
        app = gw_http.make_app(st, "http://test", "tok", publish_enabled=False,
                               live_subscriptions=True)
        try:
            pump = binding.Pump(st, app, DOMAIN + 1)
        except Exception as exc:
            raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")
        self.addCleanup(lambda: pump.participant.close(reason="test over"))
        threading.Thread(target=pump.run, daemon=True).start()
        self.addCleanup(pump.stop)
        child = subprocess.Popen(
            [sys.executable, "-c",
             self.SCRIPT.format(repo=str(REPO), domain=DOMAIN + 1)],
            stdout=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.assertEqual(child.stdout.readline().strip(), "held")
        asyncio.run(self._kill_and_watch(app, child))

    async def _kill_and_watch(self, app, child):
        server = TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as http:
                base = str(server.make_url(PATH))
                lane = RelationshipLaneThroughTheBinding
                docs = await lane._until(self, http, base, lambda d: d)
                [edge] = docs
                async with http.ws_connect(base) as ws:
                    await lane._read_until(
                        self, ws, lambda e: e.get("event") == "end_of_replay")
                    child.send_signal(signal.SIGKILL)
                    events = await lane._read_until(
                        self, ws, lambda e: e.get("event") == "liveliness",
                        timeout=20.0)
                    self.assertEqual(events[-1]["key"], edge["id"])
                    self.assertFalse(events[-1]["alive"])
                    self.assertNotIn("dispose", [e.get("event") for e in events])
                async with http.get(base) as r:
                    self.assertEqual([d["id"] for d in await r.json()],
                                     [edge["id"]],
                                     "a killed asserter's edge stands")
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
