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
    complete_coverage_element,
    create_coverage_bbox_earth_fixed,
)

SF = (-122.52, 37.70, -122.35, 37.85)
AUSTIN = (-97.75, 30.27, -97.72, 30.29)


def _local_frame(fqn):
    return {"uuid": "6f1d0e9c-0b3a-4a3f-9f4e-2f1a5c7d8e90", "fqn": fqn,
            "has_coord_convention": True, "coord_convention": "ENU"}


def _aabb_element(min_x, min_y, max_x, max_y):
    """A local volume, in metres. In CoverageElement since 1.4."""
    return complete_coverage_element(
        has_aabb=True,
        aabb={"min_xyz": [min_x, min_y, 0.0], "max_xyz": [max_x, max_y, 0.0]})


def _circle_element(center_x, center_y, radius_m):
    """Centre and radius, added by 1.7's findings-batch-2 revision."""
    return complete_coverage_element(
        has_circle=True, circle_center=[center_x, center_y, 0.0],
        circle_radius_m=radius_m)


def _geo_circle_element(lon, lat, radius_m):
    """A circle in an earth-fixed frame: centre in degrees, radius in metres."""
    return complete_coverage_element(
        has_crs=True, crs="EPSG:4979",
        has_circle=True, circle_center=[lon, lat, 0.0], circle_radius_m=radius_m)


