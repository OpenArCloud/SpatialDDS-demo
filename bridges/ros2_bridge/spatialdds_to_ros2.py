"""SpatialDDS payload-dict → ROS 2 message converters.

Reverse direction of ``ros2_to_spatialdds``. Inputs are JSON-decoded payload
dicts pulled out of the envelope's ``payload_json`` field; outputs are the
mock dataclasses from ``test_mocks`` (which have the same field shapes as
the real ``rclpy`` message classes — see ``verify_mocks.py``).

When the bridge runs on a real ROS 2 host, ``bridge_node.py`` substitutes
real ``rclpy`` message classes for the mocks via the ``factories`` argument
on each function. Tier-1 tests use the mocks directly.

NO ROS 2 imports. NO DDS imports.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from frame_mapping import FrameMapper

# Default factories: mock classes from test_mocks. The bridge node injects
# real rclpy classes at runtime by passing a different ``factories`` dict.
from test_mocks import (  # noqa: E402
    BoundingBox3D,
    CompressedImage,
    Detection3D,
    Detection3DArray,
    Header,
    Imu,
    NavSatFix,
    NavSatStatus,
    ObjectHypothesis,
    ObjectHypothesisWithPose,
    Point,
    Pose,
    PoseStamped,
    Quaternion,
    Time,
    Vector3,
)


# ---------- §3.3.2 types consumed on this side -------------------------------
# The type a topic announces decides the decoder. These used to be tuples of
# demo-private `ROS2_*` / `NUSC_*` / `DEEPSENSE_*` labels — every publisher
# invented its own name for the same thing, so every consumer kept an alias
# list. One registry name per type now.
SDDS_MSG_TYPES_DETECTION3D = ("oarc.detection3d_velocity", "radar_detection")
SDDS_MSG_TYPES_FUSED_TRACK = ("oarc.fused_track",)
SDDS_MSG_TYPES_FRAMED_POSE = ("oarc.framed_pose",)
SDDS_MSG_TYPES_GEO_POSE = ("geopose",)
SDDS_MSG_TYPES_IMU = ("oarc.imu_sample",)
SDDS_MSG_TYPES_VISION = ("video_frame",)


# ---------- Primitive helpers ------------------------------------------------

def _stamp(d: Optional[Dict[str, Any]]) -> Time:
    if not isinstance(d, dict):
        return Time(0, 0)
    return Time(sec=int(d.get("sec", 0) or 0),
                nanosec=int(d.get("nanosec", 0) or 0))


def _header(payload: Dict[str, Any], frame_mapper: FrameMapper,
             stamp_field: str = "stamp", frame_field: str = "frame_ref") -> Header:
    """Reconstruct a ROS 2 Header from a payload's stamp + frame_ref."""
    return Header(
        stamp=_stamp(payload.get(stamp_field)),
        frame_id=frame_mapper.frame_ref_to_frame_id(payload.get(frame_field) or {}),
    )


def _xyz(value: Any, default=(0.0, 0.0, 0.0)) -> tuple:
    """
    A Vec3 as (x, y, z).

    Vec3 is ``double[3]`` in the IDL, so it arrives as an array. The
    ``{"x": …}`` form is still accepted because the nuScenes and DeepSense
    converters have not been migrated yet.
    """
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return tuple(float(v) for v in value[:3])
    if isinstance(value, dict):
        return tuple(float(value.get(k, d)) for k, d in zip("xyz", default))
    return tuple(default)


def _vec3(value: Any) -> Vector3:
    x, y, z = _xyz(value)
    return Vector3(x=x, y=y, z=z)


def _point(value: Any) -> Point:
    x, y, z = _xyz(value)
    return Point(x=x, y=y, z=z)


def _quat(value: Any) -> Quaternion:
    """A QuaternionXYZW as a ROS 2 Quaternion. Identity when absent."""
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        x, y, z, w = (float(v) for v in value[:4])
        return Quaternion(x=x, y=y, z=z, w=w)
    d = value if isinstance(value, dict) else {}
    return Quaternion(x=float(d.get("x", 0.0) or 0.0),
                       y=float(d.get("y", 0.0) or 0.0),
                       z=float(d.get("z", 0.0) or 0.0),
                       w=float(d.get("w", 1.0) or 1.0))


# ---------- Decoders ---------------------------------------------------------

