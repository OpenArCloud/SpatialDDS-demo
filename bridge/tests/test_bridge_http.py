import json
import os
import queue
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional, List

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from spatialdds_demo.dds_transport import DDSTransport
from spatialdds_demo.topics import TOPIC_VPS_COVERAGE_QUERY_V1, TOPIC_VPS_COVERAGE_REPLIES_V1
from spatialdds_validation import SpatialDDSValidator, create_coverage_bbox_earth_fixed
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
    env["PYTHONPATH"] = ROOT
    return env


def _start_process(args: List[str], env: Dict[str, str], log_path: Path) -> subprocess.Popen:
    log_handle = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        args,
        cwd=ROOT,
        env=env,
        stdout=log_handle,
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
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        raise AssertionError(f"HTTP {exc.code} {exc.reason}: {body}") from exc


def _assert_close(actual: float, expected: float, eps: float, label: str) -> None:
    if abs(actual - expected) > eps:
        raise AssertionError(f"{label} expected {expected}±{eps}, got {actual}")


@pytest.fixture(scope="session")
def bridge_stack():
    try:
        __import__("cyclonedds")
    except Exception as exc:
        pytest.skip(f"cyclonedds bindings not available: {exc}")
    env = _env_for_dds()
    log_root = Path(os.getenv("SPATIALDDS_BRIDGE_TEST_LOG_DIR", "/app/bridge/tests/logs"))
    log_root.mkdir(parents=True, exist_ok=True)
    log_dir = log_root / f"run-{int(time.time())}"
    log_dir.mkdir(parents=True, exist_ok=True)
    vps_log = log_dir / "vps.log"
    catalog_log = log_dir / "catalog.log"
    bridge_log = log_dir / "bridge.log"

    vps = _start_process([sys.executable, "spatialdds_demo_server.py", "--detailed"], env, vps_log)
    catalog = _start_process([sys.executable, "spatialdds_catalog_server.py", "--detailed"], env, catalog_log)
    bridge = _start_process([sys.executable, "bridge/server.py"], env, bridge_log)

    try:
        _wait_for_health(timeout=20.0)
        yield
    except Exception as exc:
        vps_status = f"exit={vps.poll()}" if vps else "not-started"
        catalog_status = f"exit={catalog.poll()}" if catalog else "not-started"
        bridge_status = f"exit={bridge.poll()}" if bridge else "not-started"
        detail = (
            f"Bridge stack failed to start: {exc}\n\n"
            f"vps ({vps_status}) log tail:\n{_tail(vps_log)}\n\n"
            f"catalog ({catalog_status}) log tail:\n{_tail(catalog_log)}\n\n"
            f"bridge ({bridge_status}) log tail:\n{_tail(bridge_log)}"
        )
        raise RuntimeError(detail) from exc
    finally:
        _terminate(bridge)
        _terminate(catalog)
        _terminate(vps)


def _tail(path: Path, limit: int = 80) -> str:
    if not path.exists():
        return "(log missing)"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-limit:]) if lines else "(log empty)"


def test_bridge_http_flow(bridge_stack):
    coverage = _coverage_query(domain_id=1)
    results = coverage.get("results", [])
    assert results, "coverage query returned no VPS results"
    service_id = results[0].get("service_id", "")

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
    localize = _post_json("/v1/localize", {"prior_geopose": prior, "service_id": service_id})
    node_geo = localize.get("node_geo", {}) if isinstance(localize, dict) else {}
    geopose = node_geo.get("geopose", {}) if isinstance(node_geo, dict) else {}
    assert geopose, "localize response missing geopose"
    _assert_close(geopose.get("lat_deg", 0.0), AUSTIN_LAT, 0.01, "lat_deg")
    _assert_close(geopose.get("lon_deg", 0.0), AUSTIN_LON, 0.01, "lon_deg")

    catalog_response = _post_json(
        "/v1/catalog/query",
        {"geopose": geopose, "expr": 'kind=="overlay" OR kind=="poi"'},
    )
    results = catalog_response.get("results", []) if isinstance(catalog_response, dict) else []
    ids = {entry.get("content_id") for entry in results}
    missing = EXPECTED_CONTENT.difference(ids)
    assert not missing, f"catalog missing expected content: {sorted(missing)}"
