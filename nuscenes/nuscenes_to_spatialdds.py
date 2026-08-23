#!/usr/bin/env python3
"""nuScenes SDK to SpatialDDS-like dataclass mapping helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
from nuscenes.nuscenes import NuScenes

from sensor_types import (
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
    FrameQuality,
    RadDetection,
    RadDetectionSet,
    StreamMeta,
    Time,
    Vec3,
    VisionFrame,
    VisionMeta,
)
from spatialdds_idl.spatial.core import CovMatrix, TileKey  # noqa: E402


def ns_time_to_time(ns_timestamp: int) -> Time:
    sec = int(ns_timestamp // 1_000_000)
    nanosec = int((ns_timestamp % 1_000_000) * 1000)
    return Time(sec=sec, nanosec=nanosec)


def quaternion_wxyz_to_xyzw(q: List[float]) -> List[float]:
    """nuScenes stores (w, x, y, z); the spec's QuaternionXYZW is an array."""
    return [float(q[1]), float(q[2]), float(q[3]), float(q[0])]


def pose_from_translation_rotation(translation: List[float],
                                   rotation_wxyz: List[float]) -> PoseSE3:
    return PoseSE3(
        t=[float(translation[0]), float(translation[1]), float(translation[2])],
        q=quaternion_wxyz_to_xyzw(rotation_wxyz),
    )


def _identity_pose() -> PoseSE3:
    return PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0])


def _frame_quality() -> FrameQuality:
    """A "nothing measured" quality block. Every field, flags off."""
    return FrameQuality(has_snr_db=False, snr_db=0.0, percent_valid=100.0,
                        health="OK", note="")


def _frame_header(channel: str, frame_seq: int, stamp: Time,
                  blobs: List[BlobRef]) -> FrameHeader:
    """
    A complete FrameHeader.

    `sensor_pose` is presence-flagged, not optional: the value is always on
    the wire and the flag says whether to read it. nuScenes gives the sensor
    pose through calibrated_sensor rather than per frame, so the flag is off.
    """
    return FrameHeader(
        stream_id=channel, frame_seq=frame_seq, t_start=stamp, t_end=stamp,
        has_sensor_pose=False, sensor_pose=_identity_pose(), blobs=blobs,
    )


def ego_pose_to_spatialdds(ego_pose: Dict[str, Any]) -> Tuple[PoseSE3, GeoPose]:
    stamp = ns_time_to_time(int(ego_pose["timestamp"]))
    pose = pose_from_translation_rotation(ego_pose["translation"], ego_pose["rotation"])
    geopose = GeoPose(
        # nuScenes ego translation is map-local metres, not lat/lon. The demo
        # carries it in the geographic fields anyway so the geo lane has
        # something to show; a real deployment would project it.
        lat_deg=float(ego_pose["translation"][1]),
        lon_deg=float(ego_pose["translation"][0]),
        alt_m=float(ego_pose["translation"][2]),
        q=list(pose.q),
        stamp=stamp,
        cov=CovMatrix(none=0),
    )
    return pose, geopose


def _sensor_frame_ref(calibrated_sensor: Dict[str, Any], channel: str) -> FrameRef:
    return FrameRef(uuid=calibrated_sensor["token"], fqn=f"ego/{channel}",
                    has_coord_convention=True, coord_convention="ENU")


def _stream_meta(sample_data: Dict[str, Any], calibrated_sensor: Dict[str, Any], rate_hz: float) -> StreamMeta:
    channel = sample_data["channel"]
    return StreamMeta(
        stream_id=channel,
        frame_ref=_sensor_frame_ref(calibrated_sensor, channel),
        T_bus_sensor=pose_from_translation_rotation(
            calibrated_sensor["translation"], calibrated_sensor["rotation"]
        ),
        nominal_rate_hz=float(rate_hz),
        schema_version="spatial.sensing.common/1.7",
    )


