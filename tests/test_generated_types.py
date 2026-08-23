"""
Tests for the generated SpatialDDS types and the generator that produces them.

Three jobs:

1. The checked-in output under ``spatialdds_idl/`` is importable and
   round-trips — no idlc needed, so this runs anywhere.
2. Keys and wire names survive the generator's post-processing, which is what
   makes typed topics behave per the spec rather than merely compile.
3. A drift gate: re-running the generator must reproduce the checked-in tree
   byte for byte. Needs idlc, so it skips loudly rather than passing quietly
   when idlc is absent.
"""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_idl._field_aliases import FIELD_ALIASES, wire_name  # noqa: E402
from spatialdds_idl.builtin import Time  # noqa: E402
from spatialdds_idl.oarc_demo import BootstrapQuery, BootstrapResponse  # noqa: E402
from spatialdds_idl.spatial.common import CoordConvention, FrameRef  # noqa: E402
from spatialdds_idl.spatial.core import (  # noqa: E402
    Aabb3, CovMatrix, FramedPose, GeoPose, PoseSE3,
)
from spatialdds_idl.spatial.disco import (  # noqa: E402
    Announce, Capabilities, CoverageElement, CoverageFilter, CoverageQuery,
    CoverageResponse, Depart, ProfileSupport, ServiceKind, ServiceSummary,
    TopicMeta,
)


def _idlc() -> str | None:
    idlc = os.environ.get("IDLC_PATH") or shutil.which("idlc")
    if idlc:
        return idlc
    for candidate in ("/usr/local/bin/idlc", "/opt/homebrew/bin/idlc"):
        if os.path.exists(candidate):
            return candidate
    return None


STAMP = Time(sec=1700000000, nanosec=0)
FRAME = FrameRef(uuid="f-uuid", fqn="earth-fixed",
                 has_coord_convention=True, coord_convention=CoordConvention.ENU)
ZERO_AABB = Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0])
COVERAGE = CoverageElement(
    has_crs=True, crs="EPSG:4979", has_bbox=True,
    bbox=[-122.52, 37.70, -122.35, 37.85], has_aabb=False, aabb=ZERO_AABB,
    _global=False, has_frame_ref=False, frame_ref=FRAME,
    has_coverage_window=False, coverage_window_start=STAMP, coverage_window_end=STAMP,
)
TOPIC = TopicMeta(name="spatialdds/vps/query/v1", type="vps_query", version="v1",
                  qos_profile="VPS_REQ", target_rate_hz=0.0, max_chunk_bytes=0)
CAPS = Capabilities(
    supported_profiles=[ProfileSupport(name="spatial.core", major=1, min_minor=7, max_minor=7)],
    preferred_profiles=["spatial.core/1.7"], features=["blob.crc32"],
)
ANNOUNCE = Announce(
    service_id="svc:vps:demo/sf-downtown", name="MockVPS", kind=ServiceKind.VPS,
    version="1.7", org="ExampleOrg", hints=[], caps=CAPS, topics=[TOPIC],
    coverage=[COVERAGE], coverage_frame_ref=FRAME, has_coverage_eval_time=False,
    coverage_eval_time=STAMP, transforms=[],
    manifest_uri="spatialdds://vps.example.com/zone:sf-downtown/manifest:vps",
    auth_hint="", stamp=STAMP, ttl_sec=300,
)
SUMMARY = ServiceSummary(
    service_id="svc:vps:demo/sf-downtown", kind=ServiceKind.VPS, name="MockVPS",
    manifest_uri="spatialdds://vps.example.com/zone:sf-downtown/manifest:vps",
    coverage=[COVERAGE], coverage_frame_ref=FRAME, stamp=STAMP, ttl_sec=300,
)