def framed_pose_to_pose_stamped(payload: Dict[str, Any],
                                  frame_mapper: FrameMapper) -> PoseStamped:
    """``spatial::core::FramedPose`` -> ``geometry_msgs/PoseStamped``."""
    pose = payload.get("pose") or {}
    # FramedPose nests the PoseSE3 inside the frame it is expressed in; the
    # nuScenes publisher still emits a bare pose_se3.
    if isinstance(pose.get("pose"), dict):
        pose = pose["pose"]
    return PoseStamped(
        header=_header(payload, frame_mapper),
        pose=Pose(position=_point(pose.get("t") or pose.get("position")),
                  orientation=_quat(pose.get("q") or pose.get("orientation"))),
    )


def geo_pose_to_nav_sat_fix(payload: Dict[str, Any],
                            frame_mapper: FrameMapper,
                            status: Optional[Dict[str, Any]] = None,
                            sensor_id: str = "gnss") -> NavSatFix:
    """
    ``spatial::core::GeoPose`` -> ``sensor_msgs/NavSatFix``.

    GeoPose has no frame_ref and no fix status: it is lat/lon/alt, an
    orientation, a stamp and a covariance. The fix status lives on the
    registered companion type, `navsat_status`, which arrives on its own
    topic — pass it as ``status`` when the caller has correlated the two.
    """
    cov, cov_type = _cov3_from(payload.get("cov"))
    return NavSatFix(
        header=Header(stamp=_stamp(payload.get("stamp")),
                      frame_id=str(sensor_id)),
        status=NavSatStatus(status=_ros_fix_status(status), service=1),
        latitude=float(payload.get("lat_deg", 0.0) or 0.0),
        longitude=float(payload.get("lon_deg", 0.0) or 0.0),
        altitude=float(payload.get("alt_m", 0.0) or 0.0),
        position_covariance=cov,
        position_covariance_type=cov_type,
    )


# spatial::core::GnssFixType -> sensor_msgs/NavSatStatus.status. The spec's
# enum is finer than ROS 2's four values, so this collapses upward.
_SDDS_FIX_TO_ROS = {
    "NO_FIX": -1, "DEAD_RECKONING": -1,
    "FIX_2D": 0, "FIX_3D": 0, "GNSS_DR": 0,
    "SBAS": 1,
    "DGPS": 2, "RTK_FLOAT": 2, "RTK_FIXED": 2, "PPP": 2,
}


def _ros_fix_status(status: Optional[Dict[str, Any]]) -> int:
    if not isinstance(status, dict):
        return 0                       # STATUS_FIX; NavSatFix has no "unknown"
    return _SDDS_FIX_TO_ROS.get(str(status.get("fix_type") or ""), 0)


def _cov3_from(cov: Any) -> tuple:
    """A ``CovMatrix`` union back to (9-element list, NavSatFix cov type)."""
    if isinstance(cov, dict) and cov.get("discriminator") == "COV_POS3":
        values = [float(v) for v in (cov.get("pos") or [])]
        if len(values) == 9:
            return values, 3           # COVARIANCE_TYPE_KNOWN
    return [0.0] * 9, 0                # COVARIANCE_TYPE_UNKNOWN


def imu_sample_to_imu(payload: Dict[str, Any],
                       frame_mapper: FrameMapper) -> Imu:
    """
    ``spatial::vio::ImuSample`` -> ``sensor_msgs/Imu``.

    ImuSample is `(imu_id, accel, gyro, stamp, source_id, seq)`. It carries
    no orientation and no covariances, so the REP-145 sentinel
    (`orientation_covariance[0] == -1`) is always set: "orientation not
    provided" is the truth, not a fallback. ImuSample names a `source_id`,
    not a frame, so the frame_id comes from `imu_id`.
    """
    return Imu(
        header=Header(stamp=_stamp(payload.get("stamp")),
                      frame_id=str(payload.get("imu_id") or "")),
        orientation=_quat(None),
        orientation_covariance=[-1.0] + [0.0] * 8,
        angular_velocity=_vec3(payload.get("gyro")),
        linear_acceleration=_vec3(payload.get("accel")),
    )


