"""
Discovery-binding tests.

The parity suite is the point of this file: the same request/expectation table
is run against both backends — the in-memory registry ar_demo/http_binding.py
serves, and the live announce cache the web bridge serves — asserting identical
results. If the two servers ever diverge on a request, one of these fails.

Also covers the bridge-only behaviour: cache lifecycle (depart, TTL) and the
serve-or-synthesize rule.

No DDS, no FastAPI client, no network. The cache is seeded through the same
`admit()` path the bus ingestion uses.
"""

import os
import sys
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for path in (ROOT, os.path.join(ROOT, "bridges", "web_bridge"), os.path.join(ROOT, "ar_demo")):
    if path not in sys.path:
        sys.path.insert(0, path)

from announce_cache import AnnounceCache  # noqa: E402
from spatialdds_demo.discovery_http import (  # noqa: E402
    DiscoveryError,
    geohash_bounds,
    query_from_geohash,
    record_from_announce,
    search,
)
from spatialdds_validation import (  # noqa: E402
    SpatialDDSValidator,
    create_coverage_bbox_earth_fixed,
)

SF = (-122.52, 37.70, -122.35, 37.85)
AUSTIN = (-97.75, 30.27, -97.72, 30.29)


def _announce(service_id, bbox, *, kind="VPS", name="Svc", topics=None, caps=None,
              ttl_sec=300, stamp=None, manifest_uri=None):
    frame_ref, element = create_coverage_bbox_earth_fixed(*bbox)
    return {
        "service_id": service_id,
        "name": name,
        "kind": kind,
        "org": "ExampleOrg",
        "coverage": [element],
        "coverage_frame_ref": frame_ref,
        "manifest_uri": manifest_uri or f"spatialdds://demo.example/zone:z/manifest:{service_id[-4:]}",
        "caps": caps or {
            "supported_profiles": [
                {"name": "spatial.core", "major": 1, "min_minor": 7, "max_minor": 7}
            ],
            "preferred_profiles": ["spatial.core/1.7"],
            "features": ["blob.crc32"],
        },
        "topics": topics if topics is not None else [
            {"name": "spatialdds/vps/query/v1", "type": "vps_query",
             "version": "v1", "qos_profile": "VPS_REQ"},
        ],
        "stamp": stamp or SpatialDDSValidator.now_time(),
        "ttl_sec": ttl_sec,
    }


def _query(bbox, **extra):
    frame_ref, element = create_coverage_bbox_earth_fixed(*bbox)
    query = {"coverage": [element], "coverage_frame_ref": frame_ref}
    query.update(extra)
    return query


# The shared table both backends must answer identically.
ANNOUNCES = [
    _announce("svc:a", SF, kind="VPS", name="SF VPS"),
    _announce("svc:b", AUSTIN, kind="VPS", name="Austin VPS"),
    _announce("svc:c", SF, kind="CONTENT", name="SF Content",
              topics=[{"name": "spatialdds/x/tile/v1", "type": "geometry_tile",
                       "version": "v1", "qos_profile": "GEOM_TILE"}]),
]

CASES = [
    ("sf bbox matches sf services",
     _query((-122.45, 37.75, -122.40, 37.80)), ["svc:a", "svc:c"]),
    ("austin bbox matches only austin",
     _query((-97.74, 30.28, -97.73, 30.285)), ["svc:b"]),
    ("disjoint bbox matches nothing",
     _query((10.0, 10.0, 11.0, 11.0)), []),
    ("kind filter narrows to CONTENT",
     _query((-122.45, 37.75, -122.40, 37.80), kind=["CONTENT"]), ["svc:c"]),
    ("type_in filter narrows by topic type",
     _query((-122.45, 37.75, -122.40, 37.80),
            has_filter=True,
            filter={"type_in": ["geometry_tile"], "qos_profile_in": [], "module_id_in": []}),
     ["svc:c"]),
    ("module_id_in matches the 1.7 family form",
     _query((-122.45, 37.75, -122.40, 37.80),
            has_filter=True,
            filter={"type_in": [], "qos_profile_in": [], "module_id_in": ["spatial.core/1.7"]}),
     ["svc:a", "svc:c"]),
    ("empty filter arrays match all",
     _query((-122.45, 37.75, -122.40, 37.80),
            has_filter=True,
            filter={"type_in": [], "qos_profile_in": [], "module_id_in": []}),
     ["svc:a", "svc:c"]),
    ("max_results pages",
     _query((-122.45, 37.75, -122.40, 37.80), max_results=1), ["svc:a"]),
]