ROUND_TRIP_CASES = {
    "GeoPose": GeoPose(lat_deg=37.7749, lon_deg=-122.4194, alt_m=15.0,
                       q=[0.0, 0.0, 0.0, 1.0], stamp=STAMP, cov=CovMatrix(none=0)),
    "FramedPose": FramedPose(
        pose=PoseSE3(t=[1.0, 2.0, 3.0], q=[0.0, 0.0, 0.0, 1.0]),
        frame_ref=FRAME, cov=CovMatrix(none=0), stamp=STAMP,
    ),
    "CoverageElement": COVERAGE,
    "TopicMeta": TOPIC,
    "Announce": ANNOUNCE,
    "ServiceSummary": SUMMARY,
    "CoverageQuery": CoverageQuery(
        query_id="q-1", coverage=[COVERAGE], coverage_frame_ref=FRAME,
        has_coverage_eval_time=False, coverage_eval_time=STAMP, has_filter=True,
        filter=CoverageFilter(type_in=["vps_query"], qos_profile_in=[], module_id_in=[]),
        reply_topic="spatialdds/discovery/response/q-1", stamp=STAMP, ttl_sec=60),
    "CoverageResponse": CoverageResponse(query_id="q-1", results=[SUMMARY],
                                         next_page_token=""),
    "Depart": Depart(service_id="svc:vps:demo/sf-downtown", stamp=STAMP),
    "BootstrapQuery": BootstrapQuery(client_id="c1", client_kind="robot",
                                     capabilities=["localize", "catalog"],
                                     location_hint="sf-downtown", stamp=STAMP),
    "BootstrapResponse": BootstrapResponse(
        client_id="c1", spatialdds_bootstrap="1.7", dds_domain=1,
        cyclonedds_profile="", manifest_uris=["spatialdds://x/zone:z/manifest:m"],
        ttl_sec=300, stamp=STAMP),
}


class RoundTrip(unittest.TestCase):
    def test_every_case_survives_serialize_deserialize(self):
        for name, value in ROUND_TRIP_CASES.items():
            with self.subTest(type=name):
                payload = value.serialize()
                self.assertGreater(len(payload), 0)
                self.assertEqual(type(value).deserialize(payload), value)

    def test_enums_are_real_enums(self):
        """§2.8: enums are identifiers, not magic integers."""
        self.assertEqual(ANNOUNCE.kind, ServiceKind.VPS)
        self.assertEqual(ServiceKind.VPS.name, "VPS")
        self.assertEqual(FRAME.coord_convention, CoordConvention.ENU)


class KeysAndNames(unittest.TestCase):
    """The properties that make typed topics behave, not merely compile."""

    def test_spec_keyed_types_are_keyed(self):
        # These are what per-instance lifecycle (dispose, late-join backfill)
        # depends on. The demo's old envelope had no key at all.
        for cls in (Announce, Depart, CoverageQuery):
            with self.subTest(type=cls.__name__):
                self.assertFalse(cls.__idl__.keyless, f"{cls.__name__} lost its @key")

    def test_demo_types_are_keyed(self):
        for cls in (BootstrapQuery, BootstrapResponse):
            with self.subTest(type=cls.__name__):
                self.assertFalse(cls.__idl__.keyless)

    def test_python_keyword_fields_keep_their_wire_names(self):
        """
        The spec uses `global` and `from` as field names, which are Python
        keywords. idlc >= 11 emits them as `_global` / `_from`; the generator
        records the mapping so JSON-facing code can still emit the spec's name.
        """
        self.assertEqual(wire_name("spatial.disco.CoverageElement", "_global"), "global")
        self.assertEqual(wire_name("spatial.disco.Transform", "_from"), "from")
        self.assertIn("spatial.disco.ValidityWindow", FIELD_ALIASES)
        # Unrenamed fields pass through untouched.
        self.assertEqual(wire_name("spatial.disco.CoverageElement", "has_bbox"), "has_bbox")
        # And the renamed attribute really is what the dataclass exposes.
        self.assertFalse(COVERAGE._global)

    def test_typenames_are_the_spec_names(self):
        """Nesting under spatialdds_idl must not leak into DDS type identity."""
        self.assertEqual(Announce.__idl__.idl_transformed_typename, "spatial::disco::Announce")
        self.assertEqual(GeoPose.__idl__.idl_transformed_typename, "spatial::core::GeoPose")


class GeneratorDrift(unittest.TestCase):
    def test_checked_in_output_matches_a_fresh_run(self):
        idlc = _idlc()
        if not idlc:
            raise unittest.SkipTest(
                "IDLC-MISSING: idlc not on PATH, so generated-type drift is "
                "unverified here. Run in the demo image: docker run --rm "
                '-v "$PWD:/repo" -w /repo cyclonedds-python '
                "python3 -m pytest tests/test_generated_types.py"
            )
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "generate_types.py"), "--check"],
            capture_output=True, text=True, cwd=REPO,
        )
        if result.returncode == 2:
            raise unittest.SkipTest(
                "IDLC-VERSION-MISMATCH: local idlc is not the canonical "
                f"toolchain, so drift is unverified here.\n{result.stdout.strip()}"
            )
        self.assertEqual(result.returncode, 0,
                         f"generated types are stale:\n{result.stdout}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
