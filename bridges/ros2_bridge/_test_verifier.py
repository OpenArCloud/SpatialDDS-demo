#!/usr/bin/env python3
"""Tier-3 verifier: check the bridge put real SpatialDDS samples on the bus.

Subscribes with a typed reader per lane the ROS 2 bridge publishes and
asserts that each carried the payload the ROS 2 side sent. Used by
docker-compose.test.yaml, which runs real rclpy publishers against a real
bridge container.

The check that matters is that a *typed* reader matched at all: a topic
whose type or QoS profile disagreed with the writer's would simply never
deliver, so an arriving sample is already proof the bridge announced what it
publishes. The field spot-checks below confirm the translation on top of
that.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Match the SpatialDDS domain used by the bridge container.
DOMAIN = 43

REPO = Path("/ws")
sys.path.insert(0, str(REPO))

from cyclonedds.domain import DomainParticipant  # noqa: E402

from spatialdds_demo import topic_types, typed_transport as tt  # noqa: E402
from spatialdds_demo.json_mapping import to_json  # noqa: E402

# (lane, topic, §3.3.2 type, §3.3.3 QoS profile) — what the bridge's config
# maps the ROS 2 publishers onto.
# `_test_bridge.sh` runs the bridge with `--operator test_fleet` and the
# CLI-flag defaults for sensor ids.
OPERATOR = "test_fleet"
LANES = [
    ("pose", f"spatialdds/{OPERATOR}/ego/pose/v1", "framed_pose", "POSE_RT"),
    ("geo", f"spatialdds/{OPERATOR}/geo/gnss/pose/v1", "geopose", "POSE_RT"),
    ("imu", f"spatialdds/{OPERATOR}/imu/imu_0/sample/v1", "imu_sample",
     "IMU_RT"),
]


def _check(lane: str, payload: dict) -> str:
    """Spot-check the translated fields. Returns an error, or "" if fine."""
    if lane == "pose":
        x = payload.get("pose", {}).get("t", [0.0])[0]
        return "" if abs(x - 42.0) <= 1e-3 else f"pose.t[0] was {x}, expected 42.0"
    if lane == "geo":
        lat = payload.get("lat_deg", 0.0)
        return "" if abs(lat - 30.267) <= 1e-3 else f"lat_deg was {lat}"
    if lane == "imu":
        # ImuSample names the field `accel`, not ROS 2's
        # `linear_acceleration` — the bridge translates the name as well as
        # the value.
        az = payload.get("accel", [0.0, 0.0, 0.0])[2]
        return "" if abs(az - 9.78) <= 1e-3 else f"accel[2] was {az}"
    return f"no check defined for {lane}"


def main() -> int:
    print(f"[verifier] subscribing on SpatialDDS domain {DOMAIN}")
    time.sleep(5)                      # let the bridge come up

    participant = DomainParticipant(DOMAIN)
    readers = {}
    for lane, topic, type_name, profile in LANES:
        readers[lane] = (
            tt.make_reader(participant, topic,
                           topic_types.resolve(type_name), profile),
            topic, type_name,
        )

    counts: dict = {}
    first: dict = {}
    deadline = time.time() + 20
    while time.time() < deadline:
        for lane, (reader, _topic, _type_name) in readers.items():
            for sample in tt.take_samples(reader):
                counts[lane] = counts.get(lane, 0) + 1
                first.setdefault(lane, to_json(sample))
        if len(counts) == len(LANES):
            break
        time.sleep(0.2)

    print("\n[verifier] samples per lane:")
    for lane, topic, type_name, profile in LANES:
        print(f"  {counts.get(lane, 0):>4}  {lane:6s} {type_name:14s} "
              f"{profile:9s} {topic}")

    missing = [lane for lane, *_ in LANES if lane not in counts]
    if missing:
        print(f"\n[verifier] FAIL: no samples on {missing}", file=sys.stderr)
        return 1

    for lane in first:
        error = _check(lane, first[lane])
        if error:
            print(f"[verifier] FAIL: {lane}: {error}", file=sys.stderr)
            return 1

    print("\n[verifier] PASS — every ROS 2 -> SpatialDDS lane delivered a "
          "typed sample with the expected contents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
