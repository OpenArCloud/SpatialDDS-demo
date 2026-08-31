"""
The AR demo's services and client, on the host, without a bus.

`ar_demo/` had no `test_*.py` at all. Its only coverage was two scripts that
are not pytest modules and are not in the canonical run, and neither
`spatialdds_demo_client.py` nor `spatialdds_bootstrap_server.py` was
referenced by any test. That is the same shape as the ROS 2 bridge node, where
two undefined names survived six days behind a green suite.

These modules defer nothing — they import cyclonedds at module scope — but
`require_dds_env` is called inside `run_*`, not at import, so their pure
helpers are reachable on the host. Those helpers are where the decisions live:
what a service announces about itself, whether an announce is still good, and
which fields a compact summary may carry.
"""

from __future__ import annotations

import hashlib
import math
import os
import sys
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for _p in (str(_HERE), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import spatialdds_bootstrap_server as bootstrap  # noqa: E402
import spatialdds_catalog_server as catalog  # noqa: E402
import spatialdds_demo_client as client  # noqa: E402
import spatialdds_demo_server as vps  # noqa: E402
from spatialdds_demo.discovery_bus import validate_announce  # noqa: E402
from spatialdds_demo.json_mapping import from_json  # noqa: E402
from spatialdds_demo.topics import (  # noqa: E402
    TOPIC_CATALOG_QUERY_V1, TOPIC_VPS_QUERY_V1,
)
from spatialdds_idl.spatial.disco import Announce  # noqa: E402
from spatialdds_validation import SpatialDDSValidator  # noqa: E402

SEED = _HERE / "catalog_seed.json"



FOUNTAIN_SEED = _REPO / "bridges" / "web_bridge" / "tests" / "catalog_seed_fountain.json"
FOUNTAIN_ANCHORS = _REPO / "bridges" / "web_bridge" / "tests" / "frame_anchors_fountain.json"
DUCK = _REPO / "web" / "public" / "models" / "duck.glb"


class PlacedContent(unittest.TestCase):
    """
    The fountain catalogue places content, rather than implying a position.

    Its first version leaned on coverage for both jobs: the bbox centre for
    latitude and longitude, and an `aabb` for height. The aabb is metres in the
    declared frame, so writing degrees into it was wrong in proportion to the
    distance from null island, and wrong without anything failing. Placement
    now lives in the row's pose, resolved through the transform its service
    announces.
    """

    def test_coverage_carries_no_volume(self):
        for entry in catalog._load_seed(str(FOUNTAIN_SEED)):
            for element in entry["coverage"]:
                self.assertFalse(
                    element["has_aabb"],
                    "coverage is a search key; a geographic aabb holds degrees "
                    "in a metres field",
                )

    def test_an_unused_frame_ref_looks_unused(self):
        # A flagged-off member still rides the wire. Filling it in stamped
        # `coord_convention: ENU` on an `earth-fixed` fqn, which is the
        # conflation 1.7 deleted `frame_kind` to kill.
        for entry in catalog._load_seed(str(FOUNTAIN_SEED)):
            for element in entry["coverage"]:
                if not element["has_frame_ref"]:
                    self.assertEqual(element["frame_ref"]["fqn"], "")
                    self.assertFalse(element["frame_ref"]["has_coord_convention"])

    def test_the_pose_resolves_to_the_water(self):
        os.environ["SPATIALDDS_FRAME_ANCHORS"] = str(FOUNTAIN_ANCHORS)
        try:
            dataset = catalog._load_seed(str(FOUNTAIN_SEED))
            transforms = catalog._frame_transforms(dataset)
        finally:
            os.environ.pop("SPATIALDDS_FRAME_ANCHORS", None)
        self.assertEqual(len(transforms), 1)
        transform = transforms[0]
        entry = dataset[0]
        # The row's frame and the announced transform's source must be the
        # same frame, or a consumer has a pose and no way to place it.
        self.assertEqual(transform["from"]["uuid"], entry["frame_ref"]["uuid"])

        qx, qy, qz, qw = transform["pose"]["q"]
        tx, ty, tz = transform["pose"]["t"]
        vx, vy, vz = entry["pose"]["t"]
        r = [[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
             [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
             [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]]
        x = r[0][0] * vx + r[0][1] * vy + r[0][2] * vz + tx
        y = r[1][0] * vx + r[1][1] * vy + r[1][2] * vz + ty
        z = r[2][0] * vx + r[2][1] * vy + r[2][2] * vz + tz

        a, f = 6378137.0, 1.0 / 298.257223563
        e2 = f * (2.0 - f)
        pxy = math.hypot(x, y)
        lat = math.atan2(z, pxy * (1.0 - e2))
        height = 0.0
        for _ in range(8):
            n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
            height = pxy / math.cos(lat) - n
            lat = math.atan2(z, pxy * (1.0 - e2 * n / (n + height)))

        # The fountain's water surface, measured off the photorealistic tiles.
        self.assertAlmostEqual(height, 143.66, places=1)
        self.assertAlmostEqual(math.degrees(lat), 30.28384, places=4)
        self.assertAlmostEqual(math.degrees(math.atan2(y, x)), -97.73963, places=4)

    def test_the_declared_hash_is_the_shipped_asset(self):
        # An integrity hash that does not match is worse than none: it says
        # the bytes were checked.
        entry = catalog._load_seed(str(FOUNTAIN_SEED))[0]
        digest = hashlib.sha256(DUCK.read_bytes()).hexdigest()
        self.assertEqual(entry["asset"]["hash"], f"sha256:{digest}")
        self.assertEqual(entry["asset"]["media_type"], "model/gltf-binary")

    def test_the_wire_uri_is_absolute(self):
        os.environ["SPATIALDDS_ASSET_BASE"] = "https://example.test/ar"
        try:
            entry = catalog._load_seed(str(FOUNTAIN_SEED))[0]
        finally:
            os.environ.pop("SPATIALDDS_ASSET_BASE", None)
        self.assertEqual(entry["asset"]["uri"],
                         "https://example.test/ar/models/duck.glb")


class BootstrapConfig(unittest.TestCase):
    """What a fresh device is told when it asks how to join."""

    def test_manifest_list_splits_and_trims(self):
        self.assertEqual(bootstrap._manifest_list(" a , b ,, c "), ["a", "b", "c"])

    def test_no_manifests_is_an_empty_list_not_a_list_with_nothing_in_it(self):
        # An empty string must not become [""], which a consumer would try to
        # resolve as a manifest URI.
        self.assertEqual(bootstrap._manifest_list(""), [])
        self.assertEqual(bootstrap._manifest_list("   "), [])

    def test_an_unnamed_site_gets_a_name(self):
        mapping = bootstrap._mapping_for_site("", 7, [])
        self.assertEqual(mapping["site"], "default")
        self.assertEqual(mapping["dds_domain"], 7)


class AnnounceFreshness(unittest.TestCase):
    """
    The client's TTL check, which decides whether a discovered service is
    still worth using.

    It fails *open* — a missing or unreadable stamp counts as fresh — because
    dropping a service over metadata the demo did not populate is worse than
    briefly trusting a stale one. That is a deliberate choice, so it is pinned
    here rather than left to be rediscovered.
    """

    def _announce(self, age_sec: float, ttl_sec: int = 300):
        stamp_sec = time.time() - age_sec
        return {"ttl_sec": ttl_sec,
                "stamp": {"sec": int(stamp_sec), "nanosec": 0}}

    def test_a_recent_announce_is_fresh(self):
        self.assertTrue(client._announce_fresh(self._announce(10)))

    def test_the_backstop_is_twice_the_ttl(self):
        # Matches the HTTP cache's TTL_BACKSTOP_MULTIPLIER, so a service
        # leaves the bus and the cache in the same order everywhere.
        self.assertTrue(client._announce_fresh(self._announce(590, ttl_sec=300)))
        self.assertFalse(client._announce_fresh(self._announce(700, ttl_sec=300)))

    def test_missing_metadata_fails_open(self):
        for announce in ({}, {"ttl_sec": 300}, {"stamp": {"sec": 1, "nanosec": 0}},
                         {"ttl_sec": 300, "stamp": "not-a-time"}):
            with self.subTest(announce=announce):
                self.assertTrue(client._announce_fresh(announce))


class TopicProvenance(unittest.TestCase):
    """
    Whether a topic name came from a manifest, the spec, or a fallback.

    Both the client and the VPS server carry this, and they must agree — the
    demo's logs label every message with it.
    """

    def test_a_manifest_topic_is_labelled_as_one(self):
        manifest_topics = {"vps_query": TOPIC_VPS_QUERY_V1}
        for module in (client, vps):
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    module._topic_source_for(manifest_topics, "vps_query",
                                             TOPIC_VPS_QUERY_V1),
                    "manifest")

    def test_a_vps_topic_absent_from_the_manifest_is_a_fallback(self):
        for module in (client, vps):
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    module._topic_source_for({}, "vps_query", TOPIC_VPS_QUERY_V1),
                    "fallback")

    def test_anything_else_is_the_spec(self):
        for module in (client, vps):
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    module._topic_source_for({}, "catalog", TOPIC_CATALOG_QUERY_V1),
                    "spec")


