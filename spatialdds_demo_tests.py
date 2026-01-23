#!/usr/bin/env python3
import os
import subprocess
import sys

from spatialdds_demo.manifest_resolver import resolve_manifest
from spatialdds_demo.topics import (
    TOPIC_VPS_LOCALIZE_REQUEST_V1,
    TOPIC_VPS_LOCALIZE_RESPONSE_V1,
    validate_topics_are_canonical,
)


def test_manifest_resolver() -> bool:
    manifest, status = resolve_manifest("spatialdds://vps.example.com/zone:sf-downtown/manifest:vps")
    return (
        manifest is not None
        and manifest.get("service_id") == "svc:vps:demo/sf-downtown"
        and status.get("mode") == "LOCAL"
    )


def test_topic_validator() -> bool:
    ok, _ = validate_topics_are_canonical(
        [TOPIC_VPS_LOCALIZE_REQUEST_V1, TOPIC_VPS_LOCALIZE_RESPONSE_V1],
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
        [sys.executable, "spatialdds_test.py", "--summary-only"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    output = result.stdout + result.stderr
    required = [
        "manifest_resolver: LOCAL",
        "manifest_loaded: yes",
        "topic=spatialdds/vps/localize/request/v1",
        "topic=spatialdds/vps/localize/response/v1",
        "topic=spatialdds/vps/coverage/replies/v1",
        "topic=spatialdds/anchors/",
        "topic_source=manifest",
    ]
    return result.returncode == 0 and all(item in output for item in required)


def test_manifest_fallback() -> bool:
    env = os.environ.copy()
    env["SLIDE_MODE"] = "1"
    env["SPATIALDDS_DEMO_MANIFEST_URI"] = "https://example.com/demo.json"
    result = subprocess.run(
        [sys.executable, "spatialdds_test.py", "--summary-only"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    output = result.stdout + result.stderr
    required = [
        "manifest_resolver: HTTPS_DISABLED",
        "manifest_loaded: no",
        "topic=spatialdds/vps/localize/request/v1",
        "topic=spatialdds/vps/localize/response/v1",
        "topic_source=fallback",
    ]
    return result.returncode == 0 and all(item in output for item in required)


def main() -> int:
    tests = [
        ("manifest_resolver", test_manifest_resolver),
        ("topic_validator", test_topic_validator),
        ("demo_output", test_demo_output),
        ("manifest_fallback", test_manifest_fallback),
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
