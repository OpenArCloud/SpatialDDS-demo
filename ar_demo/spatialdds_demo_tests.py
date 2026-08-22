#!/usr/bin/env python3
import json
import os
import subprocess
import sys

from spatialdds_demo.manifest_resolver import resolve_manifest
from spatialdds_demo.topics import (
    TOPIC_VPS_QUERY_V1,
    TOPIC_VPS_RESULT_V1,
    validate_topic_meta,
    validate_topics_are_canonical,
)
from spatialdds_test import SpatialDDSLogger, VPSServiceV15
from spatialdds_validation import SpatialDDSValidator, create_coverage_bbox_earth_fixed


def test_manifest_resolver() -> bool:
    manifest, status = resolve_manifest("spatialdds://vps.example.com/zone:sf-downtown/manifest:vps")
    service = manifest.get("service", {}) if isinstance(manifest, dict) else {}
    return (
        manifest is not None
        and service.get("service_id") == "svc:vps:demo/sf-downtown"
        and status.get("mode") == "LOCAL"
    )


def test_topic_validator() -> bool:
    ok, _ = validate_topics_are_canonical(
        [TOPIC_VPS_QUERY_V1, TOPIC_VPS_RESULT_V1],
        service_kind="VPS",
    )
    bad, errors = validate_topics_are_canonical(
        ["vps/localize/request/v1", "spatialdds/vps/localize/response"],
        service_kind="VPS",
    )
    return ok and (not bad) and len(errors) >= 1