class CatalogueSeed(unittest.TestCase):
    """
    The authored catalogue, and the announce derived from it.

    The seed omits presence-flagged fields a human does not care about, so it
    stopped building into a `CatalogResponse` when 1.7 added the circle members
    and nobody noticed for three revisions. Completing at load is what keeps
    the seed a description of coverage rather than a transcription of the
    current struct layout.
    """

    def test_the_seed_loads_and_its_coverage_is_complete(self):
        dataset = catalog._load_seed(str(SEED))
        self.assertTrue(dataset)
        for entry in dataset:
            for element in entry["coverage"]:
                # Every presence flag the IDL defines, present whatever its value.
                for flag in ("has_bbox", "has_aabb", "has_circle", "has_crs",
                             "has_frame_ref", "has_coverage_window"):
                    self.assertIn(flag, element, entry["content_id"])

    def test_coverage_is_derived_from_the_data_not_configured_beside_it(self):
        dataset = catalog._load_seed(str(SEED))
        coverage = catalog._seed_coverage(dataset)
        self.assertEqual(len(coverage), 1)
        west, south, east, north = coverage[0]["bbox"]
        self.assertLess(west, east)
        self.assertLess(south, north)
        # Every entry the catalogue serves falls inside what it advertises.
        for entry in dataset:
            for element in entry["coverage"]:
                if not element.get("has_bbox"):
                    continue
                w, s, e, n = element["bbox"]
                self.assertGreaterEqual(w, west)
                self.assertLessEqual(e, east)
                self.assertGreaterEqual(s, south)
                self.assertLessEqual(n, north)

    def test_a_seed_with_no_bbox_announces_global_rather_than_nonsense(self):
        coverage = catalog._seed_coverage([{"coverage": []}])
        self.assertTrue(coverage[0]["global"])

    def test_the_announce_is_a_real_one(self):
        announce = catalog._catalog_announce(catalog._load_seed(str(SEED)))
        # Builds into the type and passes the rules this repo enforces at the
        # writer — the check that used to exist only in tests.
        validate_announce(from_json(Announce, announce))
        self.assertEqual(announce["kind"], "CONTENT")

    def test_it_advertises_the_query_lane_only(self):
        """
        Responses go to the `reply_topic` each query names, chosen by the
        client, so no announcement can advertise them.
        """
        announce = catalog._catalog_announce(catalog._load_seed(str(SEED)))
        self.assertEqual([t["name"] for t in announce["topics"]],
                         [TOPIC_CATALOG_QUERY_V1])


class ServiceSummaryShape(unittest.TestCase):
    """
    1.7 made `CoverageResponse` return compact rows. Inlining topics or caps
    there is refused, and a responder that does it is answering with a payload
    consumers are told not to read.
    """

    def test_the_summary_passes_the_1_7_rules(self):
        announce = catalog._catalog_announce(catalog._load_seed(str(SEED)))
        summary = catalog._service_summary(announce)
        SpatialDDSValidator.validate_service_summary(summary)

    def test_it_carries_no_topics_or_caps(self):
        announce = catalog._catalog_announce(catalog._load_seed(str(SEED)))
        summary = catalog._service_summary(announce)
        for absent in ("topics", "caps", "transforms"):
            self.assertNotIn(absent, summary)
        # But it does carry what a consumer needs to decide and then resolve.
        for present in ("service_id", "kind", "manifest_uri", "coverage"):
            self.assertIn(present, summary)


if __name__ == "__main__":
    unittest.main()
