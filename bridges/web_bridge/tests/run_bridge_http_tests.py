#!/usr/bin/env python3
"""End-to-end HTTP bridge test for SpatialDDS web demo.

Starts VPS, catalog, and bridge servers, then exercises:
- CoverageQuery (DDS) to discover VPS
- Localize via bridge (HTTP)
- Catalog query via bridge (HTTP)
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, Optional, List

from spatialdds_demo.topics import TOPIC_DISCOVERY_QUERY_V1, TOPIC_DISCOVERY_RESPONSE
from spatialdds_validation import SpatialDDSValidator, create_coverage_bbox_earth_fixed

# repo root is three levels up: tests -> web_bridge -> bridges -> root.
# (It was two when these lived in bridge/tests/; the bridge ->
# bridges/web_bridge move added a level.)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BRIDGE_URL = os.getenv("SPATIALDDS_BRIDGE_URL", "http://localhost:8088")

AUSTIN_LAT = 30.2847
AUSTIN_LON = -97.739475
AUSTIN_ALT = 18.0

EXPECTED_CONTENT = {
    "5f8b2f2a-7c2b-4f15-9b68-8a9a7c5f7e01",
    "3c1a0fd2-2e4b-4c0e-9b12-6d2c3c1b7e02",
}


def _env_for_dds() -> Dict[str, str]:
    env = os.environ.copy()
    env["SPATIALDDS_TRANSPORT"] = "dds"
    env["CYCLONEDDS_URI"] = "file:///etc/cyclonedds.xml"
    env["SPATIALDDS_DDS_DOMAIN"] = "1"
    env["SPATIALDDS_VPS_COVERAGE_BBOX"] = "-97.75,30.27,-97.72,30.29"
    env["SPATIALDDS_VPS_MAP_FQN"] = "map/austin"
    env["SPATIALDDS_VPS_MAP_ID"] = "austin-map"
    env["SPATIALDDS_CATALOG_SEED"] = os.path.join(ROOT, "bridges", "web_bridge", "tests", "catalog_seed_austin.json")
    return env


def _start_process(args: List[str], env: Dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        args,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_for_health(timeout: float = 10.0) -> Dict[str, object]:
    deadline = time.time() + timeout
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BRIDGE_URL}/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok":
                return payload
        except Exception as exc:
            last_error = exc
        time.sleep(0.3)
    raise RuntimeError(f"Bridge health check failed: {last_error}")


def _coverage_query(domain_id: int = 1) -> Dict[str, object]:
    """
    Ask the bus who covers an area, using the typed CoverageClient.

    C.5: the query goes on the well-known topic and names the topic the
    reply should come back on, so a reply reaches one asker rather than
    every client on the bus.
    """
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo.json_mapping import from_json, to_json
    from spatialdds_demo.service_bus import CoverageClient
    from spatialdds_idl.spatial.disco import CoverageQuery

    query_id = "bridge-test-coverage"
    reply_topic = TOPIC_DISCOVERY_RESPONSE(query_id)
    client = CoverageClient(DomainParticipant(domain_id), reply_topic)

    coverage_frame_ref, coverage_elem = create_coverage_bbox_earth_fixed(
        -97.75, 30.27, -97.72, 30.29
    )
    query = {
        "query_id": query_id,
        "coverage": [coverage_elem],
        "coverage_frame_ref": coverage_frame_ref,
        # Presence-flagged: the value is always on the wire and the flag says
        # whether to read it.
        "has_coverage_eval_time": False,
        "coverage_eval_time": SpatialDDSValidator.now_time(),
        "has_filter": True,
        "filter": {"type_in": [], "qos_profile_in": [], "module_id_in": []},
        "reply_topic": reply_topic,
        "stamp": SpatialDDSValidator.now_time(),
        "ttl_sec": 60,
    }
    response = client.query(from_json(CoverageQuery, query), timeout=8.0)
    if response is None:
        raise RuntimeError("COVERAGE_RESPONSE timeout")
    return to_json(response)


def _post_json(path: str, payload: Dict[str, object]) -> Dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _assert_close(actual: float, expected: float, eps: float, label: str) -> None:
    if abs(actual - expected) > eps:
        raise AssertionError(f"{label} expected {expected}±{eps}, got {actual}")


def main() -> int:
    env = _env_for_dds()
    vps = None
    catalog = None
    bridge = None

    try:
        vps = _start_process([sys.executable, "ar_demo/spatialdds_demo_server.py"], env)
        catalog = _start_process([sys.executable, "ar_demo/spatialdds_catalog_server.py"], env)
        bridge = _start_process([sys.executable, "bridges/web_bridge/server.py"], env)

        health = _wait_for_health()
        print(f"bridge health ok: domain={health.get('dds_domain')}")

        coverage = _coverage_query(domain_id=1)
        results = coverage.get("results", [])
        if not results:
            raise AssertionError("coverage query returned no VPS results")
        print(f"coverage results: {len(results)}")

        prior = {
            "lat_deg": AUSTIN_LAT,
            "lon_deg": AUSTIN_LON,
            "alt_m": AUSTIN_ALT,
            "q": [0.0, 0.0, 0.0, 1.0],
            "stamp": SpatialDDSValidator.now_time(),
            "cov": "COV_NONE",
        }
        localize = _post_json("/v1/localize", {"prior_geopose": prior})
        node_geo = localize.get("node_geo", {}) if isinstance(localize, dict) else {}
        geopose = node_geo.get("geopose", {}) if isinstance(node_geo, dict) else {}
        if not geopose:
            raise AssertionError("localize response missing geopose")
        _assert_close(geopose.get("lat_deg", 0.0), AUSTIN_LAT, 0.01, "lat_deg")
        _assert_close(geopose.get("lon_deg", 0.0), AUSTIN_LON, 0.01, "lon_deg")
        print("localize ok")

        catalog_response = _post_json(
            "/v1/catalog/query",
            {"geopose": geopose, "kind_in": ["overlay", "poi"]},
        )
        results = catalog_response.get("results", []) if isinstance(catalog_response, dict) else []
        ids = {entry.get("content_id") for entry in results}
        missing = EXPECTED_CONTENT.difference(ids)
        if missing:
            raise AssertionError(f"catalog missing expected content: {sorted(missing)}")
        print("catalog ok")

        print("bridge HTTP tests passed")
        return 0

    except Exception as exc:
        print(f"bridge HTTP tests failed: {exc}")
        return 1

    finally:
        if bridge:
            _terminate(bridge)
        if catalog:
            _terminate(catalog)
        if vps:
            _terminate(vps)


if __name__ == "__main__":
    sys.exit(main())
