"""
The well-known endpoints as HTTP, on both servers.

`test_discovery_http.py` runs the parity table through the shared core, which
is where the semantics are. This file is the layer above it: routes, methods,
query strings and status codes on the two servers that expose them —

  * `ar_demo/http_binding.py`, driven over a real socket;
  * `bridges/web_bridge/server.py`, driven through FastAPI's TestClient with
    the announce cache standing in for the bus.

The GET convenience form is the reason this file exists. §3.3.0 makes it
REQUIRED alongside POST and defines it as equivalent to a POST carrying
`{"geohash": ...}`, so "equivalent" is worth asserting rather than assuming:
each server is asked both ways and must answer identically.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for path in (ROOT, os.path.join(ROOT, "bridges", "web_bridge"), os.path.join(ROOT, "ar_demo")):
    if path not in sys.path:
        sys.path.insert(0, path)

from spatialdds_validation import (  # noqa: E402
    SpatialDDSValidator, create_coverage_bbox_earth_fixed,
)

# Downtown SF, inside geohash 9q8yy.
SF = (-122.45, 37.75, -122.40, 37.80)

# One stamp for every announce in this file. The two servers are asked to
# produce byte-identical documents, and a per-call `now_time()` would make them
# differ for a reason that has nothing to do with the binding.
STAMP = SpatialDDSValidator.now_time()


def announce(service_id="svc:vps:demo/sf", kind="VPS",
             manifest_uri="spatialdds://demo.example/zone:sf/manifest:vps"):
    frame_ref, element = create_coverage_bbox_earth_fixed(*SF)
    return {
        "service_id": service_id,
        "name": "SF VPS",
        "kind": kind,
        "org": "demo.example",
        "version": "1.7",
        "coverage": [element],
        "coverage_frame_ref": frame_ref,
        "manifest_uri": manifest_uri,
        "caps": {"supported_profiles": [{"name": "spatial.core", "major": 1,
                                         "min_minor": 7, "max_minor": 7}],
                 "preferred_profiles": [], "features": []},
        "topics": [{"name": "spatialdds/vps/query/v1", "type": "vps_query",
                    "version": "v1", "qos_profile": "VPS_REQ"}],
        "stamp": dict(STAMP),
        "ttl_sec": 300,
    }


def ids(body):
    return [r["service"]["service_id"] for r in body["results"]]


# --------------------------------------------------------------------------
# ar_demo/http_binding.py, over a real socket
# --------------------------------------------------------------------------

class HarnessEndpoints(unittest.TestCase):
    """The conformance harness, driven the way a client would drive it."""

    @classmethod
    def setUpClass(cls):
        from http.server import HTTPServer
        import http_binding

        cls.module = http_binding
        # Quiet: BaseHTTPRequestHandler logs every request to stderr.
        http_binding.SpatialDDSHTTPHandler.log_message = lambda *a, **k: None
        cls.server = HTTPServer(("127.0.0.1", 0), http_binding.SpatialDDSHTTPHandler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.module._announce_registry.clear()
        self.post("/.well-known/spatialdds/register", announce())

    def request(self, path, data=None):
        url = self.base + path
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"} if body else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def post(self, path, data):
        return self.request(path, data)

    def get(self, path):
        return self.request(path)

    def test_get_and_post_forms_answer_identically(self):
        status_get, body_get = self.get("/.well-known/spatialdds/search?geohash=9q8yy")
        status_post, body_post = self.post("/.well-known/spatialdds/search",
                                           {"geohash": "9q8yy"})
        self.assertEqual((status_get, status_post), (200, 200))
        self.assertEqual(ids(body_get), ["svc:vps:demo/sf"])
        self.assertEqual(body_get, body_post)

    def test_get_carries_the_kind_filter(self):
        _, matching = self.get("/.well-known/spatialdds/search?geohash=9q8yy&kind=VPS")
        _, other = self.get("/.well-known/spatialdds/search?geohash=9q8yy&kind=CONTENT")
        self.assertEqual(ids(matching), ["svc:vps:demo/sf"])
        self.assertEqual(other["results"], [])

    def test_get_without_a_geohash_is_400(self):
        status, _ = self.get("/.well-known/spatialdds/search")
        self.assertEqual(status, 400)

    def test_the_specs_own_examples_are_answered(self):
        """Both §3.3.0 worked examples, verbatim apart from the region."""
        status, minimal = self.post("/.well-known/spatialdds/search",
                                    {"geohash": "9q8yy"})
        self.assertEqual(status, 200)
        self.assertEqual(ids(minimal), ["svc:vps:demo/sf"])

        status, full = self.post("/.well-known/spatialdds/search", {
            "coverage": [{"crs": "EPSG:4326", "bbox": list(SF)}],
            "kind": ["VPS"],
            "filter": {"type_in": ["vps_query"], "qos_profile_in": [],
                       "module_id_in": []},
            "max_results": 10,
        })
        self.assertEqual(status, 200)
        self.assertEqual(ids(full), ["svc:vps:demo/sf"])

    def test_malformed_bodies_are_400_not_500(self):
        for label, body in (("expr", {"coverage": [], "geohash": "9q8yy",
                                      "expr": "kind == 'VPS'"}),
                            ("no coverage", {}),
                            ("bad geohash", {"geohash": "9q8yy!"})):
            with self.subTest(case=label):
                status, _ = self.post("/.well-known/spatialdds/search", body)
                self.assertEqual(status, 400)

    def test_results_are_service_manifests(self):
        _, body = self.get("/.well-known/spatialdds/search?geohash=9q8yy")
        doc = body["results"][0]
        self.assertEqual(doc["profile"], "spatial.manifest/1.7")
        self.assertEqual(doc["rtype"], "service")
        self.assertEqual(doc["service"]["service_id"], "svc:vps:demo/sf")
        self.assertEqual(doc["service"]["kind"], "VPS")

        # `service.connection` is absent, and deliberately so. `Announce` has
        # no domain_id, partitions or peers to carry, §8.2.3 makes
        # `service.connection` OPTIONAL, and F.1's rule for synthesis is to
        # omit what the announce cannot supply rather than invent it.
        #
        # §3.3.0 nonetheless says clients "MUST be able to extract
        # service.connection from any result and use it to join the service's
        # DDS domain", which a synthesized manifest cannot satisfy from an
        # announce alone. Filed against 1.8 rather than papered over here; the
        # deployment's connection parameters come from Layer 1 instead, which
        # is what /.well-known/spatialdds/bootstrap serves and what the
        # cold-start script uses.
        self.assertNotIn("connection", doc["service"])

    def test_bootstrap_shape(self):
        status, body = self.get("/.well-known/spatialdds/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(body["spatialdds_bootstrap"], "1.7")
        self.assertIsInstance(body["domain_id"], int)
        self.assertTrue(body["initial_peers"])
        # Omitted rather than advertised as a placeholder when unconfigured.
        self.assertNotIn("auth_hint", body)

    def test_an_unknown_well_known_path_is_404(self):
        status, _ = self.get("/.well-known/spatialdds/resolver")
        self.assertEqual(status, 404)


# --------------------------------------------------------------------------
# bridges/web_bridge/server.py, through the app
# --------------------------------------------------------------------------

try:
    from fastapi.testclient import TestClient
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed")
class BridgeEndpoints(unittest.TestCase):
    """
    The gateway's routes, with the announce cache standing in for the bus.

    Only `announce_records` is replaced — the routes, the shared core and the
    serve-or-synthesize hook are the real ones. The cache's own lifecycle is
    tested in test_discovery_http.py; here it is the source the endpoint reads,
    so that a departed service is shown to be absent from an HTTP response and
    not merely from the cache.
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SPATIALDDS_DDS_DOMAIN", "0")
        import server
        from announce_cache import AnnounceCache

        cls.server = server
        cls.AnnounceCache = AnnounceCache
        cls.client = TestClient(server.app)

    def setUp(self):
        self.cache = self.AnnounceCache()
        self.addCleanup(setattr, self.server.bridge, "announce_records",
                        self.server.bridge.announce_records)
        self.server.bridge.announce_records = self.cache.records
        self.assertTrue(self.cache.admit(announce()))

    def test_get_and_post_forms_answer_identically(self):
        by_get = self.client.get("/.well-known/spatialdds/search?geohash=9q8yy")
        by_post = self.client.post("/.well-known/spatialdds/search",
                                   json={"geohash": "9q8yy"})
        self.assertEqual((by_get.status_code, by_post.status_code), (200, 200))
        self.assertEqual(ids(by_get.json()), ["svc:vps:demo/sf"])
        self.assertEqual(by_get.json(), by_post.json())

    def test_search_is_answered_from_the_cache_without_touching_the_bus(self):
        """
        One round trip: the endpoint reads the cache the announce reader fills.
        If it tried to query the bus it would raise, because there isn't one.
        """
        response = self.client.post("/.well-known/spatialdds/search",
                                    json={"coverage": [{"crs": "EPSG:4326",
                                                        "bbox": list(SF)}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ids(response.json()), ["svc:vps:demo/sf"])

    def test_an_announce_only_service_survives_another_service_answering(self):
        """
        Answering a CoverageQuery is not obligatory; announcing is enough.

        OpenVPS's DDS binding publishes an Announce and serves VpsRequest, with
        no coverage responder at all. The endpoint used to return the bus
        answers whenever any arrived and consult the cache only when none did,
        so one service replying made every announce-only service vanish — a
        real VPS, its announce held in this bridge's own cache, reported as
        nobody being there. Both must come back.
        """
        from spatialdds_demo.discovery_http import record_from_announce

        answered = [record_from_announce(
            announce(service_id="svc:vps:demo/answers",
                     manifest_uri="spatialdds://demo.example/zone:sf/manifest:b"))]
        self.addCleanup(setattr, self.server.bridge, "coverage_records",
                        self.server.bridge.coverage_records)
        self.server.bridge.coverage_records = lambda *a, **k: answered

        response = self.client.get("/.well-known/spatialdds/search?geohash=9q8yy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(ids(response.json())),
                         ["svc:vps:demo/answers", "svc:vps:demo/sf"])

    def test_a_bus_answer_supersedes_the_cached_announce_for_that_service(self):
        """Same service on both sides resolves once, to the fresher answer."""
        from spatialdds_demo.discovery_http import record_from_announce

        answered = [record_from_announce(
            announce(manifest_uri="spatialdds://demo.example/zone:sf/manifest:fresh"))]
        self.addCleanup(setattr, self.server.bridge, "coverage_records",
                        self.server.bridge.coverage_records)
        self.server.bridge.coverage_records = lambda *a, **k: answered

        response = self.client.get("/.well-known/spatialdds/search?geohash=9q8yy")
        self.assertEqual(ids(response.json()), ["svc:vps:demo/sf"])

    def test_a_departed_service_is_absent_from_the_http_response(self):
        self.assertTrue(self.cache.depart("svc:vps:demo/sf"))
        response = self.client.get("/.well-known/spatialdds/search?geohash=9q8yy")
        self.assertEqual(response.json()["results"], [])

    def test_an_expired_service_is_absent_from_the_http_response(self):
        """
        Stale means "nothing has arrived for it lately", not "its stamp is old"
        — a re-announced sample can carry a frozen stamp and still be a live
        service saying so. See test_discovery_http for that case; here the
        announce arrived 700s ago and nothing has repeated it since.
        """
        import time

        cache = self.AnnounceCache()
        self.assertTrue(cache.admit(announce()))
        later = time.time() + 700             # > 2 x ttl_sec, nothing re-announced
        self.server.bridge.announce_records = lambda: cache.records(now=later)
        response = self.client.get("/.well-known/spatialdds/search?geohash=9q8yy")
        self.assertEqual(response.json()["results"], [])

    def test_an_authored_manifest_is_served_verbatim(self):
        """
        Serve-or-synthesize: the announce points at a manifest this deployment
        hosts, so the authored document is returned rather than one rebuilt
        from the announce's summary of itself.
        """
        authored = {"id": "spatialdds://demo.example/zone:sf/manifest:vps",
                    "profile": "spatial.manifest/1.7", "rtype": "service",
                    "service": {"service_id": "svc:vps:demo/sf", "kind": "VPS"},
                    "marker": "authored"}
        original = self.server._served_manifest
        self.addCleanup(setattr, self.server, "_served_manifest", original)
        self.server._served_manifest = lambda record: authored

        response = self.client.get("/.well-known/spatialdds/search?geohash=9q8yy")
        self.assertEqual(response.json()["results"], [authored])

    def test_a_manifest_describing_another_service_is_refused(self):
        """
        `manifest_uri` is only a string in an announce, so a misconfigured
        deployment can point at someone else's manifest. Serving it would
        answer discovery with a document naming a different service, whose
        topics and connection a client would take at face value.

        Seen for real while bringing the AR demo up on Fargate: a VPS
        configured for Austin whose manifest_uri still pointed at the bundled
        SF manifest, so `/search` reported `svc:vps:demo/sf-downtown` for a
        service announcing `svc:vps:demo/austin-downtown`.
        """
        record = self.cache.records()[0]
        other = {"id": "spatialdds://demo.example/zone:sf/manifest:vps",
                 "profile": "spatial.manifest/1.7", "rtype": "service",
                 "service": {"service_id": "svc:vps:someone/else", "kind": "VPS"}}
        mine = {**other, "service": {"service_id": record.service_id, "kind": "VPS"}}

        original = self.server.resolve_manifest
        self.addCleanup(setattr, self.server, "resolve_manifest", original)

        self.server.resolve_manifest = lambda uri, **kw: (other, {"mode": "test"})
        self.assertIsNone(self.server._served_manifest(record),
                          "a manifest for another service must not be served")

        self.server.resolve_manifest = lambda uri, **kw: (mine, {"mode": "test"})
        self.assertEqual(self.server._served_manifest(record), mine,
                         "a manifest for this service is served verbatim")

    def test_an_unmatched_announce_is_synthesized(self):
        response = self.client.get("/.well-known/spatialdds/search?geohash=9q8yy")
        doc = response.json()["results"][0]
        self.assertEqual(doc["profile"], "spatial.manifest/1.7")
        self.assertNotIn("marker", doc)

    def test_malformed_bodies_are_400(self):
        for label, body in (("expr", {"geohash": "9q8yy", "expr": "kind == 'VPS'"}),
                            ("no coverage", {}),
                            ("bad geohash", {"geohash": "9q8yy!"})):
            with self.subTest(case=label):
                response = self.client.post("/.well-known/spatialdds/search", json=body)
                self.assertEqual(response.status_code, 400)

    def test_bootstrap_shape(self):
        body = self.client.get("/.well-known/spatialdds/bootstrap").json()
        self.assertEqual(body["spatialdds_bootstrap"], "1.7")
        self.assertIn("initial_peers", body)


class CatalogKindVocabulary(unittest.TestCase):
    """
    The bridge's default catalogue filter has to admit every kind the browser
    can draw. It did not: `types.ts` declared "model" and the bridge filtered
    on ["overlay", "poi", "mesh"], so a glTF item was dropped here — before
    the catalogue saw the query — and the only symptom was results=0.
    """

    def test_default_kinds_cover_what_the_client_declares(self):
        import re as _re
        import pathlib as _pathlib
        root = _pathlib.Path(__file__).resolve().parent.parent.parent
        types_ts = (root / "web" / "src" / "types.ts").read_text()
        declared = _re.search(r"kind: ((?:'[a-z]+'\s*\|?\s*)+);", types_ts)
        self.assertIsNotNone(declared, "could not find CatalogItem.kind in types.ts")
        want = set(_re.findall(r"'([a-z]+)'", declared.group(1)))

        server_py = (root / "bridges" / "web_bridge" / "server.py").read_text()
        got = set(_re.findall(r"'([a-z]+)'|\"([a-z]+)\"",
                              _re.search(r"DEFAULT_CATALOG_KINDS = \[([^\]]*)\]",
                                         server_py).group(1)))
        got = {a or b for a, b in got}
        missing = want - got
        self.assertEqual(missing, set(),
                         f"browser can render {sorted(missing)} but the bridge "
                         f"filters them out; DEFAULT_CATALOG_KINDS is {sorted(got)}")


class CrossServerParity(unittest.TestCase):
    """The two servers, asked the same thing over HTTP, answer the same thing."""

    @unittest.skipUnless(_HAVE_FASTAPI, "fastapi not installed")
    def test_same_request_same_response(self):
        harness = HarnessEndpoints("test_get_and_post_forms_answer_identically")
        harness.setUpClass()
        try:
            harness.setUp()
            _, from_harness = harness.get("/.well-known/spatialdds/search?geohash=9q8yy")
        finally:
            harness.tearDownClass()

        bridge = BridgeEndpoints("test_get_and_post_forms_answer_identically")
        bridge.setUpClass()
        bridge.setUp()
        try:
            from_bridge = bridge.client.get(
                "/.well-known/spatialdds/search?geohash=9q8yy").json()
        finally:
            bridge.doCleanups()

        self.assertEqual(from_harness, from_bridge)


if __name__ == "__main__":
    unittest.main()