def test_demo_output() -> bool:
    env = os.environ.copy()
    env["SLIDE_MODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "spatialdds_test", "--summary-only"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    output = result.stdout + result.stderr
    required = [
        "manifest_resolver: LOCAL",
        "manifest_loaded: yes",
        "topic=spatialdds/vps/query/v1",
        "topic=spatialdds/vps/result/v1",
        "topic=spatialdds/discovery/response/",
        "topic=spatialdds/anchors/",
        "topic=spatialdds/catalog/query/v1",
        "topic=spatialdds/catalog/replies/",
        "topic_source=manifest",
    ]
    return result.returncode == 0 and all(item in output for item in required)


def test_manifest_fallback() -> bool:
    """
    An unresolvable manifest_uri must fall back to the spec default topics.

    Uses an unmapped spatialdds:// URI rather than an https:// one: 1.7 types
    Announce.manifest_uri / ServiceSummary.manifest_uri as SpatialUri, so an
    https URI would make the announce itself non-conformant. The HTTPS-disabled
    guard is covered directly by test_https_resolution_disabled below.
    """
    env = os.environ.copy()
    env["SLIDE_MODE"] = "1"
    env["SPATIALDDS_DEMO_MANIFEST_URI"] = (
        "spatialdds://vps.example.com/zone:not-mapped/manifest:vps"
    )
    result = subprocess.run(
        [sys.executable, "-m", "spatialdds_test", "--summary-only"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    output = result.stdout + result.stderr
    required = [
        "manifest_resolver: LOCAL_MISSING",
        "manifest_loaded: no",
        "topic=spatialdds/vps/query/v1",
        "topic=spatialdds/vps/result/v1",
        "topic_source=fallback",
    ]
    return result.returncode == 0 and all(item in output for item in required)


def test_https_resolution_disabled() -> bool:
    """HTTPS manifest resolution stays opt-in behind ALLOW_HTTPS=1."""
    os.environ.pop("ALLOW_HTTPS", None)
    manifest, status = resolve_manifest("https://example.com/demo-no-https.json")
    return manifest is None and status.get("mode") == "HTTPS_DISABLED"


def test_volume_aabb_frame_ref() -> bool:
    service = VPSServiceV15(SpatialDDSLogger())
    # 1.7: no CoverageElement.type — the volume form is has_aabb without bbox.
    volume = next(
        (
            elem
            for elem in service.coverage
            if elem.get("has_aabb") and not elem.get("has_bbox")
        ),
        None,
    )
    if not volume:
        return False
    SpatialDDSValidator.validate_coverage(service.coverage, service.coverage_frame_ref)
    return volume.get("has_aabb") and (volume.get("has_frame_ref") or service.coverage_frame_ref)


def test_http_search_returns_service_manifests() -> bool:
    """
    (a) The HTTP discovery binding returns full service manifests.

    1.7's two discovery bindings are deliberately asymmetric: HTTP returns
    whole manifests (no bus, so one round trip must carry everything), while
    the DDS binding returns compact ServiceSummary rows — see
    test_bus_search_returns_service_summaries below.
    """
    import http_binding

    http_binding._announce_registry = []
    handler = http_binding.SpatialDDSHTTPHandler

    frame_ref, cov_elem = create_coverage_bbox_earth_fixed(-122.5, 37.7, -122.3, 37.8)
    caps = {
        "supported_profiles": [
            {"name": "spatial.core", "major": 1, "min_minor": 7, "max_minor": 7}
        ],
        "preferred_profiles": ["spatial.core/1.7"],
        "features": ["blob.crc32"],
    }
    topics = [{"name": "spatialdds/vps/query/v1", "type": "vps_query",
               "version": "v1", "qos_profile": "VPS_REQ"}]
    announce = {
        "service_id": "svc:vps:test/summary",
        "name": "Test Service",
        "kind": "VPS",
        "org": "ExampleOrg",
        "coverage": [cov_elem],
        "coverage_frame_ref": frame_ref,
        "manifest_uri": "spatialdds://test.com/zone:test/manifest:svc",
        "caps": caps,
        "topics": topics,
        "stamp": SpatialDDSValidator.now_time(),
        "ttl_sec": 60,
    }
    handler._register_announce(handler, http_binding._normalize_announce(announce))

    frame_ref_q, cov_elem_q = create_coverage_bbox_earth_fixed(-122.45, 37.75, -122.4, 37.8)
    results = handler._search_manifests(
        handler,
        {
            "query_id": "q-manifest",
            "coverage": [cov_elem_q],
            "coverage_frame_ref": frame_ref_q,
            "has_filter": False,
        },
    )
    if len(results) != 1:
        return False
    row = results[0]
    try:
        SpatialDDSValidator.validate_manifest_profile(row.get("profile", ""))
    except Exception:
        return False
    return (
        row.get("id") == "spatialdds://test.com/zone:test/manifest:svc"
        and row.get("rtype") == "service"
        and row.get("service", {}).get("service_id") == "svc:vps:test/summary"
        and row.get("service", {}).get("topics") == topics
        and row.get("caps") == caps
        and row.get("coverage", {}).get("frame_ref") == frame_ref
        and row["coverage"].get("has_bbox") is True
    )


def test_http_search_response_has_no_query_id() -> bool:
    """
    The HTTP binding's envelope is {results, next_page_token} — no query_id.

    Asserts on the response the binding actually produces. (This previously
    scraped the handler's source for a `response = {` literal, which said
    nothing about behaviour and broke the moment the envelope moved into the
    shared core.)
    """
    from spatialdds_demo.discovery_http import search

    frame_ref, elem = create_coverage_bbox_earth_fixed(-122.5, 37.7, -122.3, 37.8)
    response = search([], {"coverage": [elem], "coverage_frame_ref": frame_ref})
    return set(response) == {"results", "next_page_token"}


def test_bus_search_returns_service_summaries() -> bool:
    """
    The DDS binding still returns compact ServiceSummary rows (with query_id).

    This is the counterpart to test_http_search_returns_service_manifests:
    the two bindings intentionally differ and both are checked.
    """
    service = VPSServiceV15(SpatialDDSLogger())
    frame_ref, cov_elem = create_coverage_bbox_earth_fixed(-122.45, 37.75, -122.4, 37.8)
    response = service.handle_coverage_query(
        {
            "query_id": "q-bus",
            "coverage": [cov_elem],
            "coverage_frame_ref": frame_ref,
            "has_filter": False,
        }
    )
    if response.get("query_id") != "q-bus" or "next_page_token" not in response:
        return False
    rows = response.get("results", [])
    if len(rows) != 1:
        return False
    row = rows[0]
    try:
        SpatialDDSValidator.validate_service_summary(row)
    except Exception:
        return False
    # The point of the 1.7 DDS change: no caps/topics/transforms inline.
    return not (set(row) & {"caps", "topics", "transforms", "profile", "rtype"})


def test_coverage_query_expr_rejected() -> bool:
    """(b) A CoverageQuery carrying the deleted `expr` field is rejected."""
    service = VPSServiceV15(SpatialDDSLogger())
    frame_ref, cov_elem = create_coverage_bbox_earth_fixed(-122.45, 37.75, -122.4, 37.8)
    query = {
        "query_id": "q-expr",
        "coverage": [cov_elem],
        "coverage_frame_ref": frame_ref,
        "expr": 'kind=="VPS"',
    }
    try:
        service.handle_coverage_query(query)
    except ValueError:
        return True
    return False


def test_at_form_profile_rejected() -> bool:
    """(c) Manifests using the retired `@` identifier form fail validation."""
    for bad in ("spatial.manifest@1.6", "spatial.manifest@1.7", "spatial.manifest/1.6"):
        try:
            SpatialDDSValidator.validate_manifest_profile(bad)
        except Exception:
            continue
        return False
    try:
        SpatialDDSValidator.validate_manifest_profile("spatial.manifest/1.7")
    except Exception:
        return False
    return True


def test_manifest_topics_are_registered() -> bool:
    """Bundled 1.7 manifest topics use registered (or documented) type/QoS names."""
    manifest, _ = resolve_manifest("spatialdds://vps.example.com/zone:sf-downtown/manifest:vps")
    topics = manifest.get("service", {}).get("topics", []) if manifest else []
    if not topics:
        return False
    ok, _ = validate_topic_meta(topics)
    return ok


def test_no_identity_transforms() -> bool:
    service = VPSServiceV15(SpatialDDSLogger())
    announce = service.create_announce()
    return not announce.get("transforms")


def test_catalog_seed() -> bool:
    try:
        with open("catalog_seed.json", "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return False
    if not isinstance(data, list) or len(data) < 25:
        return False
    sample = data[0]
    return (
        "content_id" in sample
        and "kind" in sample
        and "coverage" in sample
        and "frame_ref" in sample
        and "formats" in sample
    )


def main() -> int:
    tests = [
        ("manifest_resolver", test_manifest_resolver),
        ("topic_validator", test_topic_validator),
        ("demo_output", test_demo_output),
        ("manifest_fallback", test_manifest_fallback),
        ("https_resolution_disabled", test_https_resolution_disabled),
        ("volume_frame_ref", test_volume_aabb_frame_ref),
        ("http_search_returns_service_manifests", test_http_search_returns_service_manifests),
        ("http_search_response_has_no_query_id", test_http_search_response_has_no_query_id),
        ("bus_search_returns_service_summaries", test_bus_search_returns_service_summaries),
        ("coverage_query_expr_rejected", test_coverage_query_expr_rejected),
        ("at_form_profile_rejected", test_at_form_profile_rejected),
        ("manifest_topics_are_registered", test_manifest_topics_are_registered),
        ("no_identity_transforms", test_no_identity_transforms),
        ("catalog_seed", test_catalog_seed),
    ]
    failures = []
    for name, func in tests:
        if not func():
            failures.append(name)

    if failures:
        print("❌ Demo tests failed: " + ", ".join(failures))
        return 1

    print("✅ Demo tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
