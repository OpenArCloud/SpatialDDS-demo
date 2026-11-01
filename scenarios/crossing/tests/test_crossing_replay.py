#!/usr/bin/env python3
"""The replay asset and its viewer (`web/crossing/`).

* **The asset is the recording's.** Regenerated from the committed session
  it is byte-identical to the committed file (deterministic, and not stale);
  it is under the ceiling; every claim sample in it is the recording's
  sample verbatim, every segment's ending the recorded reason; every
  narration line names a source, and every record it names is in the
  recording; the tracks keep to their grid.
* **The page stands alone.** One HTML file and the asset: it loads nothing
  but three.js, pinned, from cdnjs, the fonts, and the asset beside it.
* **The page runs.** In a real browser, two loops with no console error;
  gate-9's arms, read from the drawn geometry, lie across the roadway with
  their tips meeting over the lanes when closed, and stand when open.
  Needs Node and web/'s Playwright with its Chromium; skips naming what to
  install without them, and runs once they are there.
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scenarios.crossing import export_replay                   # noqa: E402

PAGE = REPO / "web" / "crossing" / "index.html"


def secs(t):
    return int(t["sec"]) + int(t["nanosec"]) / 1e9


class TheAsset(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = export_replay.ASSET.read_text()
        cls.asset = json.loads(cls.text)
        cls.export = export_replay.Export()

    def test_it_is_the_recordings_export_byte_for_byte(self):
        self.assertEqual(export_replay.render(self.export.asset()), self.text,
                         "stale or nondeterministic: python3 -m "
                         "scenarios.crossing.export_replay")

    def test_its_shape_and_size(self):
        a = self.asset
        self.assertEqual(a["schema"], "owm-replay/2")
        for k in ("meta", "map", "tracks", "gate", "sight", "claims", "events"):
            self.assertIn(k, a)
        for k in ("recording", "sha256", "commit", "captured_utc", "window",
                  "loop_s", "track_dt", "honesty"):
            self.assertIn(k, a["meta"])
        self.assertLess(len(self.text.encode()), export_replay.CEILING_BYTES)

    def test_claims_are_the_recorded_samples_verbatim(self):
        recorded = {}
        for _t, d in self.export.s.edges:
            recorded.setdefault(d["id"], []).append(d)
        self.assertEqual({c["id"] for c in self.asset["claims"]}, set(recorded))
        for c in self.asset["claims"]:
            with self.subTest(claim=c["name"]):
                self.assertEqual([r["record"] for r in c["records"]], recorded[c["id"]])
                retired = [d["state_reason"] for d in recorded[c["id"]]
                           if d["state"] == "RETIRED"]
                self.assertEqual([s["reason"] for s in c["segments"] if s["reason"]],
                                 retired)

    def test_every_narration_line_names_a_source_that_exists(self):
        keys = {(t, d["id"], json.dumps(d["stamp"])) for t, d in self.export.s.edges}
        keys |= {(t, d["entity_id"], json.dumps(d["stamp"]))
                 for t, d in self.export.s.entities}
        self.assertGreater(len(self.asset["events"]), 10)
        for e in self.asset["events"]:
            with self.subTest(event=e["text"][:50]):
                self.assertTrue(e["sources"])
                for s in e["sources"]:
                    if "record" in s:
                        r = s["record"]
                        self.assertIn((r["lane"], r["key"], json.dumps(r["stamp"])), keys)
                    else:
                        self.assertIn("truth", s)

    def test_both_protagonists_are_narrated(self):
        who = {e["who"] for e in self.asset["events"]}
        self.assertTrue({"car-a1", "car-b2"} <= who)
        self.assertTrue(any("sits this one out" in e["text"] for e in self.asset["events"]))

    def test_tracks_keep_to_their_grid(self):
        dt, loop = self.asset["meta"]["track_dt"], self.asset["meta"]["loop_s"]
        for tr in self.asset["tracks"]:
            n = len(tr["x"])
            self.assertEqual(n, len(tr["y"]))
            self.assertEqual(n, len(tr["h"]))
            self.assertLessEqual(n, round(loop / dt) + 1)


class ThePage(unittest.TestCase):

    def test_it_loads_nothing_from_the_repository(self):
        html = PAGE.read_text()
        srcs = re.findall(r'<script[^>]*src="([^"]+)"', html)
        self.assertEqual(srcs, ["https://cdnjs.cloudflare.com/ajax/libs/three.js/0.149.0/three.min.js"])
        self.assertNotIn("import ", re.sub(r"<!--.*?-->", "", html, flags=re.S).split("<script>")[1])
        links = re.findall(r'<link[^>]*href="([^"]+)"', html)
        self.assertTrue(all(h.startswith("https://fonts.") for h in links), links)
        self.assertIn('"crossing-replay.json"', html)

    def test_it_says_what_is_simulated_without_interaction(self):
        html = PAGE.read_text()
        self.assertIn("Simulated world.", html)
        self.assertIn("Real records.", html)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class InABrowser(unittest.TestCase):

    def test_two_loops_without_a_console_error(self):
        node = shutil.which("node")
        web = REPO / "web"
        if node is None or not (web / "node_modules" / "@playwright" / "test").exists():
            self.skipTest("needs Node and web/'s Playwright: cd web && npm ci && "
                          "npx playwright install chromium")
        probe = subprocess.run([node, "-e", "require('@playwright/test').chromium.executablePath()"
                                " && require('fs').accessSync(require('@playwright/test').chromium.executablePath())"],
                               cwd=web, capture_output=True)
        if probe.returncode:
            self.skipTest("Playwright's Chromium is not installed: cd web && "
                          "npx playwright install chromium")
        port = _free_port()
        server = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                                   "--bind", "127.0.0.1", "--directory", str(PAGE.parent)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            out = subprocess.run([node, "crossing/check.mjs", f"http://127.0.0.1:{port}/",
                                  "/dev/null", "--loops-only"], cwd=web,
                                 capture_output=True, text=True, timeout=120)
        finally:
            server.terminate()
            server.wait(timeout=10)
        result = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(result["errors"], [])
        self.assertGreaterEqual(result["loops"], 2)
        self.assertEqual(result["gate"], [])


if __name__ == "__main__":
    unittest.main()