def camera_to_vision_meta(sample_data: Dict[str, Any], calibrated_sensor: Dict[str, Any]) -> VisionMeta:
    intr = calibrated_sensor["camera_intrinsic"]
    width = int(sample_data.get("width", 1600))
    height = int(sample_data.get("height", 900))
    return VisionMeta(
        stream_id=sample_data["channel"],
        base=_stream_meta(sample_data, calibrated_sensor, rate_hz=12.0),
        # The field is `K`, not `cam` — the camera matrix, named as it is in
        # every calibration convention.
        K=CamIntrinsics(
            model="PINHOLE",
            width=width,
            height=height,
            fx=float(intr[0][0]),
            fy=float(intr[1][1]),
            cx=float(intr[0][2]),
            cy=float(intr[1][2]),
            # nuScenes ships rectified images, so no distortion model.
            dist="NONE",
            dist_params=[],
            shutter_us=0.0,
            readout_us=0.0,
            pix="RGB8",
            color="SRGB",
            calib_version="nuscenes-v1.0",
        ),
        # RigRole names a camera's position in a rig, not its lens type;
        # nuScenes CAM_FRONT and friends map straight onto it.
        role=_RIG_ROLE.get(sample_data["channel"], "AUX"),
        rig_id="ego",
        codec="JPEG",
        pix="RGB8",
        color="SRGB",
        schema_version="spatial.sensing.vision/1.7",
    )


def sample_data_to_vision_frame(sample_data: Dict[str, Any], frame_seq: int) -> VisionFrame:
    stamp = ns_time_to_time(int(sample_data["timestamp"]))
    channel = sample_data["channel"]
    return VisionFrame(
        stream_id=channel,
        frame_seq=frame_seq,
        hdr=_frame_header(channel, frame_seq, stamp, [
            BlobRef(blob_id=sample_data["filename"], role="image", checksum=""),
        ]),
        codec="JPEG",
        pix="RGB8",
        color="SRGB",
        has_line_readout_us=False,
        line_readout_us=0.0,
        rectified=True,
        is_key_frame=True,
        quality=_frame_quality(),
    )


def lidar_to_meta_and_frame(
    sample_data: Dict[str, Any], calibrated_sensor: Dict[str, Any], frame_seq: int
) -> Tuple[LidarMeta, LidarFrame]:
    channel = sample_data["channel"]
    stamp = ns_time_to_time(int(sample_data["timestamp"]))
    meta = LidarMeta(
        stream_id=channel,
        base=_stream_meta(sample_data, calibrated_sensor, rate_hz=20.0),
        type="MULTI_BEAM_3D",
        n_rings=32,
        has_range_limits=True,
        min_range_m=0.9,
        max_range_m=100.0,
        has_horiz_fov=True,
        horiz_fov_deg_min=-180.0,
        horiz_fov_deg_max=180.0,
        has_vert_fov=True,
        vert_fov_deg_min=-30.0,
        vert_fov_deg_max=10.0,
        has_wavelength=False,
        wavelength_nm=0.0,
        # nuScenes ships raw interleaved float32 (x, y, z, intensity, ring).
        encoding="BIN_INTERLEAVED",
        codec="CODEC_NONE",
        layout="XYZ_I_R",
        schema_version="spatial.sensing.lidar/1.7",
    )
    frame = LidarFrame(
        stream_id=channel,
        frame_seq=frame_seq,
        hdr=_frame_header(channel, frame_seq, stamp, [
            BlobRef(blob_id=sample_data["filename"], role="lidar_bin",
                    checksum=""),
        ]),
        encoding="BIN_INTERLEAVED",
        codec="CODEC_NONE",
        layout="XYZ_I_R",
        # nuScenes .bin sweeps carry no per-point timestamps.
        has_per_point_timestamps=False,
        has_average_range_m=False,
        average_range_m=0.0,
        has_percent_valid=False,
        percent_valid=0.0,
        has_quality=False,
        quality=_frame_quality(),
    )
    return meta, frame