def _ids(response):
    return [r["service"]["service_id"] for r in response["results"]]


class RegistryBackend:
    """What ar_demo/http_binding.py serves from."""

    name = "http_binding registry"

    def __init__(self, announces):
        self._records = [record_from_announce(dict(a)) for a in announces]

    def records(self):
        return list(self._records)


class CacheBackend:
    """What the web bridge serves from, seeded through the bus ingestion path."""

    name = "web_bridge announce cache"

    def __init__(self, announces):
        self._cache = AnnounceCache()
        for announce in announces:
            assert self._cache.admit(dict(announce)), announce["service_id"]

    def records(self):
        return self._cache.records()


BACKENDS = (RegistryBackend, CacheBackend)


class Parity(unittest.TestCase):
    """Same request, same answer, whichever server is asked."""

    def test_cases_agree_across_backends(self):
        for label, query, expected in CASES:
            answers = {}
            for backend_cls in BACKENDS:
                backend = backend_cls(ANNOUNCES)
                response = search(backend.records(), dict(query))
                answers[backend_cls.name] = _ids(response)
                with self.subTest(case=label, backend=backend_cls.name):
                    self.assertEqual(response and _ids(response), expected)
            self.assertEqual(
                len(set(map(tuple, answers.values()))), 1,
                f"backends disagreed on {label!r}: {answers}",
            )

    def test_full_manifests_agree_across_backends(self):
        query = _query((-122.45, 37.75, -122.40, 37.80))
        docs = [search(b(ANNOUNCES).records(), dict(query))["results"] for b in BACKENDS]
        self.assertEqual(docs[0], docs[1])
        for doc in docs[0]:
            self.assertEqual(doc["profile"], "spatial.manifest/1.7")
            self.assertEqual(doc["rtype"], "service")

    def test_rejections_agree_across_backends(self):
        bad = [
            ("expr removed in 1.7", _query(SF, expr='kind=="VPS"')),
            ("missing coverage", {"coverage_frame_ref": {"uuid": "u", "fqn": "earth-fixed"}}),
        ]
        for label, query in bad:
            for backend_cls in BACKENDS:
                with self.subTest(case=label, backend=backend_cls.name):
                    with self.assertRaises(DiscoveryError):
                        search(backend_cls(ANNOUNCES).records(), dict(query))


class Pagination(unittest.TestCase):
    def test_page_token_walks_the_result_set(self):
        records = RegistryBackend(ANNOUNCES).records()
        query = _query((-122.45, 37.75, -122.40, 37.80), max_results=1)
        first = search(records, dict(query))
        self.assertEqual(_ids(first), ["svc:a"])
        self.assertEqual(first["next_page_token"], "o=1")

        query["page_token"] = first["next_page_token"]
        second = search(records, dict(query))
        self.assertEqual(_ids(second), ["svc:c"])
        self.assertEqual(second["next_page_token"], "")

    def test_ordering_is_stable_regardless_of_input_order(self):
        query = _query((-122.45, 37.75, -122.40, 37.80))
        forward = search(RegistryBackend(ANNOUNCES).records(), dict(query))
        reverse = search(RegistryBackend(list(reversed(ANNOUNCES))).records(), dict(query))
        self.assertEqual(_ids(forward), _ids(reverse))


