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

Exits 0 on success, 1 on first failure.

Usage:

    python3 deploy/aws/smoke_test.py
    BASE=http://localhost:8088 TIMEOUT=30 python3 deploy/aws/smoke_test.py
"""

from __future__ import annotations

import json
import os
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

    print("[smoke] PASS — DDS-on-loopback verified across web-bridge / "
          "publisher / fusion containers", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
