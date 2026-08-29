#!/usr/bin/env python3
"""Smoke-test the local Fargate-style stack.

Hits /health, /api/topics, and /api/stats on a running web bridge
(default ``http://localhost:8088``) and asserts that:

  * The bridge is up.
  * Synthetic Detection3D envelopes from ≥ 2 of the 3 fake operators
    reach the bridge (proves DDS discovery on the shared loopback works
    between the publisher and web-bridge containers).
  * FusedTrackSet envelopes from the platform fuser reach the bridge
    (proves discovery between publisher → fusion AND fusion → web-bridge
    across separate containers in the same task).
  * The AR demo half, when deployed: both services discoverable over the
    bus by kind, a localize round trip, and the Cesium bundle served.
    Skipped rather than failed when the AR containers are switched off, so
    a fusion-only deployment still passes.

Exits 0 on success, 1 on first failure.

Usage:

    python3 deploy/aws/smoke_test.py
    BASE=http://localhost:8088 TIMEOUT=30 python3 deploy/aws/smoke_test.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from typing import Any


BASE = os.getenv("BASE", "http://localhost:8088").rstrip("/")
TIMEOUT_S = float(os.getenv("TIMEOUT", "30"))


def _get(path: str) -> Any:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as resp:
        return json.loads(resp.read())


def _wait(condition_fn, what: str, timeout_s: float = TIMEOUT_S) -> Any:
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = condition_fn()
            if last:
                return last
        except Exception as exc:
            last = f"(error: {exc})"
        time.sleep(0.5)
    print(f"[FAIL] timed out waiting for {what}; last={last}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    print(f"[smoke] target={BASE}", flush=True)

    # 1) /health up
    health = _wait(lambda: _get("/health"), "/health to return 200")
    assert health.get("status") == "ok", f"/health returned {health}"
    print(f"[smoke] /health OK  domain={health['dds_domain']}", flush=True)

    # 2) Synthetic publishers reach the bridge
    def operator_topics():
        topics = _get("/api/topics?stale_threshold_s=120")["topics"]
        op_topics = [t for t in topics
                       if "/sensing/detection3d/" in t["logical_topic"]]
        if len(op_topics) >= 2:
            return op_topics
        return None

    op_topics = _wait(operator_topics,
                       ">=2 operator detection3d topics on the bus")
    print(f"[smoke] {len(op_topics)} operator detection3d topics seen:",
          flush=True)
    for t in op_topics:
        print(f"        {t['rate_hz']:>5.1f}Hz  {t['logical_topic']}",
              flush=True)

    # 3) Fused tracks reach the bridge
    def fused_topic():
        topics = _get("/api/topics?stale_threshold_s=120")["topics"]
        fused = [t for t in topics
                  if t["logical_topic"] == "spatialdds/platform/fusion/track/v1"]
        return fused[0] if fused else None

    track_topic = _wait(fused_topic,
                          "platform/fusion/track/v1 to appear",
                          timeout_s=TIMEOUT_S * 2)
    print(f"[smoke] fused tracks: {track_topic['message_count']} messages "
          f"@ {track_topic['rate_hz']:.1f} Hz", flush=True)

    # 4) Bridge stats sanity
    stats = _get("/api/stats")
    print(f"[smoke] stats: uptime={stats['uptime_s']}s "
          f"dispatched={stats['total_dispatched']} "
          f"topics_active={stats['topics_active']}", flush=True)
    assert stats["topics_active"] >= 3, \
        f"expected >=3 active topics, got {stats['topics_active']}"

    # 5) The AR demo half, if it is deployed.
    #
    # Skipped rather than failed when absent: `features.ar_demo: false` is a
    # supported deployment, and a smoke test that fails on a supported
    # configuration teaches people to ignore it.
    if _ar_demo_present():
        _check_ar_demo()
    else:
        print("[smoke] AR demo not deployed (features.ar_demo off?) — skipped",
              flush=True)

    print("[smoke] PASS — DDS-on-loopback verified across web-bridge / "
          "publisher / fusion containers", flush=True)
    return 0


def _search(kind: str) -> list:
    """Service ids covering downtown Austin, by kind, via the spec binding."""
    body = _get(f"/.well-known/spatialdds/search?geohash=9v6kr&kind={kind}")
    return [r["service"]["service_id"] for r in body.get("results", [])]


def _ar_demo_present() -> bool:
    try:
        return bool(_search("VPS"))
    except Exception:
        return False


def _check_ar_demo() -> None:
    # Discovery goes over the bus: the endpoint issues a CoverageQuery and
    # the services answer it, so this exercises the AR containers rather than
    # a cache the bridge filled at startup.
    vps = _wait(lambda: _search("VPS") or None, "a VPS covering 9v6kr")
    content = _wait(lambda: _search("CONTENT") or None,
                    "a content service covering 9v6kr")
    print(f"[smoke] discovery: VPS={vps} CONTENT={content}", flush=True)

    # A localize round trip, naming the service discovery just returned.
    # No query image: the bridge sends its placeholder, which is enough to
    # prove the request/reply path across containers.
    request = json.dumps({
        "service_id": vps[0],
        "prior_geopose": {"lat_deg": 30.284996, "lon_deg": -97.739494,
                          "alt_m": 18.0, "q": [0.0, 0.0, 0.0, 1.0],
                          "stamp": {"sec": 0, "nanosec": 0}, "cov": "COV_NONE"},
    }).encode()
    req = urllib.request.Request(f"{BASE}/v1/localize", data=request,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        localize = json.loads(resp.read())
    assert localize.get("status") == "VPS_SUCCESS", f"localize: {localize}"
    assert localize.get("service_id") == vps[0], (
        f"answered by {localize.get('service_id')}, asked {vps[0]}")
    print(f"[smoke] localize: {localize['status']} from {localize['service_id']}",
          flush=True)

    # The Cesium bundle is served by the same process behind the same
    # load balancer, which is what lets the app find its API on its own origin.
    with urllib.request.urlopen(f"{BASE}/ar/", timeout=10) as resp:
        body = resp.read().decode("utf-8", "replace")
    assert resp.status == 200 and "<" in body, "/ar/ did not serve a page"

    # And its assets resolve. Serving the HTML proves almost nothing: Vite
    # writes absolute asset URLs from its `base`, so a bundle built for the
    # site root while mounted at /ar returns a perfectly good page whose every
    # script and stylesheet 404s — a blank screen with a healthy page source.
    assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', body)
    assert assets, f"/ar/ served no script or stylesheet references: {body[:200]}"
    for asset in assets:
        url = f"{BASE}{asset}" if asset.startswith("/") else f"{BASE}/ar/{asset}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            assert resp.status == 200, f"{asset} -> {resp.status}"
    print(f"[smoke] AR bundle: /ar/ served {len(body)} bytes, "
          f"{len(assets)} asset(s) resolve", flush=True)


if __name__ == "__main__":
    sys.exit(main())
