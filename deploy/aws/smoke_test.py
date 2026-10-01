#!/usr/bin/env python3
"""Smoke-test the local Fargate-style stack.

Hits /health, /api/topics, and /api/stats on a running web bridge
(default ``http://localhost:8088``) and asserts that:

  * The bridge is up.
  * The fusion half, when deployed: Detection3D envelopes from ≥ 2 of the
    3 fake operators, and FusedTrackSet envelopes from the platform fuser
    (proves DDS discovery on the shared loopback, publisher → fusion and
    fusion → web-bridge, across separate containers in one task).
    Skipped rather than failed when `features.fusion_demo` is off — which
    is the committed default, since the two demos share a DDS domain.
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

import base64
import json
import os
import pathlib
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

    # 2-3) The fusion half, if it is deployed.
    #
    # Conditional for exactly the reason step 5 already is: `fusion_demo: false`
    # is a supported deployment -- it is the committed default in config.yaml,
    # because the two demos share a DDS domain and running both means each sees
    # the other's traffic. Asserting fusion topics unconditionally made the
    # smoke test fail on the repo's own default, which is worse than having no
    # smoke test: deploy.sh prints this as the way to verify a deployment, so a
    # guaranteed red teaches people to ignore the one check they are told to run.
    if _fusion_demo_present():
        _check_fusion()
    else:
        print("[smoke] fusion demo not deployed (features.fusion_demo off?) "
              "— skipped", flush=True)

    # 4) Bridge stats sanity
    #
    # `topics_active` counts topics seen in the last 30 s (get_topics' default
    # window), so it measures *continuous* publishers. The fusion demo has
    # those; the AR demo does not -- its traffic is request/reply, so 30 s after
    # the last localize the count is legitimately 0. Asserting >= 3 here was a
    # third fusion assumption wearing a general name.
    stats = _get("/api/stats")
    print(f"[smoke] stats: uptime={stats['uptime_s']}s "
          f"dispatched={stats['total_dispatched']} "
          f"topics_active={stats['topics_active']}", flush=True)
    assert stats["uptime_s"] > 0, f"bridge reports no uptime: {stats}"
    # What is true in every configuration: the bridge has seen the bus. Over a
    # 120 s window rather than 30, so a request-driven demo is not called dead
    # for being idle.
    seen = _get("/api/topics?stale_threshold_s=120")["topics"]
    assert seen, "bridge has seen no topics at all on the bus"
    print(f"[smoke] {len(seen)} topics seen within 120s:", flush=True)
    for t in seen:
        print(f"        {t.get('rate_hz', 0):>5.1f}Hz  {t['logical_topic']}",
              flush=True)

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

    print("[smoke] PASS", flush=True)
    return 0


def _search(kind: str) -> list:
    """Service ids covering downtown Austin, by kind, via the spec binding."""
    body = _get(f"/.well-known/spatialdds/search?geohash=9v6kr&kind={kind}")
    return [r["service"]["service_id"] for r in body.get("results", [])]


def _fusion_demo_present() -> bool:
    """Is anything publishing the fusion demo's topics?

    Probed rather than read from config.yaml: the smoke test runs against a
    deployment by URL and may not have the config that produced it.
    """
    try:
        topics = _get("/api/topics?stale_threshold_s=120")["topics"]
    except Exception:
        return False
    return any("/sensing/detection3d/" in t["logical_topic"] for t in topics)


def _check_fusion() -> None:
    # Synthetic publishers reach the bridge -- proves DDS discovery on the
    # shared loopback works between the publisher and web-bridge containers.
    def operator_topics():
        topics = _get("/api/topics?stale_threshold_s=120")["topics"]
        op_topics = [t for t in topics
                     if "/sensing/detection3d/" in t["logical_topic"]]
        return op_topics if len(op_topics) >= 2 else None

    op_topics = _wait(operator_topics,
                      ">=2 operator detection3d topics on the bus")
    print(f"[smoke] {len(op_topics)} operator detection3d topics seen:",
          flush=True)
    for t in op_topics:
        print(f"        {t['rate_hz']:>5.1f}Hz  {t['logical_topic']}",
              flush=True)

    # Fused tracks reach the bridge -- proves discovery publisher -> fusion AND
    # fusion -> web-bridge, across separate containers in one task.
    def fused_topic():
        topics = _get("/api/topics?stale_threshold_s=120")["topics"]
        fused = [t for t in topics
                 if t["logical_topic"] == "spatialdds/platform/fusion/track/v1"]
        return fused[0] if fused else None

    track_topic = _wait(fused_topic, "platform/fusion/track/v1 to appear",
                        timeout_s=TIMEOUT_S * 2)
    print(f"[smoke] fused tracks: {track_topic['message_count']} messages "
          f"@ {track_topic['rate_hz']:.1f} Hz", flush=True)


def _query_frame() -> str | None:
    """A real photograph from the scan a map was built from, base64, if present.

    Without one, a real localizer can only be asked to fail. The bundle is
    gitignored -- they are photographs of a real place -- so this is best-effort
    by design rather than a missing fixture.
    """
    override = os.getenv("QUERY_FRAME")
    candidates = []
    if override:
        candidates.append(pathlib.Path(override))
    else:
        bundle = (pathlib.Path(__file__).resolve().parents[2]
                  / "web" / "public" / "query-frames")
        manifest = bundle / "manifest.json"
        if manifest.is_file():
            try:
                names = json.loads(manifest.read_text()).get("frames") or []
            except Exception:
                names = []
            candidates += [bundle / n for n in names[:1]]
    for c in candidates:
        if c.is_file():
            return base64.b64encode(c.read_bytes()).decode()
    return None


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
    #
    # What this step can assert depends on who is answering, and conflating the
    # two made it fail against a working deployment.
    #
    # The demo's own stand-in returns the prior plus jitter and never looks at a
    # pixel, so the bridge's placeholder image gets VPS_SUCCESS out of it.
    # A *real* OpenVPS retrieves and matches against a map: a placeholder has
    # nothing in common with the capture, so VPS_FAILED is the correct answer
    # and the only correct answer. Requiring VPS_SUCCESS here asserted that the
    # localizer was ignoring its input.
    #
    # The discriminator is the announced id, the same one web/src uses to decide
    # whether to auto-localize: anything that is not `svc:vps:demo/...` is
    # somebody else's service.
    is_mock = vps[0].startswith("svc:vps:demo/")
    query_image = _query_frame()

    body = {
        "service_id": vps[0],
        "prior_geopose": {"lat_deg": 30.284996, "lon_deg": -97.739494,
                          "alt_m": 18.0, "q": [0.0, 0.0, 0.0, 1.0],
                          "stamp": {"sec": 0, "nanosec": 0}, "cov": "COV_NONE"},
    }
    if query_image:
        body["query_image"] = query_image
    req = urllib.request.Request(f"{BASE}/v1/localize",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        localize = json.loads(resp.read())

    # True in every case, and the thing this step actually exists to prove: the
    # request crossed to the service and its reply came back, from the service
    # that was asked rather than from whoever answered first.
    assert localize.get("service_id") == vps[0], (
        f"answered by {localize.get('service_id')}, asked {vps[0]}")
    assert localize.get("status"), f"no status in reply: {localize}"

    if is_mock or query_image:
        assert localize.get("status") == "VPS_SUCCESS", f"localize: {localize}"
        where = "mock" if is_mock else "real VPS, real query frame"
        print(f"[smoke] localize: {localize['status']} from "
              f"{localize['service_id']} ({where})", flush=True)
    else:
        print(f"[smoke] localize: {localize['status']} from "
              f"{localize['service_id']} — round trip verified. A real VPS "
              f"cannot match the placeholder image, so a pose is not expected "
              f"here; install web/public/query-frames/ or set QUERY_FRAME to "
              f"check poses.", flush=True)

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
