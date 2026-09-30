#!/usr/bin/env python3
"""
`scripts/cold_start.sh` is the repo's one end-to-end "no SpatialDDS client
code" demonstration -- bootstrap, search, pick a manifest, subscribe, use the
service -- and until this file nothing ran it.

It broke a month after it was written and stayed broken. Step 3 took the first
search result that advertised a topic, which was the VPS only because the VPS
was the sole announcing service. When the catalogue began announcing itself
(`ar_demo: the catalogue announces itself`) the search returned two manifests,
`results` is ordered by service_id, and `svc:content:demo/catalog` sorts ahead
of `svc:vps:demo/austin-downtown`. The script then POSTed a CONTENT service to
`/v1/localize`, which correctly answers 502.

Nothing failed. Every test passed, the endpoint behaved exactly as specified,
and the only record of what the script used to do was a transcript pasted into
`bridges/web_bridge/README.md` showing a pattern -- `spatialdds/vps/*` -- that
the script had stopped producing.

So the assertion here is not "it exits 0". It is *which service the script
chooses*, because that is the thing a new announcement silently changed.

Skipped when no bridge is on :8088. This needs the live stack -- the point is
the whole path, and a mocked version of it would not have caught the bug.
"""
import json
import os
import re
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.environ.get("BRIDGE", "http://127.0.0.1:8088")


def _bridge_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BRIDGE}/health", timeout=3) as response:
            return json.load(response).get("status") == "ok"
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def _have_websocket_client() -> bool:
    try:
        import websocket  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(_bridge_is_up(), f"no bridge on {BRIDGE}")
@unittest.skipUnless(_have_websocket_client(), "websocket-client not installed")
class ColdStartReachesAVps(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.result = subprocess.run(
            ["bash", os.path.join(REPO, "scripts", "cold_start.sh")],
            cwd=REPO, capture_output=True, text=True, timeout=180,
            env={**os.environ, "BRIDGE": BRIDGE},
        )
        cls.out = cls.result.stdout + cls.result.stderr

    def test_it_completes(self):
        self.assertEqual(self.result.returncode, 0,
                         f"cold_start.sh failed:\n{self.out}")

    def test_it_picks_a_vps_and_not_whatever_sorts_first(self):
        """
        The regression, stated directly. `svc:content:demo/catalog` sorts
        before any `svc:vps:…`, so a script picking by list order picks the
        catalogue -- and a catalogue cannot localize.
        """
        chosen = re.search(r"^service:\s+(\S+)", self.out, re.M)
        self.assertIsNotNone(chosen, f"no service line in output:\n{self.out}")
        self.assertIn(":vps:", chosen.group(1),
                      f"cold start chose {chosen.group(1)}, which is not a VPS")

    def test_the_search_returned_more_than_one_service(self):
        """
        Without this the test above passes for the wrong reason: if only the
        VPS announces, picking by order and picking by kind are the same
        choice, and the gate proves nothing. The bug needed two services to
        appear, so the gate needs two services to be there.
        """
        count = re.search(r"^(\d+) service manifest\(s\)", self.out, re.M)
        self.assertIsNotNone(count, f"no search summary in output:\n{self.out}")
        self.assertGreaterEqual(
            int(count.group(1)), 2,
            "only one service announced, so this run cannot distinguish "
            "selection-by-kind from selection-by-list-order")

    def test_the_vps_answered(self):
        self.assertIn("vps_response", self.out,
                      f"no VPS reply on the subscribed topic:\n{self.out}")


if __name__ == "__main__":
    unittest.main()
