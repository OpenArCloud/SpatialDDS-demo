#!/usr/bin/env python3
"""Subset of SpatialDDS-like dataclasses used by the nuScenes demo."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List


@dataclass
class Time:
    sec: int
    nanosec: int


@dataclass
class Vec3:
    x: float
    y: float
    z: float


@dataclass
class QuaternionXYZW:
    x: float
    y: float
    z: float
    w: float


@dataclass
class FrameRef:
    uuid: str
    fqn: str


@dataclass
class PoseSE3:
    t: Vec3
    q: QuaternionXYZW


@dataclass
class GeoPose:
    lat_deg: float
    lon_deg: float
    alt_m: float
    q: List[float]
    frame_kind: str
    frame_ref: FrameRef
    stamp: Time
    cov: str = "COV_NONE"


@dataclass
class BlobRef:
    blob_id: str
    role: str
    checksum: str = ""


@dataclass
class FrameHeader:
    stream_id: str
    frame_seq: int
    t_start: Time
    t_end: Time
    blobs: List[BlobRef] = field(default_factory=list)
    has_sensor_pose: bool = False


@dataclass
class StreamMeta:
    stream_id: str
    frame_ref: FrameRef
    T_bus_sensor: PoseSE3
    nominal_rate_hz: float


@dataclass
class CamIntrinsics:
    model: str
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int


@dataclass
class VisionMeta:
    stream_id: str
    base: StreamMeta
    pix: str
    codec: str
    cam: CamIntrinsics
    rig_id: str
    schema_version: str


@dataclass
class VisionFrame:
    stream_id: str
    hdr: FrameHeader
    schema_version: str
    codec: str = "JPEG"


@dataclass
class LidarMeta:
    stream_id: str
    base: StreamMeta
    sensor_type: str
    n_rings: int
    point_layout: str
    max_range_m: float
    schema_version: str


@dataclass
class LidarFrame:
    stream_id: str
    hdr: FrameHeader
    schema_version: str


@dataclass
class RadDetection:
    xyz_m: Vec3
    rcs_dbsm: float
    has_velocity_compensated: bool
    vx_compensated: float
    vy_compensated: float
    has_dyn_prop: bool
    dyn_prop: int


@dataclass
class RadDetectionSet:
    stream_id: str
    frame_seq: int
    detections: List[RadDetection]
    stamp: Time
    schema_version: str


@dataclass
class Detection3D:
    det_id: str
    center: Vec3
    size: Vec3
    q: QuaternionXYZW
    class_id: str
    score: float
    has_visibility: bool = False
    visibility: float = 0.0
    has_num_lidar_pts: bool = False
    num_lidar_pts: int = 0
    has_num_radar_pts: bool = False
    num_radar_pts: int = 0
    has_velocity: bool = False
    velocity: Vec3 = field(default_factory=lambda: Vec3(0.0, 0.0, 0.0))


@dataclass
class Detection3DSet:
    frame_seq: int
    stamp: Time
    detections: List[Detection3D]


def to_dict(obj: object) -> Dict:
    return asdict(obj)
