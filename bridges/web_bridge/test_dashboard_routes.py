"""Smoke tests for the canvas dashboard + debug routes.

These tests don't import ``server.py`` (which would pull in the DDS
transport — heavy and not relevant). Instead we mirror the
file-serving snippet from server.py against the real static directory
and verify the expected HTML lands at the expected URLs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for p in (str(_HERE), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False


STATIC_DIR = _HERE / "static"


def _build_app() -> "FastAPI":
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def _root():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/debug")
    def _debug():
        return FileResponse(STATIC_DIR / "debug.html")

    return app


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi/httpx not installed")
class TestDashboardRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(_build_app())

    def test_landing_page_serves_canvas(self):
        """GET / returns the new canvas dashboard HTML."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("<canvas", body)
        # Sanity-checks for the wiring this page depends on:
        self.assertIn("/static/spatialdds-ws-client.js", body)
        self.assertIn("Multi-Operator Intersection", body)

    def test_debug_route_serves_topic_list(self):
        """GET /debug returns the legacy topic-list debug page."""
        resp = self.client.get("/debug")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("topics-tbl", body)        # the topic table id
        self.assertIn("Custom subscription", body)

    def test_static_assets_still_served(self):
        """The WebSocket client lib is still reachable at /static/..."""
        resp = self.client.get("/static/spatialdds-ws-client.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("SpatialDDSClient", resp.text)

    def test_dashboard_subscribes_to_v1_6_topics(self):
        """The dashboard must subscribe to every topic in the v1.6-era set
        (PlannedTrajectory, EntityBinding, Announce, conflict events), otherwise the
        viz can't render the new features."""
        body = self.client.get("/").text
        for needle in (
            "/sensing/detection3d/v1",
            "/ego/pose/v1",
            "/plan/",
            "/trajectory/v1",
            "/platform/fusion/track/v1",
            "/platform/events/trajectory_conflict/v1",
            "/platform/entity/binding/v1",
            "/discovery/announce/v1",
        ):
            self.assertIn(needle, body, f"missing subscription pattern: {needle}")


class TestStaticFilesPresent(unittest.TestCase):
    """Run even without fastapi to catch a missing/renamed HTML file."""

    def test_index_html_exists(self):
        self.assertTrue((STATIC_DIR / "index.html").is_file(),
                          "static/index.html missing")

    def test_debug_html_exists(self):
        self.assertTrue((STATIC_DIR / "debug.html").is_file(),
                          "static/debug.html missing — landing page swap "
                          "may have lost the legacy debug UI")

    def test_ws_client_present(self):
        self.assertTrue((STATIC_DIR / "spatialdds-ws-client.js").is_file())


if __name__ == "__main__":
    unittest.main()
