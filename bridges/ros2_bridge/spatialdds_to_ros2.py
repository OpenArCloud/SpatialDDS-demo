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


# ---------- Wire-format msg_type strings consumed on this side ---------------
# These are the msg_types we know how to translate back into ROS 2 messages.
# Bridge subscribers filter on these to decide which decoder to invoke.
SDDS_MSG_TYPES_DETECTION3D = (
    "ROS2_DETECTION3D_SET",
    "NUSC_DET3D_SET",
    "NUSC_FUSED_TRACK_SET",   # the multi-operator fusion service publishes this
)
SDDS_MSG_TYPES_FRAMED_POSE = ("ROS2_FRAMED_POSE", "NUSC_EGO_POSE")
SDDS_MSG_TYPES_GEO_POSE = ("ROS2_GEO_POSE", "DEEPSENSE_UNIT1_GEOPOSE",
                            "DEEPSENSE_UNIT2_GEOPOSE")
SDDS_MSG_TYPES_IMU = ("ROS2_IMU_SAMPLE",)
SDDS_MSG_TYPES_VISION = ("ROS2_VISION_FRAME", "DEEPSENSE_VISION_FRAME",
                          "NUSC_VISION_FRAME")


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


def _vec3(d: Optional[Dict[str, Any]]) -> Vector3:
    d = d or {}
    return Vector3(x=float(d.get("x", 0.0) or 0.0),
                    y=float(d.get("y", 0.0) or 0.0),
                    z=float(d.get("z", 0.0) or 0.0))


def _point(d: Optional[Dict[str, Any]]) -> Point:
    d = d or {}
    return Point(x=float(d.get("x", 0.0) or 0.0),
                  y=float(d.get("y", 0.0) or 0.0),
                  z=float(d.get("z", 0.0) or 0.0))


def _quat(d: Optional[Dict[str, Any]]) -> Quaternion:
    d = d or {}
    return Quaternion(x=float(d.get("x", 0.0) or 0.0),
                       y=float(d.get("y", 0.0) or 0.0),
                       z=float(d.get("z", 0.0) or 0.0),
                       w=float(d.get("w", 1.0) or 1.0))


# ---------- Decoders ---------------------------------------------------------

def framed_pose_to_pose_stamped(payload: Dict[str, Any],
                                  frame_mapper: FrameMapper) -> PoseStamped:
    """``FramedPose`` payload (from ROS2_FRAMED_POSE or NUSC_EGO_POSE) → PoseStamped."""
    pose = payload.get("pose") or {}
    t = pose.get("t") or pose.get("position") or {}
    q = pose.get("q") or pose.get("orientation") or {}
    return PoseStamped(
        header=_header(payload, frame_mapper),
        pose=Pose(position=_point(t), orientation=_quat(q)),
    )


def geo_pose_to_nav_sat_fix(payload: Dict[str, Any],
                              frame_mapper: FrameMapper) -> NavSatFix:
    """SpatialDDS GeoPose payload → ``sensor_msgs/NavSatFix``."""
    fix_status = int(payload.get("fix_status", 0) or 0)
    cov = list(payload.get("position_covariance") or [0.0] * 9)
    if len(cov) != 9:
        cov = [0.0] * 9
    sensor_id = payload.get("sensor_id") or "gnss"
    return NavSatFix(
        header=Header(
            stamp=_stamp(payload.get("stamp")),
            # GeoPose uses sensor_id (not frame_ref) in our shape; reverse-map
            # via FrameMapper so existing frames are preserved.
            frame_id=frame_mapper.frame_ref_to_frame_id(
                payload.get("frame_ref") or {"fqn": sensor_id}
            ),
        ),
        status=NavSatStatus(status=fix_status, service=1),
        latitude=float(payload.get("lat_deg", 0.0) or 0.0),
        longitude=float(payload.get("lon_deg", 0.0) or 0.0),
        altitude=float(payload.get("alt_m", 0.0) or 0.0),
        position_covariance=[float(c) for c in cov],
        position_covariance_type=int(payload.get("position_covariance_type", 0) or 0),
    )


def imu_sample_to_imu(payload: Dict[str, Any],
                       frame_mapper: FrameMapper) -> Imu:
    """SpatialDDS ImuSample payload → ``sensor_msgs/Imu``."""
    has_orientation = bool(payload.get("has_orientation", False))
    return Imu(
        header=_header(payload, frame_mapper),
        orientation=_quat(payload.get("orientation") if has_orientation else None),
        # REP-145 sentinel for "no orientation provided"
        orientation_covariance=[0.0] * 9 if has_orientation else [-1.0] + [0.0] * 8,
        angular_velocity=_vec3(payload.get("angular_velocity")),
        linear_acceleration=_vec3(payload.get("linear_acceleration")),
    )


def vision_frame_to_compressed_image(payload: Dict[str, Any],
                                       frame_mapper: FrameMapper) -> CompressedImage:
    """SpatialDDS VisionFrame payload → ``sensor_msgs/CompressedImage``.

    Uses ``data_hex`` if present (ROS2_VISION_FRAME emits this for inline
    payload). Falls back to an empty byte string when the payload only
    references an external blob (e.g. NUSC_VISION_FRAME with BlobRef).
    """
    codec = (payload.get("codec") or "").lower()
    if "png" in codec:
        fmt = "png"
    elif "jpeg" in codec or "jpg" in codec:
        fmt = "jpeg"
    else:
        fmt = codec or "raw"
    data_hex = payload.get("data_hex")
    if isinstance(data_hex, str) and data_hex:
        try:
            data = bytes.fromhex(data_hex)
        except ValueError:
            data = b""
    else:
        data = b""
    return CompressedImage(
        header=_header(payload, frame_mapper),
        format=fmt,
        data=data,
    )


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
    raw_items = payload.get("detections") or payload.get("tracks") or []

    detections: List[Detection3D] = []
    for i, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        det_id = (
            item.get("det_id")
            or item.get("track_id")
            or f"det_{i}"
        )
        prefixed_id = f"{src}/{det_id}" if src else str(det_id)
        class_id = item.get("class_id") or "unknown"
        score = float(item.get("score", 0.0) or 0.0)

        center = item.get("center") or {}
        size = item.get("size") or {}
        q = item.get("q") or {}

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
    if msg_type in SDDS_MSG_TYPES_DETECTION3D:
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