class CacheLifecycle(unittest.TestCase):
    """A departed or expired service must not appear in search results."""

    def test_latest_announce_wins(self):
        cache = AnnounceCache()
        cache.admit(_announce("svc:a", SF, name="first"))
        cache.admit(_announce("svc:a", SF, name="second"))
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.get("svc:a").payload["name"], "second")

    def test_depart_removes_from_search(self):
        cache = AnnounceCache()
        cache.admit(_announce("svc:a", SF))
        cache.admit(_announce("svc:c", SF))
        query = _query((-122.45, 37.75, -122.40, 37.80))
        self.assertEqual(_ids(search(cache.records(), dict(query))), ["svc:a", "svc:c"])

        self.assertTrue(cache.depart("svc:a"))
        self.assertEqual(_ids(search(cache.records(), dict(query))), ["svc:c"])
        self.assertFalse(cache.depart("svc:a"))  # already gone

    def test_ttl_expiry_removes_from_search(self):
        cache = AnnounceCache()
        stale = SpatialDDSValidator.now_time()
        stale["sec"] -= 700  # > 2 x ttl_sec below
        cache.admit(_announce("svc:a", SF, ttl_sec=300, stamp=stale))
        cache.admit(_announce("svc:c", SF, ttl_sec=300))

        query = _query((-122.45, 37.75, -122.40, 37.80))
        self.assertEqual(_ids(search(cache.records(), dict(query))), ["svc:c"])
        self.assertEqual(cache.stats()["expired"], 1)

    def test_entry_without_ttl_is_kept(self):
        cache = AnnounceCache()
        announce = _announce("svc:a", SF)
        announce["ttl_sec"] = 0
        cache.admit(announce)
        self.assertEqual(len(cache.records()), 1)

    def test_malformed_announce_is_dropped_not_raised(self):
        cache = AnnounceCache()
        self.assertFalse(cache.admit({"service_id": "svc:x"}))  # no coverage
        self.assertFalse(cache.admit({"nonsense": True}))
        self.assertEqual(len(cache), 0)


class ServeOrSynthesize(unittest.TestCase):
    def test_authored_manifest_is_served_verbatim(self):
        authored = {"id": "spatialdds://x/zone:z/manifest:m", "profile": "spatial.manifest/1.7",
                    "rtype": "service", "service": {"service_id": "svc:a", "kind": "VPS"},
                    "marker": "authored"}
        records = RegistryBackend([_announce("svc:a", SF)]).records()
        response = search(records, _query(SF), manifest_provider=lambda r: authored)
        self.assertEqual(response["results"], [authored])

    def test_falls_back_to_synthesis_when_nothing_is_hosted(self):
        records = RegistryBackend([_announce("svc:a", SF)]).records()
        response = search(records, _query(SF), manifest_provider=lambda r: None)
        doc = response["results"][0]
        self.assertEqual(doc["service"]["service_id"], "svc:a")
        self.assertEqual(doc["profile"], "spatial.manifest/1.7")
        self.assertNotIn("marker", doc)

    def test_synthesis_omits_fields_the_announce_cannot_supply(self):
        announce = _announce("svc:a", SF)
        announce.pop("org")
        records = RegistryBackend([announce]).records()
        doc = search(records, _query(SF))["results"][0]
        self.assertNotIn("org", doc["service"])
        self.assertNotIn("transforms", doc)


class GeohashShorthand(unittest.TestCase):
    def test_known_geohash_bounds(self):
        # "9q8yy" covers part of San Francisco.
        west, south, east, north = geohash_bounds("9q8yy")
        self.assertTrue(-122.5 < west < -122.3, west)
        self.assertTrue(37.7 < south < 37.9, south)
        self.assertLess(west, east)
        self.assertLess(south, north)

    def test_geohash_query_finds_the_service_in_that_cell(self):
        records = RegistryBackend(ANNOUNCES).records()
        response = search(records, query_from_geohash("9q8y"))
        self.assertIn("svc:a", _ids(response))
        self.assertNotIn("svc:b", _ids(response))

    def test_invalid_geohash_is_rejected(self):
        for bad in ("", "abc!", "ail"):  # 'a', 'i', 'l' are not in the alphabet
            with self.assertRaises(DiscoveryError):
                geohash_bounds(bad)


if __name__ == "__main__":
    unittest.main()
