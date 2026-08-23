"""ROS 2 message → SpatialDDS payload-dict converters.

Each ``*_to_spatialdds`` function takes a duck-typed ROS 2 message (real
``rclpy`` class or a mock from ``test_mocks``) plus operator/sensor context,
and returns ``(topic, §3.3.2 type name, payload dict)``.

The payload is a dict because that is convenient to build and test, not
because it is the wire format: ``bridge_node`` builds it into the named type
before writing, so a payload that is not a well-formed sample fails at the
bridge. ``spatialdds_to_ros2.py`` goes the other way.

The builders for spec types come from ``spatialdds_demo.payloads``, shared
with the multi-operator publishers. Each side used to have its own and both
were wrong — a bridge and a publisher disagreeing about a spec type is
exactly the drift a typed wire is meant to make impossible.

NO ROS 2 imports. NO DDS imports. Pure-Python so Tier-1 tests can run
without either dependency.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from spatialdds_demo import payloads
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
# §3.3.2 registered type names — the same strings the announce advertises
# and a consumer resolves into a reader. These used to be `ROS2_*` labels
# this bridge chose for itself, which no registry knew and nothing could
# resolve; a `ROS2_FRAMED_POSE` on a topic told a consumer nothing it could
# act on. Where the registry names no type for a stable 1.7 type, the demo's
# documented `oarc.*` extension name is used.
MSG_TYPE_FRAMED_POSE      = "framed_pose"
MSG_TYPE_GEO_POSE         = "geopose"
MSG_TYPE_IMU_SAMPLE       = "imu_sample"
MSG_TYPE_VISION_FRAME     = "video_frame"
# Not the registered `radar_detection`. This bridge writes onto the same
# `…/sensing/detection3d/v1` topic the fusion demo reads, and a topic is one
# type: publishing a bare Detection3DSet there would be a type collision DDS
# would refuse. ROS 2's Detection3DArray carries no velocity, so the
# presence flag is set false — which is exactly what it is for.
MSG_TYPE_DETECTION3D_SET  = "detection3d"


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
    """
    ``geometry_msgs/PoseStamped`` -> ``spatial::core::FramedPose``.

    FramedPose is exactly `(pose, frame_ref, cov, stamp)`. The old payload
    also carried `schema_version` and `source_operator`, neither of which the
    type has: the operator is already in the topic name, which is where DDS
    expects that kind of identity to live.

    PoseStamped has no covariance, so `cov` is the COV_NONE case rather than
    a zero matrix pretending to be one.
    """
    h = msg.header
    return {
        "pose": {"t": _as_array(_vec3(msg.pose.position)),
                 "q": _as_array(_quat(msg.pose.orientation), ("x", "y", "z", "w"))},
        "frame_ref": frame_mapper.frame_id_to_frame_ref(h.frame_id),
        "cov": {"discriminator": "COV_NONE", "none": 0},
        "stamp": _ros_time_to_sdds(h.stamp),
    }


def nav_sat_fix_to_geo_pose(msg: Any, operator: str,
                            sensor_id: str = "gnss") -> Dict[str, Any]:
    """
    ``sensor_msgs/NavSatFix`` -> ``spatial::core::GeoPose``.

    GeoPose is `(lat_deg, lon_deg, alt_m, q, stamp, cov)` and nothing else.
    The old payload invented `source_operator`, `sensor_id` and `fix_status`;
    none exist on the type, and the fix status has its own registered
    companion — see :func:`nav_sat_fix_to_nav_sat_status`, which the spec's
    own registry describes as the "companion to GeoPose".

    NavSatFix carries no orientation, so `q` is identity. 1.7 fixes GeoPose
    orientation to the local ENU tangent frame at the encoded position,
    which is what NavSatFix already implies.
    """
    h = msg.header
    cov_type = int(getattr(msg, "position_covariance_type", 0) or 0)
    raw_cov = getattr(msg, "position_covariance", None)
    cov = list(raw_cov) if raw_cov is not None else []
    return {
        "lat_deg": float(msg.latitude),
        "lon_deg": float(msg.longitude),
        "alt_m": float(msg.altitude),
        "q": [0.0, 0.0, 0.0, 1.0],
        "stamp": _ros_time_to_sdds(h.stamp),
        "cov": _cov3(cov if cov_type > 0 else []),
    }


# sensor_msgs/NavSatStatus.status -> spatial::core::GnssFixType. ROS 2 has
# four values; the spec's enum is finer, so the mapping is deliberately
# lossy upward: NO_FIX, and then the coarsest fix each ROS value guarantees.
_ROS_FIX_TO_SDDS = {
    -1: "NO_FIX",       # STATUS_NO_FIX
    0: "FIX_3D",        # STATUS_FIX        — unaugmented
    1: "SBAS",          # STATUS_SBAS_FIX
    2: "DGPS",          # STATUS_GBAS_FIX   — ground-based augmentation
}


def nav_sat_fix_to_nav_sat_status(msg: Any, sensor_id: str = "gnss"
                                  ) -> Dict[str, Any]:
    """
    ``sensor_msgs/NavSatFix`` -> ``spatial::core::NavSatStatus``.

    The receiver diagnostics NavSatFix carries but GeoPose has no field for.
    3.3.2 registers `navsat_status` as GeoPose's companion, so the bridge
    publishes both rather than bolting a `fix_status` onto the pose.

    ROS 2 gives status, service and nothing else — no DOP, no satellite
    count, no differential age — so those stay flagged absent.
    """
    status = getattr(msg, "status", None)
    ros_status = int(getattr(status, "status", 0) or 0)
    return {
        "gnss_id": str(sensor_id),
        "fix_type": _ROS_FIX_TO_SDDS.get(ros_status, "NO_FIX"),
        "service": int(getattr(status, "service", 0) or 0),
        # NavSatFix reports no satellite count; 0 is "not reported", which is
        # what the type's own absence of a flag here leaves us.
        "num_satellites": 0,
        "has_dop": False,
        "pdop": 0.0, "hdop": 0.0, "vdop": 0.0,
        "has_velocity": False,
        "speed_mps": 0.0, "course_deg": 0.0,
        "has_diff_age": False,
        "diff_age_s": 0.0, "diff_station_id": "",
        "stamp": _ros_time_to_sdds(msg.header.stamp),
        "schema_version": SCHEMA_CORE,
    }


def _cov3(values: List[float]) -> Dict[str, Any]:
    """
    A 3x3 position covariance as a ``CovMatrix``, or COV_NONE if absent.

    The union's position case is ``COV_POS3`` carrying ``pos`` — not the
    ``COV_FULL_3X3``/``full3x3`` this bridge first guessed at. Getting it
    wrong used to be silent: cyclonedds built a union with no active case
    and the covariance vanished. `from_json` refuses an unknown case now.
    """
    if len(values) >= 9:
        return {"discriminator": "COV_POS3", "pos": [float(v) for v in values[:9]]}
    return {"discriminator": "COV_NONE", "none": 0}


def imu_to_imu_sample(msg: Any, operator: str, sensor_id: str,
                      frame_mapper: FrameMapper) -> Dict[str, Any]:
    """
    ``sensor_msgs/Imu`` -> ``spatial::vio::ImuSample``.

    ImuSample gained accel and gyro covariances in 1.7's findings-batch-2
    revision, so the only thing ROS 2 carries that still has nowhere to go
    is **orientation** and its covariance (REP-145) — and that is arguably
    right: ImuSample is raw accel + gyro, and a fused attitude is a
    FramedPose, published separately if the platform has one.

    `frame_ref` has no home either: ImuSample names a `source_id`, not a
    frame, so the mapper is unused and kept in the signature only because
    the bridge calls every encoder the same way.
    """
    return {
        "imu_id": str(sensor_id),
        "accel": _as_array(_vec3(msg.linear_acceleration)),
        "gyro": _as_array(_vec3(msg.angular_velocity)),
        "stamp": _ros_time_to_sdds(msg.header.stamp),
        "source_id": str(operator),
        "seq": 0,
        "has_accel_cov": True,
        "accel_cov": _cov3(_cov_list(msg, "linear_acceleration_covariance")),
        "has_gyro_cov": True,
        "gyro_cov": _cov3(_cov_list(msg, "angular_velocity_covariance")),
    }


def _cov_list(msg: Any, field: str) -> List[float]:
    """A ROS 2 covariance array as a list. They arrive as numpy arrays."""
    raw = getattr(msg, field, None)
    return list(raw) if raw is not None else []


# sensor_msgs/CompressedImage.format -> spatial::sensing::common::Codec.
# PNG was added to the enum in 1.7's findings-batch-2 revision; before that
# a PNG had to be announced as CODEC_NONE, which is wrong in a way a
# consumer cannot detect. ROS 2 uses PNG routinely for depth and mask
# imagery, where JPEG's lossiness is unacceptable.
def _codec_for(fmt: str) -> str:
    fmt = (fmt or "").lower()
    if "jpeg" in fmt or "jpg" in fmt:
        return "JPEG"
    if "png" in fmt:
        return "PNG"
    return "CODEC_NONE"


def compressed_image_to_vision_frame(msg: Any, operator: str, sensor_id: str,
                                     frame_mapper: FrameMapper,
                                     frame_seq: int = 0) -> Dict[str, Any]:
    """
    ``sensor_msgs/CompressedImage`` -> ``spatial::sensing::vision::VisionFrame``.

    The image bytes are **not** in here, and the spec is explicit that they
    should not be: a frame message is metadata plus a `BlobRef`, and the
    bytes travel as blob chunks. The old payload inlined them as a hex string
    under a `data_hex` key VisionFrame does not have — which is the exact
    pattern this migration exists to remove, and it silently vanished on the
    wire.

    Use :func:`compressed_image_blob` for the bytes; the `BlobRef` in
    `hdr.blobs` is what ties the two together.
    """
    h = msg.header
    raw: bytes = bytes(getattr(msg, "data", b"") or b"")
    stamp = _ros_time_to_sdds(h.stamp)
    return {
        "stream_id": str(sensor_id),
        "frame_seq": int(frame_seq),
        "hdr": {
            "stream_id": str(sensor_id),
            "frame_seq": int(frame_seq),
            "t_start": stamp,
            "t_end": stamp,
            # CompressedImage carries no sensor pose; the flag says so.
            "has_sensor_pose": False,
            "sensor_pose": {"t": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]},
            "blobs": [_blob_ref_for(sensor_id, stamp, raw)],
        },
        "codec": _codec_for(getattr(msg, "format", "")),
        # CompressedImage does not say what the encoded pixels were; the
        # codec's own container does.
        "pix": "UNKNOWN",
        "color": "SRGB",
        "has_line_readout_us": False,
        "line_readout_us": 0.0,
        "rectified": False,
        "is_key_frame": True,
        "quality": {"has_snr_db": False, "snr_db": 0.0,
                    "percent_valid": 100.0, "health": "OK", "note": ""},
    }


def compressed_image_blob(msg: Any, sensor_id: str, frame_seq: int = 0):
    """
    The image bytes as ``oarc_demo::BlobChunk`` samples.

    Published on the shared blob topic alongside the VisionFrame that
    references them. Yields nothing for an empty image.
    """
    from spatialdds_demo import blob

    raw: bytes = bytes(getattr(msg, "data", b"") or b"")
    if not raw:
        return
    stamp = _ros_time_to_sdds(msg.header.stamp)
    yield from blob.chunk(_blob_id(sensor_id, stamp), raw)


def _blob_id(sensor_id: str, stamp: Dict[str, int]) -> str:
    return f"{sensor_id}_{stamp['sec']}_{stamp['nanosec']}"


def _blob_ref_for(sensor_id: str, stamp: Dict[str, int],
                  raw: bytes) -> Dict[str, str]:
    from spatialdds_demo import blob

    return blob.blob_ref(_blob_id(sensor_id, stamp), "image", raw)


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
        frame_ref = frame_mapper.frame_id_to_frame_ref(h.frame_id)
        # The one canonical Detection3D builder, shared with the
        # multi-operator publishers. Each side used to have its own and both
        # were wrong; a bridge and a publisher disagreeing about a spec type
        # is exactly the drift the typed wire is meant to make impossible.
        detection = payloads.detection3d(
            det_id=det_id, class_id=class_id, score=score,
            center=_as_array(_vec3(center.position)),
            size=_as_array(_vec3(size)),
            q=_as_array(_quat(center.orientation), ("x", "y", "z", "w")),
            frame_ref_fqn="", frame_ref_dict=frame_ref,
            timestamp_s=_stamp_seconds(h.stamp), source_id=operator)
        # vision_msgs/Detection3DArray has no velocity, so `velocity=None`
        # above leaves Detection3D's has_velocity false — the flag says so
        # rather than a zero vector pretending to be a measurement.
        detections.append(detection)
    return payloads.detection_set(
        set_id=f"{operator}-{int(getattr(h.stamp, 'sec', 0) or 0)}",
        source_operator=operator, frame_ref_fqn="",
        frame_ref_dict=frame_mapper.frame_id_to_frame_ref(h.frame_id),
        dets=detections, frame_seq=int(getattr(h.stamp, "sec", 0) or 0),
        timestamp_s=_stamp_seconds(h.stamp))


def _stamp_seconds(ros_stamp: Any) -> float:
    return (float(getattr(ros_stamp, "sec", 0) or 0)
            + float(getattr(ros_stamp, "nanosec", 0) or 0) / 1e9)


def _as_array(value: Any, keys=("x", "y", "z")) -> List[float]:
    """Vec3/Quat are IDL arrays; these converters build {x,y,z} dicts."""
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value]
    return [float((value or {}).get(k, 1.0 if k == "w" else 0.0)) for k in keys]


# ---------- Convenience: convert + topic + type ------------------------------
# Each ``encode_*`` returns ``(topic, type_name, payload)`` ready for a typed
# writer. ``type_name`` is the §3.3.2 registry name, which is also what the
# announce advertises and what a consumer resolves into a reader.

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
