"""ROS 2 message → SpatialDDS payload-dict converters.

Each ``*_to_spatialdds`` function takes a duck-typed ROS 2 message (real
``rclpy`` class or a mock from ``test_mocks``) plus operator/sensor context,
and returns a JSON-serializable dict ready to ride inside the existing
``spatialdds/envelope/v1`` envelope's ``payload_json`` field.

Per repo convention (see ``nuscenes/dds_envelope_transport.py`` /
``multi_operator_fusion/publisher.py``), the bridge emits dicts rather than
typed SpatialDDS dataclasses. This keeps the shared
``nuscenes/spatialdds_types.py`` lean — types are promoted to dataclasses
only when a real consumer needs typed access.

The companion ``envelope_io.py`` ships these dicts on the wire and
``spatialdds_to_ros2.py`` unpacks them on the way back out.

NO ROS 2 imports. NO DDS imports. Pure-Python so Tier-1 tests can run
without either dependency.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from frame_mapping import FrameMapper


# ---------- Schema-version literals ------------------------------------------
# These mirror the v1.7 IDL profile names. 1.7 dropped selective per-profile
# minor bumps: every module versions together with the spec, so every
# MODULE_ID and schema_version is spatial.<profile>/1.7.
SCHEMA_CORE = "spatial.core/1.7"
SCHEMA_VISION = "spatial.sensing.vision/1.7"
SCHEMA_SEMANTICS = "spatial.semantics/1.7"


# ---------- Wire-format msg_type strings -------------------------------------
# Distinct from NUSC_*/DEEPSENSE_* so consumers can filter on origin.
MSG_TYPE_FRAMED_POSE      = "ROS2_FRAMED_POSE"
MSG_TYPE_GEO_POSE         = "ROS2_GEO_POSE"
MSG_TYPE_IMU_SAMPLE       = "ROS2_IMU_SAMPLE"
MSG_TYPE_VISION_FRAME     = "ROS2_VISION_FRAME"
MSG_TYPE_DETECTION3D_SET  = "ROS2_DETECTION3D_SET"


# ---------- Primitive helpers ------------------------------------------------

def _ros_time_to_sdds(stamp: Any) -> Dict[str, int]:
    """builtin_interfaces.msg.Time → SpatialDDS Time dict."""
    return {
        "sec": int(getattr(stamp, "sec", 0) or 0),
        "nanosec": int(getattr(stamp, "nanosec", 0) or 0),
    }


def _stamp_to_ns(stamp: Any) -> int:
    """ROS 2 stamp → integer nanoseconds (handy for envelope log_time)."""
    s = int(getattr(stamp, "sec", 0) or 0)
    n = int(getattr(stamp, "nanosec", 0) or 0)
    return s * 1_000_000_000 + n


def _vec3(v: Any) -> Dict[str, float]:
    return {
        "x": float(getattr(v, "x", 0.0) or 0.0),
        "y": float(getattr(v, "y", 0.0) or 0.0),
        "z": float(getattr(v, "z", 0.0) or 0.0),
    }


def _quat(q: Any) -> Dict[str, float]:
    """ROS 2 ``Quaternion`` (x,y,z,w) → SpatialDDS quaternion (x,y,z,w).
    No reordering — the conventions match."""
    return {
        "x": float(getattr(q, "x", 0.0) or 0.0),
        "y": float(getattr(q, "y", 0.0) or 0.0),
        "z": float(getattr(q, "z", 0.0) or 0.0),
        "w": float(getattr(q, "w", 1.0) or 1.0),
    }


# ---------- Topic builders ---------------------------------------------------

def _topic(operator: str, *segments: str) -> str:
    return "/".join(["spatialdds", operator, *segments])


def topic_for_pose(operator: str) -> str:
    return _topic(operator, "ego/pose/v1")


def topic_for_geo_pose(operator: str, sensor_id: str) -> str:
    return _topic(operator, "geo", sensor_id, "pose/v1")


def topic_for_imu(operator: str, sensor_id: str) -> str:
    return _topic(operator, "imu", sensor_id, "sample/v1")


def topic_for_vision_frame(operator: str, sensor_id: str) -> str:
    return _topic(operator, "vision", sensor_id, "frame/v1")


def topic_for_detection3d(operator: str) -> str:
    return _topic(operator, "sensing/detection3d/v1")


# ---------- Converters -------------------------------------------------------

def pose_stamped_to_framed_pose(msg: Any, operator: str,
                                 frame_mapper: FrameMapper) -> Dict[str, Any]:
    """``geometry_msgs/PoseStamped`` → SpatialDDS FramedPose payload.

    Output keys
    -----------
      ``schema_version``   spec profile literal
      ``source_operator``  operator namespace (also embedded in topic)
      ``frame_ref``        FrameRef from header.frame_id
      ``stamp``            ``{sec, nanosec}``
      ``pose``             ``{t: Vec3, q: QuaternionXYZW}``
    """
    h = msg.header
    return {
        "schema_version": SCHEMA_CORE,
        "source_operator": operator,
        "frame_ref": frame_mapper.frame_id_to_frame_ref(h.frame_id),
        "stamp": _ros_time_to_sdds(h.stamp),
        "pose": {"t": _vec3(msg.pose.position), "q": _quat(msg.pose.orientation)},
    }


def nav_sat_fix_to_geo_pose(msg: Any, operator: str,
                             sensor_id: str = "gnss") -> Dict[str, Any]:
    """``sensor_msgs/NavSatFix`` → SpatialDDS GeoPose payload.

    The status (no-fix / fix / SBAS / GBAS) is preserved as ``fix_status``
    so consumers can drop unfixed samples. Position covariance is forwarded
    when present.

    1.7 removed ``GeoPose.frame_kind``: orientation is fixed to the local
    ENU tangent frame at the encoded position, which is what NavSatFix
    already implies, so the field (and its parameter) is simply gone.
    """
    h = msg.header
    cov_type = int(getattr(msg, "position_covariance_type", 0) or 0)
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_CORE,
        "source_operator": operator,
        "sensor_id": sensor_id,
        "stamp": _ros_time_to_sdds(h.stamp),
        "lat_deg": float(msg.latitude),
        "lon_deg": float(msg.longitude),
        "alt_m": float(msg.altitude),
        "fix_status": int(getattr(getattr(msg, "status", None), "status", 0) or 0),
    }
    raw_cov = getattr(msg, "position_covariance", None)
    if cov_type > 0 and raw_cov is not None and len(raw_cov) > 0:
        payload["position_covariance"] = list(raw_cov)
        payload["position_covariance_type"] = cov_type
    return payload


def imu_to_imu_sample(msg: Any, operator: str, sensor_id: str,
                       frame_mapper: FrameMapper) -> Dict[str, Any]:
    """``sensor_msgs/Imu`` → SpatialDDS ImuSample payload.

    Per ROS REP-145, ``orientation_covariance[0] == -1`` means orientation is
    not provided. We surface that as ``has_orientation: false`` so consumers
    don't accidentally trust an identity quaternion as a real estimate.
    """
    h = msg.header
    # ROS 2 ships covariance arrays as numpy arrays; ``arr or []`` raises
    # "truth value of an array is ambiguous". Use an explicit None check.
    raw_ori_cov = getattr(msg, "orientation_covariance", None)
    ori_cov = list(raw_ori_cov) if raw_ori_cov is not None else []
    has_orientation = not (ori_cov and ori_cov[0] == -1.0)
    return {
        "schema_version": SCHEMA_CORE,
        "source_operator": operator,
        "sensor_id": sensor_id,
        "frame_ref": frame_mapper.frame_id_to_frame_ref(h.frame_id),
        "stamp": _ros_time_to_sdds(h.stamp),
        "linear_acceleration": _vec3(msg.linear_acceleration),
        "angular_velocity": _vec3(msg.angular_velocity),
        "has_orientation": has_orientation,
        "orientation": _quat(msg.orientation) if has_orientation else None,
    }


def compressed_image_to_vision_frame(msg: Any, operator: str, sensor_id: str,
                                      frame_mapper: FrameMapper,
                                      frame_seq: int = 0) -> Dict[str, Any]:
    """``sensor_msgs/CompressedImage`` → SpatialDDS VisionFrame payload.

    The compressed bytes ride on the wire as a hex-encoded string so the
    payload remains valid JSON. (For larger payloads a follow-on PR will
    move this to MCAP attachments / external blob storage; for the v0
    scope we keep it self-contained and inline.)
    """
    h = msg.header
    fmt = (getattr(msg, "format", "") or "").lower()
    if "png" in fmt:
        codec = "PNG"
    elif "jpeg" in fmt or "jpg" in fmt:
        codec = "JPEG"
    else:
        codec = "RAW"
    raw: bytes = bytes(getattr(msg, "data", b"") or b"")
    stamp = _ros_time_to_sdds(h.stamp)
    return {
        "schema_version": SCHEMA_VISION,
        "source_operator": operator,
        "sensor_id": sensor_id,
        "stream_id": sensor_id,
        "frame_ref": frame_mapper.frame_id_to_frame_ref(h.frame_id),
        "hdr": {
            "stream_id": sensor_id,
            "frame_seq": int(frame_seq),
            "t_start": stamp,
            "t_end": stamp,
            "blobs": [
                {
                    "blob_id": f"{sensor_id}_{stamp['sec']}_{stamp['nanosec']}",
                    "role": "image",
                    "checksum": "",
                }
            ],
        },
        "stamp": stamp,
        "codec": codec,
        "data_hex": raw.hex(),
        "size_bytes": len(raw),
    }


def detection3d_array_to_set(msg: Any, operator: str,
                              frame_mapper: FrameMapper) -> Dict[str, Any]:
    """``vision_msgs/Detection3DArray`` → SpatialDDS Detection3DSet payload.

    Per detection: take the highest-scoring hypothesis (ROS 2's ``results``
    is a list of ``ObjectHypothesisWithPose``); empty results → class
    ``"unknown"`` with score 0.0.
    """
    h = msg.header
    detections: List[Dict[str, Any]] = []
    for i, det in enumerate(msg.detections):
        # ``results`` is a Python list on real rclpy classes, but use an
        # explicit None check for parity with the covariance fix above.
        raw_results = getattr(det, "results", None)
        hypotheses = list(raw_results) if raw_results is not None else []
        if hypotheses:
            top = max(hypotheses,
                      key=lambda r: float(getattr(getattr(r, "hypothesis", None),
                                                   "score", 0.0) or 0.0))
            class_id = getattr(getattr(top, "hypothesis", None), "class_id", "") or "unknown"
            score = float(getattr(getattr(top, "hypothesis", None), "score", 0.0) or 0.0)
        else:
            class_id, score = "unknown", 0.0

        bbox = det.bbox
        center = bbox.center
        size = bbox.size
        det_id = getattr(det, "id", "") or f"det_{i}"
        detections.append({
            "det_id": det_id,
            "center": _vec3(center.position),
            "size": _vec3(size),
            "q": _quat(center.orientation),
            "class_id": class_id,
            "score": score,
        })
    return {
        "schema_version": SCHEMA_SEMANTICS,
        "source_operator": operator,
        "frame_ref": frame_mapper.frame_id_to_frame_ref(h.frame_id),
        "stamp": _ros_time_to_sdds(h.stamp),
        "frame_seq": int(getattr(h.stamp, "sec", 0) or 0),
        "detections": detections,
    }


# ---------- Convenience: convert + topic + msg_type --------------------------
# Each ``encode_*`` returns ``(logical_topic, msg_type, payload_dict)`` ready
# for ``envelope_io.publish_envelope(...)``.

def encode_pose_stamped(msg: Any, operator: str,
                         frame_mapper: FrameMapper) -> Tuple[str, str, Dict[str, Any]]:
    return topic_for_pose(operator), MSG_TYPE_FRAMED_POSE, \
        pose_stamped_to_framed_pose(msg, operator, frame_mapper)


def encode_nav_sat_fix(msg: Any, operator: str, sensor_id: str = "gnss"
                       ) -> Tuple[str, str, Dict[str, Any]]:
    return topic_for_geo_pose(operator, sensor_id), MSG_TYPE_GEO_POSE, \
        nav_sat_fix_to_geo_pose(msg, operator, sensor_id=sensor_id)


def encode_imu(msg: Any, operator: str, sensor_id: str,
                frame_mapper: FrameMapper) -> Tuple[str, str, Dict[str, Any]]:
    return topic_for_imu(operator, sensor_id), MSG_TYPE_IMU_SAMPLE, \
        imu_to_imu_sample(msg, operator, sensor_id, frame_mapper)


def encode_compressed_image(msg: Any, operator: str, sensor_id: str,
                             frame_mapper: FrameMapper,
                             frame_seq: int = 0) -> Tuple[str, str, Dict[str, Any]]:
    return topic_for_vision_frame(operator, sensor_id), MSG_TYPE_VISION_FRAME, \
        compressed_image_to_vision_frame(msg, operator, sensor_id, frame_mapper,
                                          frame_seq=frame_seq)


def encode_detection3d_array(msg: Any, operator: str,
                              frame_mapper: FrameMapper
                              ) -> Tuple[str, str, Dict[str, Any]]:
    return topic_for_detection3d(operator), MSG_TYPE_DETECTION3D_SET, \
        detection3d_array_to_set(msg, operator, frame_mapper)
