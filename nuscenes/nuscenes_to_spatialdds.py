#!/usr/bin/env python3
"""nuScenes SDK to SpatialDDS-like dataclass mapping helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
from nuscenes.nuscenes import NuScenes

from spatialdds_types import (
    BlobRef,
    CamIntrinsics,
    Detection3D,
    Detection3DSet,
    FrameHeader,
    FrameRef,
    GeoPose,
    LidarFrame,
    LidarMeta,
    PoseSE3,
    QuaternionXYZW,
    RadDetection,
    RadDetectionSet,
    StreamMeta,
    Time,
    Vec3,
    VisionFrame,
    VisionMeta,
)


def ns_time_to_time(ns_timestamp: int) -> Time:
    sec = int(ns_timestamp // 1_000_000)
    nanosec = int((ns_timestamp % 1_000_000) * 1000)
    return Time(sec=sec, nanosec=nanosec)


def quaternion_wxyz_to_xyzw(q: List[float]) -> QuaternionXYZW:
    return QuaternionXYZW(x=float(q[1]), y=float(q[2]), z=float(q[3]), w=float(q[0]))


def pose_from_translation_rotation(translation: List[float], rotation_wxyz: List[float]) -> PoseSE3:
    return PoseSE3(
        t=Vec3(x=float(translation[0]), y=float(translation[1]), z=float(translation[2])),
        q=quaternion_wxyz_to_xyzw(rotation_wxyz),
    )


def ego_pose_to_spatialdds(ego_pose: Dict[str, Any]) -> Tuple[PoseSE3, GeoPose]:
    stamp = ns_time_to_time(int(ego_pose["timestamp"]))
    pose = pose_from_translation_rotation(ego_pose["translation"], ego_pose["rotation"])
    geopose = GeoPose(
        lat_deg=float(ego_pose["translation"][1]),
        lon_deg=float(ego_pose["translation"][0]),
        alt_m=float(ego_pose["translation"][2]),
        q=[pose.q.x, pose.q.y, pose.q.z, pose.q.w],
        stamp=stamp,
    )
    return pose, geopose


def _sensor_frame_ref(calibrated_sensor: Dict[str, Any], channel: str) -> FrameRef:
    return FrameRef(uuid=calibrated_sensor["token"], fqn=f"ego/{channel}")


def _stream_meta(sample_data: Dict[str, Any], calibrated_sensor: Dict[str, Any], rate_hz: float) -> StreamMeta:
    channel = sample_data["channel"]
    return StreamMeta(
        stream_id=channel,
        frame_ref=_sensor_frame_ref(calibrated_sensor, channel),
        T_bus_sensor=pose_from_translation_rotation(
            calibrated_sensor["translation"], calibrated_sensor["rotation"]
        ),
        nominal_rate_hz=float(rate_hz),
    )


def camera_to_vision_meta(sample_data: Dict[str, Any], calibrated_sensor: Dict[str, Any]) -> VisionMeta:
    intr = calibrated_sensor["camera_intrinsic"]
    width = int(sample_data.get("width", 1600))
    height = int(sample_data.get("height", 900))
    return VisionMeta(
        stream_id=sample_data["channel"],
        base=_stream_meta(sample_data, calibrated_sensor, rate_hz=12.0),
        pix="RGB8",
        codec="JPEG",
        cam=CamIntrinsics(
            model="PINHOLE",
            fx=float(intr[0][0]),
            fy=float(intr[1][1]),
            cx=float(intr[0][2]),
            cy=float(intr[1][2]),
            width=width,
            height=height,
        ),
        rig_id="ego",
        schema_version="spatial.sensing.vision/1.7",
    )


def sample_data_to_vision_frame(sample_data: Dict[str, Any], frame_seq: int) -> VisionFrame:
    stamp = ns_time_to_time(int(sample_data["timestamp"]))
    channel = sample_data["channel"]
    return VisionFrame(
        stream_id=channel,
        hdr=FrameHeader(
            stream_id=channel,
            frame_seq=frame_seq,
            t_start=stamp,
            t_end=stamp,
            blobs=[BlobRef(blob_id=sample_data["filename"], role="image")],
            has_sensor_pose=False,
        ),
        schema_version="spatial.sensing.vision/1.7",
    )


def lidar_to_meta_and_frame(
    sample_data: Dict[str, Any], calibrated_sensor: Dict[str, Any], frame_seq: int
) -> Tuple[LidarMeta, LidarFrame]:
    channel = sample_data["channel"]
    stamp = ns_time_to_time(int(sample_data["timestamp"]))
    meta = LidarMeta(
        stream_id=channel,
        base=_stream_meta(sample_data, calibrated_sensor, rate_hz=20.0),
        sensor_type="MULTI_BEAM_3D",
        n_rings=32,
        point_layout="XYZ_I_R",
        max_range_m=100.0,
        schema_version="spatial.sensing.lidar/1.7",
    )
    frame = LidarFrame(
        stream_id=channel,
        hdr=FrameHeader(
            stream_id=channel,
            frame_seq=frame_seq,
            t_start=stamp,
            t_end=stamp,
            blobs=[BlobRef(blob_id=sample_data["filename"], role="lidar_bin")],
            has_sensor_pose=False,
        ),
        schema_version="spatial.sensing.lidar/1.7",
    )
    return meta, frame


def radar_to_detection_set(
    sample_data: Dict[str, Any], radar_points: np.ndarray, frame_seq: int
) -> RadDetectionSet:
    detections: List[RadDetection] = []
    for i in range(radar_points.shape[1]):
        p = radar_points[:, i]
        detections.append(
            RadDetection(
                xyz_m=Vec3(x=float(p[0]), y=float(p[1]), z=float(p[2])),
                rcs_dbsm=float(p[5]),
                has_velocity_compensated=True,
                vx_compensated=float(p[6]),
                vy_compensated=float(p[7]),
                has_dyn_prop=True,
                dyn_prop=int(p[3]),
            )
        )
    return RadDetectionSet(
        stream_id=sample_data["channel"],
        frame_seq=frame_seq,
        detections=detections,
        stamp=ns_time_to_time(int(sample_data["timestamp"])),
        schema_version="spatial.sensing.rad/1.7",
    )


def annotation_to_detection3d(nusc: NuScenes, ann_token: str) -> Detection3D:
    ann = nusc.get("sample_annotation", ann_token)
    q = quaternion_wxyz_to_xyzw(ann["rotation"])
    velocity = nusc.box_velocity(ann_token)
    vx, vy, vz = velocity if velocity is not None else (0.0, 0.0, 0.0)
    if np.isnan(vx):
        vx, vy, vz = 0.0, 0.0, 0.0

    visibility_level = 0.0
    if ann.get("visibility_token"):
        vis = nusc.get("visibility", ann["visibility_token"])
        try:
            visibility_level = float(vis.get("level", 0.0)) / 4.0
        except Exception:
            visibility_level = 0.0

    return Detection3D(
        det_id=ann["token"],
        center=Vec3(
            x=float(ann["translation"][0]),
            y=float(ann["translation"][1]),
            z=float(ann["translation"][2]),
        ),
        size=Vec3(
            x=float(ann["size"][0]),  # width
            y=float(ann["size"][2]),  # height
            z=float(ann["size"][1]),  # depth
        ),
        q=q,
        class_id=str(ann["category_name"]),
        score=1.0,
        has_visibility=True,
        visibility=visibility_level,
        has_num_lidar_pts=True,
        num_lidar_pts=int(ann.get("num_lidar_pts", 0)),
        has_num_radar_pts=True,
        num_radar_pts=int(ann.get("num_radar_pts", 0)),
        has_velocity=True,
        velocity=Vec3(x=float(vx), y=float(vy), z=float(vz)),
    )


def sample_annotations_to_set(nusc: NuScenes, sample: Dict[str, Any], frame_seq: int) -> Detection3DSet:
    timestamp = int(sample["timestamp"])
    dets = [annotation_to_detection3d(nusc, t) for t in sample["anns"]]
    return Detection3DSet(frame_seq=frame_seq, stamp=ns_time_to_time(timestamp), detections=dets)
