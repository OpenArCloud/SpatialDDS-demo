#!/usr/bin/env python3
"""Tier-3 verifier: subscribe on the SpatialDDS envelope topic and assert that
the bridge translated every ROS 2 publisher into the expected ROS2_* envelope
type. Used by docker-compose.test.yaml.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Match the SpatialDDS domain used by the bridge container.
DOMAIN = 43

REPO = Path("/ws")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bridges" / "mcap_bridge"))

from recorder import _make_lossless_reader  # noqa: E402


def main() -> int:
    print(f"[verifier] subscribing on SpatialDDS domain {DOMAIN}")
    # Allow the bridge a moment to come up before we start polling.
    time.sleep(5)
    reader = _make_lossless_reader(DOMAIN)

    expected = {"ROS2_FRAMED_POSE", "ROS2_GEO_POSE", "ROS2_IMU_SAMPLE"}
    counts: dict[str, int] = {}
    topics: set[str] = set()
    samples_for: dict[str, dict] = {}

    deadline = time.time() + 20
    while time.time() < deadline:
        samples = reader.take(N=512)
        if samples:
            for s in samples:
                if s is None or not hasattr(s, "payload_json"):
                    continue
                try:
                    payload = json.loads(getattr(s, "payload_json", "") or "{}")
                except json.JSONDecodeError:
                    continue
                mt = getattr(s, "msg_type", "") or ""
                topic = getattr(s, "logical_topic", "") or ""
                counts[mt] = counts.get(mt, 0) + 1
                topics.add(topic)
                samples_for.setdefault(mt, payload)
        if expected <= counts.keys():
            break
        time.sleep(0.2)

    print()
    print("[verifier] received message-type counts:")
    for mt in sorted(counts):
        print(f"  {counts[mt]:>4}  {mt}")
    print("[verifier] logical topics:")
    for t in sorted(topics):
        print(f"  {t}")

    missing = expected - counts.keys()
    if missing:
        print(f"\n[verifier] FAIL: missing msg_types {sorted(missing)}", file=sys.stderr)
        return 1

    # Spot-check that the bridge actually translated payload fields.
    pose = samples_for.get("ROS2_FRAMED_POSE", {})
    if abs(pose.get("pose", {}).get("t", {}).get("x", 0.0) - 42.0) > 1e-3:
        print(f"[verifier] FAIL: pose.t.x mismatch in {pose}", file=sys.stderr)
        return 1
    geo = samples_for.get("ROS2_GEO_POSE", {})
    if abs(geo.get("lat_deg", 0.0) - 30.267) > 1e-3:
        print(f"[verifier] FAIL: geo lat_deg mismatch in {geo}", file=sys.stderr)
        return 1
    imu = samples_for.get("ROS2_IMU_SAMPLE", {})
    if abs(imu.get("linear_acceleration", {}).get("z", 0.0) - 9.78) > 1e-3:
        print(f"[verifier] FAIL: imu linear_acceleration.z mismatch in {imu}",
              file=sys.stderr)
        return 1

    if not any("/ego/pose/v1" in t for t in topics):
        print(f"[verifier] FAIL: no ego/pose topic in {topics}", file=sys.stderr)
        return 1
    if not any("/geo/" in t for t in topics):
        print(f"[verifier] FAIL: no geo topic in {topics}", file=sys.stderr)
        return 1
    if not any("/imu/" in t for t in topics):
        print(f"[verifier] FAIL: no imu topic in {topics}", file=sys.stderr)
        return 1

    print("\n[verifier] PASS — every ROS 2 → SpatialDDS bridge message arrived "
          "with the expected payload contents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