def radar_to_detection_set(
    sample_data: Dict[str, Any], radar_points: np.ndarray, frame_seq: int
) -> RadDetectionSet:
    detections: List[RadDetection] = []
    for i in range(radar_points.shape[1]):
        p = radar_points[:, i]
        detections.append(RadDetection(
            xyz_m=[float(p[0]), float(p[1]), float(p[2])],
            has_velocity_xyz=False,
            velocity_xyz=[0.0, 0.0, 0.0],
            has_v_r_mps=False,
            v_r_mps=0.0,
            # nuScenes radar gives ego-motion-compensated vx/vy and no vz.
            has_velocity_comp_xyz=True,
            velocity_comp_xyz=[float(p[6]), float(p[7]), 0.0],
            # `rcs_dbm2`, not `rcs_dbsm`.
            has_rcs_dbm2=True,
            rcs_dbm2=float(p[5]),
            intensity=0.0,
            quality=0.0,
            has_dyn_prop=True,
            dyn_prop=_DYN_PROP[int(p[3])] if int(p[3]) < len(_DYN_PROP) else "UNKNOWN",
            has_pos_rms=False,
            x_rms_m=0.0, y_rms_m=0.0, z_rms_m=0.0,
            has_vel_rms=False,
            vx_rms_mps=0.0, vy_rms_mps=0.0, vz_rms_mps=0.0,
            has_ambig_state=False,
            ambig_state=0,
            has_false_alarm_prob=False,
            false_alarm_prob=0.0,
            has_sensor_track_id=False,
            sensor_track_id=0,
        ))
    channel = sample_data["channel"]
    stamp = ns_time_to_time(int(sample_data["timestamp"]))
    return RadDetectionSet(
        stream_id=channel,
        frame_seq=frame_seq,
        frame_ref=FrameRef(uuid="", fqn=f"ego/{channel}",
                           has_coord_convention=True, coord_convention="ENU"),
        # `dets`, not `detections` — the sequence field is named the same way
        # in every *Set type in the spec.
        dets=detections,
        stamp=stamp,
        source_id="nuscenes",
        seq=frame_seq,
        proc_chain="nuscenes-devkit",
        has_quality=False,
        quality=_frame_quality(),
    )


# nuScenes camera channels map onto RigRole positions.
_RIG_ROLE = {
    "CAM_FRONT": "FRONT",
    "CAM_FRONT_LEFT": "FRONT_LEFT",
    "CAM_FRONT_RIGHT": "FRONT_RIGHT",
    "CAM_BACK": "BACK",
    "CAM_BACK_LEFT": "BACK_LEFT",
    "CAM_BACK_RIGHT": "BACK_RIGHT",
}


# nuScenes radar dyn_prop is an integer code; rad::RadDynProp is an enum.
_DYN_PROP = ["MOVING", "STATIONARY", "ONCOMING", "STATIONARY_CANDIDATE",
             "UNKNOWN", "CROSSING_STATIONARY", "CROSSING_MOVING", "STOPPED"]


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

    # semantics::Detection3D has no velocity field, so the box velocity
    # nuScenes provides has nowhere to go here. The multi-operator demo needs
    # it and composes `oarc_demo::DetectionWithVelocity` around this type for
    # exactly that reason; on the findings list.
    return Detection3D(
        det_id=ann["token"],
        frame_ref=FrameRef(uuid="", fqn="nuscenes/map",
                           has_coord_convention=True, coord_convention="ENU"),
        has_tile=False,
        tile_key=TileKey(x=0, y=0, z=0, level=0),
        class_id=str(ann["category_name"]),
        score=1.0,
        center=[float(ann["translation"][0]), float(ann["translation"][1]),
                float(ann["translation"][2])],
        # nuScenes size is (width, length, height); the spec's Vec3 size is
        # (x, y, z) in the detection's own frame.
        size=[float(ann["size"][0]), float(ann["size"][2]),
              float(ann["size"][1])],
        q=q,
        has_covariance=False,
        cov_pos=[0.0] * 9,
        cov_rot=[0.0] * 9,
        has_track_id=True,
        track_id=str(ann.get("instance_token", "")),
        stamp=ns_time_to_time(int(ann.get("timestamp", 0) or 0)),
        source_id="nuscenes",
        has_attributes=False,
        attributes=[],
        has_visibility=True,
        visibility=visibility_level,
        has_num_pts=True,
        num_lidar_pts=int(ann.get("num_lidar_pts", 0)),
        num_radar_pts=int(ann.get("num_radar_pts", 0)),
    )


def sample_annotations_to_set(nusc: NuScenes, sample: Dict[str, Any], frame_seq: int) -> Detection3DSet:
    timestamp = int(sample["timestamp"])
    dets = [annotation_to_detection3d(nusc, t) for t in sample["anns"]]
    return Detection3DSet(
        set_id=f"nuscenes-{frame_seq}",
        frame_ref=FrameRef(uuid="", fqn="nuscenes/map",
                           has_coord_convention=True, coord_convention="ENU"),
        has_tile=False,
        tile_key=TileKey(x=0, y=0, z=0, level=0),
        dets=dets,
        stamp=ns_time_to_time(timestamp),
        source_id="nuscenes",
    )
