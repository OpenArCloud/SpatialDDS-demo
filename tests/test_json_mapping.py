"""
Typed sample <-> JSON, the seam that replaces the envelope.

The envelope put JSON on the bus. This puts JSON only at the edges that need
it — /ws clients, MQTT, MCAP, the HTTP discovery surface — so the browser-facing
contract can stay byte-identical while the bus carries real types.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo.json_mapping import from_json, to_json  # noqa: E402
from spatialdds_idl.builtin import Time  # noqa: E402
from spatialdds_idl.spatial.common import CoordConvention, FrameRef  # noqa: E402
from spatialdds_idl.spatial.core import Aabb3, CovMatrix, GeoPose  # noqa: E402
from spatialdds_idl.spatial.disco import (  # noqa: E402
    Announce, Capabilities, CoverageElement, CoverageFilter, CoverageQuery,
    Depart, ProfileSupport, ServiceKind, ServiceSummary, TopicMeta,
)

STAMP = Time(sec=1700000000, nanosec=0)
FRAME = FrameRef(uuid="u", fqn="earth-fixed", has_coord_convention=True,
                 coord_convention=CoordConvention.ENU)
ZERO = Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0])
COVERAGE = CoverageElement(
    has_crs=True, crs="EPSG:4979", has_bbox=True,
    bbox=[-122.52, 37.70, -122.35, 37.85], has_aabb=False, aabb=ZERO,
    _global=False, has_frame_ref=False, frame_ref=FRAME,
    has_coverage_window=False, coverage_window_start=STAMP, coverage_window_end=STAMP,
    # Added in 1.7's findings-batch-2 revision: a circular footprint is a
    # circle now rather than its bounding aabb.
    has_circle=False, circle_center=[0.0, 0.0, 0.0], circle_radius_m=0.0,
)
ANNOUNCE = Announce(
    service_id="svc:vps:demo/sf", name="MockVPS", kind=ServiceKind.VPS, version="1.7",
    org="ExampleOrg", hints=[],
    caps=Capabilities(
        supported_profiles=[ProfileSupport(name="spatial.core", major=1, min_minor=7, max_minor=7)],
        preferred_profiles=["spatial.core/1.7"], features=["blob.crc32"]),
    topics=[TopicMeta(name="spatialdds/vps/query/v1", type="vps_query", version="v1",
                      qos_profile="VPS_REQ", target_rate_hz=0.0, max_chunk_bytes=0)],
    coverage=[COVERAGE], coverage_frame_ref=FRAME, has_coverage_eval_time=False,
    coverage_eval_time=STAMP, transforms=[],
    manifest_uri="spatialdds://x/zone:z/manifest:m", auth_hint="", stamp=STAMP, ttl_sec=300,
    coverage_source_ids=[],
)

CASES = {
    "Time": STAMP,
    "FrameRef": FRAME,
    "CoverageElement": COVERAGE,
    "TopicMeta": ANNOUNCE.topics[0],
    "Announce": ANNOUNCE,
    "ServiceSummary": ServiceSummary(
        service_id="svc:a", kind=ServiceKind.VPS, name="MockVPS",
        manifest_uri="spatialdds://x/zone:z/manifest:m", coverage=[COVERAGE],
        coverage_frame_ref=FRAME, stamp=STAMP, ttl_sec=300),
    "CoverageQuery": CoverageQuery(
        query_id="q1", coverage=[COVERAGE], coverage_frame_ref=FRAME,
        has_coverage_eval_time=False, coverage_eval_time=STAMP, has_filter=True,
        filter=CoverageFilter(type_in=["vps_query"], qos_profile_in=[], module_id_in=[]),
        reply_topic="spatialdds/discovery/response/q1", stamp=STAMP, ttl_sec=60),
    "Depart": Depart(service_id="svc:a", stamp=STAMP),
    "GeoPose(COV_NONE)": GeoPose(lat_deg=37.7, lon_deg=-122.4, alt_m=15.0,
                                 q=[0.0, 0.0, 0.0, 1.0], stamp=STAMP, cov=CovMatrix(none=0)),
    "GeoPose(COV_POS3)": GeoPose(lat_deg=37.7, lon_deg=-122.4, alt_m=15.0,
                                 q=[0.0, 0.0, 0.0, 1.0], stamp=STAMP,
                                 cov=CovMatrix(pos=[1.0] * 9)),
}


class RoundTrip(unittest.TestCase):
    def test_every_case_round_trips(self):
        for name, value in CASES.items():
            with self.subTest(type=name):
                self.assertEqual(from_json(type(value), to_json(value)), value)

    def test_output_is_json_serialisable(self):
        for name, value in CASES.items():
            with self.subTest(type=name):
                json.dumps(to_json(value))


class SpecShape(unittest.TestCase):
    """What a browser sees has to be the spec's vocabulary, not Python's."""

    def test_enums_are_identifiers(self):
        """§2.8: JSON carries enum identifiers, not integers."""
        data = to_json(ANNOUNCE)
        self.assertEqual(data["kind"], "VPS")
        self.assertEqual(data["coverage_frame_ref"]["coord_convention"], "ENU")

    def test_keyword_fields_use_their_idl_names(self):
        """idlc renames `global` to `_global`; JSON must say `global`."""
        element = to_json(COVERAGE)
        self.assertIn("global", element)
        self.assertNotIn("_global", element)

    def test_unions_name_their_active_case(self):
        cov = to_json(GeoPose(lat_deg=0.0, lon_deg=0.0, alt_m=0.0, q=[0.0, 0.0, 0.0, 1.0],
                              stamp=STAMP, cov=CovMatrix(pos=[1.0] * 9)))["cov"]
        self.assertEqual(cov["discriminator"], "COV_POS3")
        self.assertIn("pos", cov)
        # cyclonedds' private bookkeeping must not leak into the wire format.
        self.assertFalse([k for k in cov if k.startswith("_")])

    def test_missing_fields_raise_rather_than_defaulting(self):
        data = to_json(COVERAGE)
        del data["has_bbox"]
        with self.assertRaises(ValueError):
            from_json(CoverageElement, data)


if __name__ == "__main__":
    unittest.main()
