#!/usr/bin/env python3
"""End-to-end HTTP bridge test for SpatialDDS web demo.

Starts VPS, catalog, and bridge servers, then exercises:
- CoverageQuery (DDS) to discover VPS
- Localize via bridge (HTTP)
- Catalog query via bridge (HTTP)
"""

import json
import os
import queue
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, Optional, List

from spatialdds_demo.dds_transport import DDSTransport
from spatialdds_demo.topics import TOPIC_VPS_COVERAGE_QUERY_V1, TOPIC_VPS_COVERAGE_REPLIES_V1
from spatialdds_validation import SpatialDDSValidator, create_coverage_bbox_earth_fixed

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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
    env["SPATIALDDS_CATALOG_SEED"] = os.path.join(ROOT, "bridge", "tests", "catalog_seed_austin.json")
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
    inbox: queue.Queue = queue.Queue()

    def on_message(envelope: object) -> None:
        inbox.put(envelope)

    transport = DDSTransport(on_message_callback=on_message, domain_id=domain_id, local_sender_id="bridge-test")
    transport.start()
    coverage_frame_ref, coverage_elem = create_coverage_bbox_earth_fixed(
        -97.75, 30.27, -97.72, 30.29
    )
    query = {
        "query_id": "bridge-test-coverage",
        "coverage": [coverage_elem],
        "coverage_frame_ref": coverage_frame_ref,
        "has_coverage_eval_time": False,
        "expr": 'kind=="VPS"',
        "reply_topic": TOPIC_VPS_COVERAGE_REPLIES_V1,
        "stamp": SpatialDDSValidator.now_time(),
        "ttl_sec": 60,
    }
    transport.publish(
        TOPIC_VPS_COVERAGE_QUERY_V1,
        "COVERAGE_QUERY",
        json.dumps(query),
        query["query_id"],
    )

    response_env = _wait_for(inbox, "COVERAGE_RESPONSE", timeout=6)
    transport.stop()
    if not response_env:
        raise RuntimeError("COVERAGE_RESPONSE timeout")
    return json.loads(response_env.payload_json)


def _wait_for(queue_obj: queue.Queue, msg_type: str, timeout: float) -> Optional[object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        try:
            envelope = queue_obj.get(timeout=remaining)
        except queue.Empty:
            continue
        if envelope.msg_type == msg_type:
            return envelope
    return None


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
        vps = _start_process([sys.executable, "spatialdds_demo_server.py"], env)
        catalog = _start_process([sys.executable, "spatialdds_catalog_server.py"], env)
        bridge = _start_process([sys.executable, "bridge/server.py"], env)

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
            "q_xyzw": [0.0, 0.0, 0.0, 1.0],
            "frame_kind": "ENU",
            "frame_ref": {"uuid": "austin-seed", "fqn": "earth.enu"},
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
            {"geopose": geopose, "expr": 'kind=="overlay" OR kind=="poi"'},
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
