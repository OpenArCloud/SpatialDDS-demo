#!/usr/bin/env python3
import json
import os
import subprocess
import sys

from spatialdds_demo.manifest_resolver import resolve_manifest
from spatialdds_demo.topics import (
    TOPIC_VPS_QUERY_V1,
    TOPIC_VPS_RESULT_V1,
    validate_topics_are_canonical,
)
from spatialdds_test import SpatialDDSLogger, VPSServiceV15
from spatialdds_validation import SpatialDDSValidator


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