def vision_frame_to_compressed_image(payload: Dict[str, Any],
                                     frame_mapper: FrameMapper,
                                     data: Optional[bytes] = None
                                     ) -> CompressedImage:
    """
    ``spatial::sensing::vision::VisionFrame`` -> ``sensor_msgs/CompressedImage``.

    The bytes are not in the frame message and are not meant to be: a
    VisionFrame is metadata plus a `BlobRef`, and the bytes travel as blob
    chunks. Pass the reassembled blob as ``data`` when the caller has it;
    without it the image is empty and only the metadata crosses.
    """
    codec = str(payload.get("codec") or "").upper()
    fmt = "jpeg" if codec == "JPEG" else codec.lower() or "raw"
    return CompressedImage(
        header=Header(stamp=_stamp((payload.get("hdr") or {}).get("t_start")),
                      frame_id=str(payload.get("stream_id") or "")),
        format=fmt,
        data=bytes(data or b""),
    )


def vision_frame_blob_id(payload: Dict[str, Any]) -> str:
    """The blob id whose chunks carry this frame's bytes, or ``""``."""
    blobs = (payload.get("hdr") or {}).get("blobs") or []
    for ref in blobs:
        if isinstance(ref, dict) and ref.get("role") == "image":
            return str(ref.get("blob_id") or "")
    return ""


def detection3d_set_to_array(payload: Dict[str, Any],
                              frame_mapper: FrameMapper) -> Detection3DArray:
    """Detection3DSet / FusedTrackSet payload → ``vision_msgs/Detection3DArray``.

    Handles three slightly different shapes we receive on the bus:

      * ``ROS2_DETECTION3D_SET``  — emitted by this bridge.
      * ``NUSC_DET3D_SET``        — nuScenes publisher; same field names.
      * ``NUSC_FUSED_TRACK_SET``  — multi-operator fusion service. Per-track
        records carry ``track_id`` instead of ``det_id`` and may carry
        per-source provenance — we map them in as Detection3D records,
        preserving the score and class.

    The ``source_operator`` from the payload is hoisted into each
    detection's ``id`` (``"{operator}/{det_or_track_id}"``) so downstream
    ROS 2 consumers can disambiguate detections from different operators
    when multiple bridges feed the same ``/fused/detections_3d`` topic.
    """
    header = _header(payload, frame_mapper)
    src = str(payload.get("source_operator") or "")
    raw_items = (payload.get("dets") or payload.get("detections")
                 or payload.get("tracks") or [])

    detections: List[Detection3D] = []
    for i, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        # OperatorDetectionSet composes the spec Detection3D under
        # `detection`; a FusedTrack is flat.
        if isinstance(item.get("detection"), dict):
            item = item["detection"]
        det_id = (
            item.get("det_id")
            or item.get("track_id")
            or f"det_{i}"
        )
        prefixed_id = f"{src}/{det_id}" if src else str(det_id)
        class_id = item.get("class_id") or "unknown"
        score = float(item.get("score", 0.0) or 0.0)

        center = item.get("center") or item.get("position")
        size = item.get("size")
        q = item.get("q")

        detections.append(Detection3D(
            header=header,
            id=str(prefixed_id),
            results=[ObjectHypothesisWithPose(
                hypothesis=ObjectHypothesis(class_id=str(class_id), score=score),
            )],
            bbox=BoundingBox3D(
                center=Pose(position=_point(center), orientation=_quat(q)),
                size=_vec3(size),
            ),
        ))

    return Detection3DArray(header=header, detections=detections)


# ---------- Dispatch helpers -------------------------------------------------

def msg_type_to_decoder(msg_type: str
                         ) -> Optional[Callable[[Dict[str, Any], FrameMapper], Any]]:
    """Map an envelope msg_type string to a decoder, or None if unsupported."""
    if msg_type in SDDS_MSG_TYPES_DETECTION3D or msg_type in SDDS_MSG_TYPES_FUSED_TRACK:
        return detection3d_set_to_array
    if msg_type in SDDS_MSG_TYPES_FRAMED_POSE:
        return framed_pose_to_pose_stamped
    if msg_type in SDDS_MSG_TYPES_GEO_POSE:
        return geo_pose_to_nav_sat_fix
    if msg_type in SDDS_MSG_TYPES_IMU:
        return imu_sample_to_imu
    if msg_type in SDDS_MSG_TYPES_VISION:
        return vision_frame_to_compressed_image
    return None
