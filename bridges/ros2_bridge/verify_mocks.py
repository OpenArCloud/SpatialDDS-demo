#!/usr/bin/env python3
"""One-time mock fidelity check — run inside a ROS 2 environment.

The Tier-1 tests use mock dataclasses in ``ros2_mocks.py`` so they can run
without ``rclpy``. This script imports the *real* ROS 2 message classes
and the mocks side-by-side, and verifies that every field name the
conversion layer reads is present on both.

Run inside a ROS 2 humble/iron/jazzy/rolling environment with the
relevant message packages installed:

    python3 -m pip install pyyaml  # optional
    python3 bridges/ros2_bridge/verify_mocks.py

Exits 0 when mocks match real ROS 2 IDL; nonzero with a diff report
otherwise. Re-run whenever ROS 2 message definitions change upstream.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import Set, Type

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import ros2_mocks as M  # noqa: E402


def _ros2_field_set(cls) -> Set[str]:
    """Return the set of field names on a real rclpy message class."""
    # rclpy generates a `get_fields_and_field_types()` classmethod on every
    # message class.
    if hasattr(cls, "get_fields_and_field_types"):
        return set(cls.get_fields_and_field_types().keys())
    if hasattr(cls, "_fields_and_field_types"):
        return set(cls._fields_and_field_types.keys())
    raise RuntimeError(f"Unsupported ROS 2 message class layout: {cls!r}")


def _mock_field_set(cls: Type) -> Set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def _check(label: str, mock_cls: Type, ros2_cls) -> int:
    mock_fields = _mock_field_set(mock_cls)
    ros2_fields = _ros2_field_set(ros2_cls)
    missing_on_ros2 = mock_fields - ros2_fields
    missing_on_mock = ros2_fields - mock_fields
    if not missing_on_ros2 and not missing_on_mock:
        print(f"  OK     {label}")
        return 0
    print(f"  DIFF   {label}")
    if missing_on_ros2:
        print(f"    mock has fields not present on ROS 2: {sorted(missing_on_ros2)}")
    if missing_on_mock:
        # Not strictly an error — the mock is allowed to omit fields the
        # bridge never reads — but worth surfacing.
        print(f"    ROS 2 has fields the mock omits (informational): "
              f"{sorted(missing_on_mock)}")
    return 0 if not missing_on_ros2 else 1


def main() -> int:
    try:
        from geometry_msgs.msg import PoseStamped as RosPoseStamped
        from sensor_msgs.msg import (CompressedImage as RosCompressedImage,
                                       Imu as RosImu, NavSatFix as RosNavSatFix)
        from vision_msgs.msg import (BoundingBox3D as RosBoundingBox3D,
                                       Detection3D as RosDetection3D,
                                       Detection3DArray as RosDetection3DArray,
                                       ObjectHypothesis as RosObjectHypothesis,
                                       ObjectHypothesisWithPose as
                                       RosObjectHypothesisWithPose)
    except Exception as exc:
        print(f"ROS 2 message imports failed: {exc}", file=sys.stderr)
        print("Run this script from inside a ROS 2 environment with "
              "sensor_msgs / geometry_msgs / vision_msgs installed.",
              file=sys.stderr)
        return 2

    rc = 0
    print("Mock vs ROS 2 field-name fidelity check:")
    rc |= _check("PoseStamped",                M.PoseStamped, RosPoseStamped)
    rc |= _check("CompressedImage",            M.CompressedImage, RosCompressedImage)
    rc |= _check("Imu",                        M.Imu, RosImu)
    rc |= _check("NavSatFix",                  M.NavSatFix, RosNavSatFix)
    rc |= _check("Detection3DArray",           M.Detection3DArray, RosDetection3DArray)
    rc |= _check("Detection3D",                M.Detection3D, RosDetection3D)
    rc |= _check("BoundingBox3D",              M.BoundingBox3D, RosBoundingBox3D)
    rc |= _check("ObjectHypothesis",           M.ObjectHypothesis, RosObjectHypothesis)
    rc |= _check("ObjectHypothesisWithPose",   M.ObjectHypothesisWithPose,
                 RosObjectHypothesisWithPose)

    if rc == 0:
        print("\nAll mock classes match real ROS 2 messages on the fields the "
              "bridge actually reads.")
    else:
        print("\nSome mocks have fields the real ROS 2 messages don't expose. "
              "Update ros2_mocks.py or the converter accordingly.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
