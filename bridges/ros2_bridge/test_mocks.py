"""Mock ROS 2 message classes — zero ROS 2 dependencies.

The conversion layer is duck-typed: it operates on objects with the same
field names as real ROS 2 messages, so these mocks can stand in for
``sensor_msgs/PoseStamped`` etc. in unit tests.

When the bridge is run on a real ROS 2 host, ``rclpy`` provides the actual
message classes; these mocks aren't imported. ``verify_mocks.py`` confirms
mock field names match the real ROS 2 IDL — see the README.

v0 covers only the 5 message types in scope:

  * ``geometry_msgs/PoseStamped``
  * ``sensor_msgs/NavSatFix``
  * ``sensor_msgs/Imu``
  * ``sensor_msgs/CompressedImage``
  * ``vision_msgs/Detection3DArray``  (with its supporting types)

Plus the small geometry primitives every message reuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ---------- Built-in / std primitives ----------------------------------------

@dataclass
class Time:
    sec: int = 0
    nanosec: int = 0


@dataclass
class Header:
    stamp: Time = field(default_factory=Time)
    frame_id: str = ""


# ---------- geometry_msgs ----------------------------------------------------

@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Point:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Quaternion:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


@dataclass
class Pose:
    position: Point = field(default_factory=Point)
    orientation: Quaternion = field(default_factory=Quaternion)


@dataclass
class PoseStamped:
    header: Header = field(default_factory=Header)
    pose: Pose = field(default_factory=Pose)


@dataclass
class PoseWithCovariance:
    pose: Pose = field(default_factory=Pose)
    # Row-major 6x6 covariance, length 36
    covariance: List[float] = field(default_factory=lambda: [0.0] * 36)


# ---------- sensor_msgs ------------------------------------------------------

@dataclass
class NavSatStatus:
    STATUS_NO_FIX: int = -1
    STATUS_FIX: int = 0
    STATUS_SBAS_FIX: int = 1
    STATUS_GBAS_FIX: int = 2
    SERVICE_GPS: int = 1
    status: int = 0      # = STATUS_FIX
    service: int = 1     # = SERVICE_GPS


@dataclass
class NavSatFix:
    header: Header = field(default_factory=Header)
    status: NavSatStatus = field(default_factory=NavSatStatus)
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    # 9-element row-major position covariance
    position_covariance: List[float] = field(default_factory=lambda: [0.0] * 9)
    position_covariance_type: int = 0


@dataclass
class Imu:
    header: Header = field(default_factory=Header)
    orientation: Quaternion = field(default_factory=Quaternion)
    orientation_covariance: List[float] = field(default_factory=lambda: [0.0] * 9)
    angular_velocity: Vector3 = field(default_factory=Vector3)
    angular_velocity_covariance: List[float] = field(default_factory=lambda: [0.0] * 9)
    linear_acceleration: Vector3 = field(default_factory=Vector3)
    linear_acceleration_covariance: List[float] = field(default_factory=lambda: [0.0] * 9)


@dataclass
class CompressedImage:
    header: Header = field(default_factory=Header)
    format: str = "jpeg"        # "jpeg", "png", "jpeg compressed bgr8", etc.
    data: bytes = b""


# ---------- vision_msgs ------------------------------------------------------

@dataclass
class ObjectHypothesis:
    class_id: str = ""
    score: float = 0.0


@dataclass
class ObjectHypothesisWithPose:
    hypothesis: ObjectHypothesis = field(default_factory=ObjectHypothesis)
    pose: PoseWithCovariance = field(default_factory=PoseWithCovariance)


@dataclass
class BoundingBox3D:
    center: Pose = field(default_factory=Pose)
    size: Vector3 = field(default_factory=Vector3)


@dataclass
class Detection3D:
    header: Header = field(default_factory=Header)
    results: List[ObjectHypothesisWithPose] = field(default_factory=list)
    bbox: BoundingBox3D = field(default_factory=BoundingBox3D)
    id: str = ""


@dataclass
class Detection3DArray:
    header: Header = field(default_factory=Header)
    detections: List[Detection3D] = field(default_factory=list)


# ---------- Factory helpers --------------------------------------------------

def make_test_pose_stamped(x: float = 10.0, y: float = 20.0, z: float = 0.5,
                            frame_id: str = "map") -> PoseStamped:
    return PoseStamped(
        header=Header(stamp=Time(sec=100, nanosec=500_000_000), frame_id=frame_id),
        pose=Pose(
            position=Point(x=x, y=y, z=z),
            orientation=Quaternion(x=0.0, y=0.0, z=0.383, w=0.924),
        ),
    )


def make_test_nav_sat_fix(lat: float = 30.267, lon: float = -97.743,
                           alt: float = 150.0, frame_id: str = "gnss") -> NavSatFix:
    return NavSatFix(
        header=Header(stamp=Time(sec=100, nanosec=0), frame_id=frame_id),
        status=NavSatStatus(status=0, service=1),
        latitude=lat, longitude=lon, altitude=alt,
        position_covariance=[1.0, 0, 0, 0, 1.0, 0, 0, 0, 4.0],
        position_covariance_type=2,
    )


def make_test_imu(frame_id: str = "imu_link") -> Imu:
    return Imu(
        header=Header(stamp=Time(sec=100, nanosec=250_000_000), frame_id=frame_id),
        orientation=Quaternion(x=0.0, y=0.0, z=0.01, w=0.9999),
        angular_velocity=Vector3(x=0.001, y=-0.002, z=0.05),
        linear_acceleration=Vector3(x=0.1, y=-0.05, z=9.78),
    )


def make_test_compressed_image(frame_id: str = "camera_optical",
                                fmt: str = "jpeg") -> CompressedImage:
    return CompressedImage(
        header=Header(stamp=Time(sec=100, nanosec=750_000_000), frame_id=frame_id),
        format=fmt,
        # Tiny synthetic JPEG header bytes; just need some bytes for the test
        data=b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01" + b"\x00" * 32,
    )


def make_test_detection3d_array(n: int = 3, frame_id: str = "map") -> Detection3DArray:
    detections: List[Detection3D] = []
    for i in range(n):
        det = Detection3D(
            header=Header(stamp=Time(sec=100), frame_id=frame_id),
            id=f"det_{i}",
            results=[
                ObjectHypothesisWithPose(
                    hypothesis=ObjectHypothesis(class_id="vehicle.car", score=0.7 + 0.05 * i),
                ),
                ObjectHypothesisWithPose(
                    hypothesis=ObjectHypothesis(class_id="vehicle.truck", score=0.3),
                ),
            ],
            bbox=BoundingBox3D(
                center=Pose(
                    position=Point(x=float(i * 10), y=5.0, z=1.0),
                    orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
                ),
                size=Vector3(x=4.5, y=1.8, z=1.6),
            ),
        )
        detections.append(det)
    return Detection3DArray(
        header=Header(stamp=Time(sec=100), frame_id=frame_id),
        detections=detections,
    )