def _announce(service_id, bbox=None, *, kind="VPS", name="Svc", topics=None, caps=None,
              ttl_sec=300, stamp=None, manifest_uri=None,
              coverage=None, coverage_frame_ref=None):
    if coverage is None:
        frame_ref, element = create_coverage_bbox_earth_fixed(*bbox)
        coverage, coverage_frame_ref = [element], frame_ref
    frame_ref = coverage_frame_ref
    element = coverage[0]
    return {
        "service_id": service_id,
        "name": name,
        "kind": kind,
        "org": "ExampleOrg",
        "coverage": list(coverage),
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


def _local_query(element, fqn="scene/intersection", **extra):
    """A query in a local metric frame, which is where aabb footprints live."""
    query = {"coverage": [element], "coverage_frame_ref": _local_frame(fqn)}
    query.update(extra)
    return query


# The frame the aabb/circle services declare. Local metres, so an earth-fixed
# query cannot reach them and a query in this frame can — see
# tests/test_coverage_model.py for why that is the intended answer.
INTERSECTION = _local_frame("scene/intersection")

# The shared table both backends must answer identically.
ANNOUNCES = [
    _announce("svc:a", SF, kind="VPS", name="SF VPS"),
    _announce("svc:b", AUSTIN, kind="VPS", name="Austin VPS"),
    _announce("svc:c", SF, kind="CONTENT", name="SF Content",
              topics=[{"name": "spatialdds/x/tile/v1", "type": "geometry_tile",
                       "version": "v1", "qos_profile": "GEOM_TILE"}]),
    # Geometry other than bbox. Every one of these matched nothing until the
    # predicate became the §3.3.4 model.
    _announce("svc:d", kind="SENSING", name="Intersection radar",
              coverage=[_circle_element(0.0, 0.0, 120.0)],
              coverage_frame_ref=INTERSECTION),
    _announce("svc:e", kind="MAPPING", name="Intersection map",
              coverage=[_aabb_element(-200.0, -200.0, -50.0, -50.0)],
              coverage_frame_ref=INTERSECTION),
    _announce("svc:f", kind="SENSING", name="SF roadside unit",
              coverage=[_geo_circle_element(-122.4194, 37.7749, 400.0)],
              coverage_frame_ref=create_coverage_bbox_earth_fixed(0, 0, 0, 0)[0]),
]

CASES = [
    ("sf bbox matches sf services",
     _query((-122.45, 37.75, -122.40, 37.80)), ["svc:a", "svc:c", "svc:f"]),
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
     ["svc:a", "svc:c", "svc:f"]),
    ("empty filter arrays match all",
     _query((-122.45, 37.75, -122.40, 37.80),
            has_filter=True,
            filter={"type_in": [], "qos_profile_in": [], "module_id_in": []}),
     ["svc:a", "svc:c", "svc:f"]),
    ("max_results pages",
     _query((-122.45, 37.75, -122.40, 37.80), max_results=1), ["svc:a"]),

    # --- geometry beyond bbox (W.1) -----------------------------------------
    ("a local circle is found by a query in its own frame",
     _local_query(_aabb_element(-10.0, -10.0, 10.0, 10.0)), ["svc:d"]),
    ("a local aabb is found by a query in its own frame",
     _local_query(_aabb_element(-210.0, -210.0, -190.0, -190.0)), ["svc:e"]),
    ("a query spanning both local footprints finds both",
     _local_query(_aabb_element(-300.0, -300.0, 300.0, 300.0)), ["svc:d", "svc:e"]),
    ("a local query outside both footprints finds neither",
     _local_query(_aabb_element(5000.0, 5000.0, 5100.0, 5100.0)), []),
    ("a circle is approximated by its bounding box",
     # Clips the corner of svc:d's bounding box while missing the circle
     # itself. §3.3.4 permits the approximation explicitly.
     _local_query(_aabb_element(119.0, 119.0, 140.0, 140.0)), ["svc:d"]),
    ("an earth-fixed query cannot reach a local frame",
     _query((-180.0, -90.0, 180.0, 90.0)), ["svc:a", "svc:b", "svc:c", "svc:f"]),
    ("a geographic circle is found by a bbox over it",
     # Narrowed by kind so the case is about the circle rather than about the
     # bbox services that also cover downtown SF.
     _query((-122.4195, 37.7748, -122.4193, 37.7750), kind=["SENSING"]), ["svc:f"]),
    ("a geographic circle's radius is metres, not degrees",
     # ~4 km east of svc:f: outside a 400 m footprint. Read as degrees the
     # footprint would span the planet and match this.
     _query((-122.375, 37.7748, -122.374, 37.7750), kind=["SENSING"]), []),

    # --- the binding's own request forms (W.2) ------------------------------
    ("the spec's minimal example: geohash alone",
     {"geohash": "9q8y"}, ["svc:a", "svc:c", "svc:f"]),
    ("geohash widens a coverage block rather than replacing it",
     _query(AUSTIN, geohash="9q8y"), ["svc:a", "svc:b", "svc:c", "svc:f"]),
    ("the spec's full example: no coverage_frame_ref, no presence flags",
     {"coverage": [{"crs": "EPSG:4326", "bbox": [-122.45, 37.75, -122.40, 37.80]}],
      "kind": ["VPS"],
      "filter": {"type_in": ["vps_query"], "qos_profile_in": [], "module_id_in": []},
      "max_results": 10},
     ["svc:a"]),
    ("an explicit has_bbox=false is honoured, not inferred over",
     {"coverage": [{"crs": "EPSG:4326", "has_bbox": False,
                    "bbox": [-122.45, 37.75, -122.40, 37.80],
                    "global": True}]},
     ["svc:a", "svc:b", "svc:c", "svc:d", "svc:e", "svc:f"]),
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
        """
        A malformed body is the caller's problem — 400, and the same 400 from
        either server. §3.3.0's error table maps every one of these to 400.
        """
        bad = [
            ("expr removed in 1.7", _query(SF, expr='kind=="VPS"')),
            ("neither coverage nor geohash",
             {"coverage_frame_ref": {"uuid": "u", "fqn": "earth-fixed"}}),
            ("empty coverage array", {"coverage": []}),
            ("body is not an object", ["not", "an", "object"]),
            ("coverage is not an array", {"coverage": {"bbox": [0, 0, 1, 1]}}),
            ("coverage element is not an object", {"coverage": ["nope"]}),
            ("geohash is not a string", {"geohash": 9}),
            ("geohash has characters outside the alphabet", {"geohash": "9q8yy!"}),
            ("element declares no geometry",
             {"coverage": [complete_coverage_element()]}),
            ("CoverageElement.type removed in 1.7",
             _query(SF, coverage=[dict(create_coverage_bbox_earth_fixed(*SF)[1],
                                       type="bbox")])),
            ("non-finite bbox",
             {"coverage": [{"crs": "EPSG:4979", "bbox": [float("inf"), 0, 1, 1]}]}),
            ("circle with a negative radius",
             {"coverage": [_circle_element(0.0, 0.0, -1.0)],
              "coverage_frame_ref": INTERSECTION}),
            ("max_results is not an integer", _query(SF, max_results="ten")),
            ("max_results is negative", _query(SF, max_results=-1)),
        ]
        for label, query in bad:
            for backend_cls in BACKENDS:
                with self.subTest(case=label, backend=backend_cls.name):
                    with self.assertRaises(DiscoveryError):
                        search(backend_cls(ANNOUNCES).records(),
                               dict(query) if isinstance(query, dict) else query)

    def test_duplicate_service_ids_collapse_to_one_result(self):
        """
        Latest-wins is the registry's and the cache's job; search dedupes
        anyway, because a gateway fed by two announce readers can legitimately
        hold the same service twice and must not report it twice.
        """
        twice = [_announce("svc:a", SF, name="first"), _announce("svc:a", SF, name="second")]
        for backend_cls in BACKENDS:
            with self.subTest(backend=backend_cls.name):
                response = search(backend_cls(twice).records(),
                                  _query((-122.45, 37.75, -122.40, 37.80)))
                self.assertEqual(_ids(response), ["svc:a"])


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
        self.assertEqual(second["next_page_token"], "o=2")

        query["page_token"] = second["next_page_token"]
        last = search(records, dict(query))
        self.assertEqual(_ids(last), ["svc:f"])
        self.assertEqual(last["next_page_token"], "",
                         "the final page must not offer another")

    def test_max_results_zero_returns_nothing_and_no_token(self):
        """
        Unset means server-defined; an explicit zero asked for none. A token
        here would advance the offset by nothing and hand the client a loop.
        """
        records = RegistryBackend(ANNOUNCES).records()
        response = search(records, _query((-122.45, 37.75, -122.40, 37.80), max_results=0))
        self.assertEqual(response["results"], [])
        self.assertEqual(response["next_page_token"], "")

    def test_page_token_past_the_end_is_an_empty_final_page(self):
        records = RegistryBackend(ANNOUNCES).records()
        response = search(records, _query((-122.45, 37.75, -122.40, 37.80),
                                          max_results=2, page_token="o=99"))
        self.assertEqual(response["results"], [])
        self.assertEqual(response["next_page_token"], "")

    def test_max_results_larger_than_the_result_set_offers_no_token(self):
        records = RegistryBackend(ANNOUNCES).records()
        response = search(records, _query((-122.45, 37.75, -122.40, 37.80), max_results=99))
        self.assertEqual(_ids(response), ["svc:a", "svc:c", "svc:f"])
        self.assertEqual(response["next_page_token"], "")

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
        """A service that stops announcing goes stale on schedule."""
        cache = AnnounceCache()
        cache.admit(_announce("svc:a", SF, ttl_sec=300))
        cache.admit(_announce("svc:c", SF, ttl_sec=300))

        query = _query((-122.45, 37.75, -122.40, 37.80))
        self.assertEqual(sorted(_ids(search(cache.records(), dict(query))))
                         , ["svc:a", "svc:c"])

        # 700s later, past 2 x ttl_sec, with nothing having re-announced.
        later = time.time() + 700
        self.assertEqual(_ids(search(cache.records(now=later), dict(query))), [])
        self.assertEqual(cache.stats()["expired"], 2)

    def test_a_frozen_stamp_does_not_expire_a_service_that_keeps_announcing(self):
        """
        Re-announcing is evidence of life even when the stamp does not move.

        A publisher may re-announce by re-writing a sample it built once: the
        DDS Lifespan is refreshed, so the sample stays valid on the wire, while
        the payload stamp stays frozen at first build. OpenVPS's binding does
        this. Judged on the stamp alone the service is expired the moment it
        arrives — measured on AWS, its announce was admitted and swept in the
        same sweep, and discovery reported an empty deployment while the
        localizer was answering requests.
        """
        cache = AnnounceCache()
        frozen = SpatialDDSValidator.now_time()
        frozen["sec"] -= 700  # older than 2 x ttl_sec, and never updated
        cache.admit(_announce("svc:a", SF, ttl_sec=300, stamp=frozen))

        query = _query((-122.45, 37.75, -122.40, 37.80))
        self.assertEqual(_ids(search(cache.records(), dict(query))), ["svc:a"])
        self.assertEqual(cache.stats()["expired"], 0)

        # Still nothing new 700s on: now it is genuinely stale.
        later = time.time() + 700
        self.assertEqual(_ids(search(cache.records(now=later), dict(query))), [])

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
