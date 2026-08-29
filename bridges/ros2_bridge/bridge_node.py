#!/usr/bin/env python3
"""ROS 2 bridge node — the only file in this package that imports rclpy.

Wires real ROS 2 subscribers and publishers to typed SpatialDDS topics.
Designed to be the **only** ROS 2-coupled file: the
conversion layer (``ros2_to_spatialdds``, ``spatialdds_to_ros2``,
``frame_mapping``) stays duck-typed so 80% of the bridge keeps working
unit-tests-only.

Usage (inside a ROS 2 workspace with cyclonedds also installed):

    ros2 run spatialdds_ros2_bridge bridge_node \\
        --ros-args -p config:=/path/to/bridge_config.yaml

A minimal config:

    operator: operator_a
    domain_id: 1                # SpatialDDS domain (separate from ROS_DOMAIN_ID)
    ros2_to_spatialdds:
      - ros2_topic: /robot/pose
        ros2_type: geometry_msgs/msg/PoseStamped
        spatialdds_type: FramedPose
      - ros2_topic: /gps/fix
        ros2_type: sensor_msgs/msg/NavSatFix
        spatialdds_type: GeoPose
        sensor_id: gnss_0
      - ros2_topic: /imu/data
        ros2_type: sensor_msgs/msg/Imu
        spatialdds_type: ImuSample
        sensor_id: imu_0
      - ros2_topic: /camera/image/compressed
        ros2_type: sensor_msgs/msg/CompressedImage
        spatialdds_type: VisionFrame
        sensor_id: cam_front
      - ros2_topic: /detections_3d
        ros2_type: vision_msgs/msg/Detection3DArray
        spatialdds_type: Detection3DSet
    spatialdds_to_ros2:
      - spatialdds_pattern: spatialdds/*/sensing/detection3d/v1
        ros2_topic_template: /{source_operator}/detections_3d
      - spatialdds_pattern: spatialdds/platform/fusion/track/v1
        ros2_topic: /fused/detections_3d
      - spatialdds_pattern: spatialdds/*/ego/pose/v1
        ros2_topic_template: /{source_operator}/pose
      - spatialdds_pattern: spatialdds/*/geo/*/pose/v1
        ros2_topic_template: /{source_operator}/gps/fix

The two domains are intentionally separate. ``rclpy`` participates in the
ROS 2 DDS domain (``ROS_DOMAIN_ID``); this node creates a **separate**
CycloneDDS participant on ``domain_id`` for SpatialDDS traffic.

This file is Tier-3 — exercised manually inside a ROS 2 environment.
Tier-1/Tier-2 cover the conversion logic without ROS 2 installed.
"""

from __future__ import annotations

import fnmatch
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Sibling modules
_HERE = Path(__file__).resolve().parent
_BRIDGES = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_BRIDGES) not in sys.path:
    sys.path.insert(0, str(_BRIDGES))

from spatialdds_demo import blob, topic_types, typed_transport as tt  # noqa: E402
from spatialdds_demo.stream import StreamSubscriber  # noqa: E402
from frame_mapping import FrameMapper  # noqa: E402
from ros2_to_spatialdds import (  # noqa: E402
    compressed_image_blob,
    encode_compressed_image,
    encode_detection3d_array,
    encode_imu,
    encode_nav_sat_fix,
    encode_pose_stamped,
)
from spatialdds_to_ros2 import (  # noqa: E402
    detection3d_set_to_array,
    vision_frame_blob_id,
    framed_pose_to_pose_stamped,
    geo_pose_to_nav_sat_fix,
    imu_sample_to_imu,
    msg_type_to_decoder,
    vision_frame_to_compressed_image,
)


# ---- ROS 2 imports are deferred so this module is at least *importable*
# without ROS 2 installed. Calling main() without rclpy raises a clear error.

def _import_ros2():
    try:
        import rclpy  # noqa: F401
        from rclpy.node import Node  # noqa: F401
        from sensor_msgs.msg import CompressedImage, Imu, NavSatFix  # noqa: F401
        from geometry_msgs.msg import PoseStamped  # noqa: F401
        from vision_msgs.msg import Detection3DArray  # noqa: F401
    except Exception as exc:  # pragma: no cover - Tier-3 only
        raise SystemExit(
            "bridge_node.py requires ROS 2 (rclpy + sensor_msgs + geometry_msgs + "
            "vision_msgs). Install a ROS 2 environment, or run only the Tier-1/"
            "Tier-2 tests under bridges/ros2_bridge/."
        ) from exc


# ---- Mapping table: ROS 2 type-string → (msg_class_name, encoder) ----------
# The encoder takes (msg, operator, frame_mapper, mapping_dict) and returns
# (logical_topic, msg_type, payload).

def _enc_pose(msg, operator, fm, _mapping):
    return encode_pose_stamped(msg, operator, fm)


def _enc_navsat(msg, operator, fm, mapping):
    sensor_id = mapping.get("sensor_id") or "gnss"
    return encode_nav_sat_fix(msg, operator, sensor_id=sensor_id)


def _enc_imu(msg, operator, fm, mapping):
    sensor_id = mapping.get("sensor_id") or "imu_0"
    return encode_imu(msg, operator, sensor_id, fm)


def _enc_compressed_image(msg, operator, fm, mapping):
    sensor_id = mapping.get("sensor_id") or "cam"
    seq = int(getattr(msg.header.stamp, "sec", 0) or 0)
    return encode_compressed_image(msg, operator, sensor_id, fm, frame_seq=seq)


def _enc_detection3d(msg, operator, fm, _mapping):
    return encode_detection3d_array(msg, operator, fm)


_ROS2_TYPE_TABLE: Dict[str, Dict[str, Any]] = {
    # key: ros2 type string in config; value: how to import + encode
    "geometry_msgs/msg/PoseStamped": {
        "module": "geometry_msgs.msg", "name": "PoseStamped", "encoder": _enc_pose,
    },
    "sensor_msgs/msg/NavSatFix": {
        "module": "sensor_msgs.msg", "name": "NavSatFix", "encoder": _enc_navsat,
    },
    "sensor_msgs/msg/Imu": {
        "module": "sensor_msgs.msg", "name": "Imu", "encoder": _enc_imu,
    },
    "sensor_msgs/msg/CompressedImage": {
        "module": "sensor_msgs.msg", "name": "CompressedImage",
        "encoder": _enc_compressed_image,
    },
    "vision_msgs/msg/Detection3DArray": {
        "module": "vision_msgs.msg", "name": "Detection3DArray",
        "encoder": _enc_detection3d,
    },
}


def _resolve_ros2_class(type_str: str):
    entry = _ROS2_TYPE_TABLE.get(type_str)
    if entry is None:
        raise ValueError(f"Unsupported ROS 2 type: {type_str}")
    import importlib
    module = importlib.import_module(entry["module"])
    return getattr(module, entry["name"]), entry["encoder"]


# ---- Reverse direction: decoder + outbound ROS 2 message factory -----------

def _make_pose_stamped(payload, fm):
    from geometry_msgs.msg import PoseStamped  # type: ignore
    src = framed_pose_to_pose_stamped(payload, fm)
    out = PoseStamped()
    out.header.stamp.sec = src.header.stamp.sec
    out.header.stamp.nanosec = src.header.stamp.nanosec
    out.header.frame_id = src.header.frame_id
    out.pose.position.x = src.pose.position.x
    out.pose.position.y = src.pose.position.y
    out.pose.position.z = src.pose.position.z
    out.pose.orientation.x = src.pose.orientation.x
    out.pose.orientation.y = src.pose.orientation.y
    out.pose.orientation.z = src.pose.orientation.z
    out.pose.orientation.w = src.pose.orientation.w
    return out


def _make_nav_sat_fix(payload, fm):
    from sensor_msgs.msg import NavSatFix, NavSatStatus  # type: ignore
    src = geo_pose_to_nav_sat_fix(payload, fm)
    out = NavSatFix()
    out.header.stamp.sec = src.header.stamp.sec
    out.header.stamp.nanosec = src.header.stamp.nanosec
    out.header.frame_id = src.header.frame_id
    out.latitude = src.latitude
    out.longitude = src.longitude
    out.altitude = src.altitude
    status = NavSatStatus()
    status.status = src.status.status
    status.service = src.status.service
    out.status = status
    out.position_covariance = list(src.position_covariance)
    out.position_covariance_type = src.position_covariance_type
    return out


def _make_detection3d_array(payload, fm):
    from vision_msgs.msg import (  # type: ignore
        BoundingBox3D as RosBBox,
        Detection3D as RosDet3D,
        Detection3DArray as RosDet3DArray,
        ObjectHypothesis as RosHyp,
        ObjectHypothesisWithPose as RosHypWithPose,
    )
    from geometry_msgs.msg import Pose as RosPose  # type: ignore
    src = detection3d_set_to_array(payload, fm)
    out = RosDet3DArray()
    out.header.stamp.sec = src.header.stamp.sec
    out.header.stamp.nanosec = src.header.stamp.nanosec
    out.header.frame_id = src.header.frame_id
    for d in src.detections:
        det = RosDet3D()
        det.header = out.header
        det.id = d.id
        for r in d.results:
            hwp = RosHypWithPose()
            hyp = RosHyp()
            hyp.class_id = r.hypothesis.class_id
            hyp.score = r.hypothesis.score
            hwp.hypothesis = hyp
            det.results.append(hwp)
        bbox = RosBBox()
        center = RosPose()
        center.position.x = d.bbox.center.position.x
        center.position.y = d.bbox.center.position.y
        center.position.z = d.bbox.center.position.z
        center.orientation.x = d.bbox.center.orientation.x
        center.orientation.y = d.bbox.center.orientation.y
        center.orientation.z = d.bbox.center.orientation.z
        center.orientation.w = d.bbox.center.orientation.w
        bbox.center = center
        bbox.size.x = d.bbox.size.x
        bbox.size.y = d.bbox.size.y
        bbox.size.z = d.bbox.size.z
        det.bbox = bbox
        out.detections.append(det)
    return out


# Each decoder produces an instance of the matching ROS 2 message class.
def _make_imu(payload, fm):
    from sensor_msgs.msg import Imu  # type: ignore

    src = imu_sample_to_imu(payload, fm)
    out = Imu()
    out.header.stamp.sec = src.header.stamp.sec
    out.header.stamp.nanosec = src.header.stamp.nanosec
    out.header.frame_id = src.header.frame_id
    out.linear_acceleration.x = src.linear_acceleration.x
    out.linear_acceleration.y = src.linear_acceleration.y
    out.linear_acceleration.z = src.linear_acceleration.z
    out.angular_velocity.x = src.angular_velocity.x
    out.angular_velocity.y = src.angular_velocity.y
    out.angular_velocity.z = src.angular_velocity.z
    # vio::ImuSample carries no orientation, so REP-145's "not provided"
    # sentinel is the truth here rather than a fallback.
    out.orientation_covariance = list(src.orientation_covariance)
    return out


def _make_compressed_image(payload, fm, data: bytes = b""):
    from sensor_msgs.msg import CompressedImage  # type: ignore

    src = vision_frame_to_compressed_image(payload, fm, data=data)
    out = CompressedImage()
    out.header.stamp.sec = src.header.stamp.sec
    out.header.stamp.nanosec = src.header.stamp.nanosec
    out.header.frame_id = src.header.frame_id
    out.format = src.format
    out.data = list(src.data)
    return out


_OUTBOUND_FACTORIES: Dict[str, Callable[..., Any]] = {
    "PoseStamped": _make_pose_stamped,
    "NavSatFix": _make_nav_sat_fix,
    "Detection3DArray": _make_detection3d_array,
    "Imu": _make_imu,
    "CompressedImage": _make_compressed_image,
}


# ---- CLI-flag config builder -----------------------------------------------
# Convenience: build a config dict from CLI flags so quick tests don't need a
# YAML file. Topic→type mapping is auto-inferred from the topic name pattern
# (pose / fix / imu / image / det) — sufficient for the v0 type set; YAML
# remains the precise interface for production deployments.

_TOPIC_PATTERN_TO_TYPE = (
    ("pose",       "geometry_msgs/msg/PoseStamped"),
    ("fix",        "sensor_msgs/msg/NavSatFix"),
    ("imu",        "sensor_msgs/msg/Imu"),
    ("image",      "sensor_msgs/msg/CompressedImage"),
    ("det",        "vision_msgs/msg/Detection3DArray"),
    ("detection",  "vision_msgs/msg/Detection3DArray"),
)


def _infer_type_for_topic(topic: str) -> str:
    lowered = topic.lower()
    for pat, type_str in _TOPIC_PATTERN_TO_TYPE:
        if pat in lowered:
            return type_str
    raise ValueError(
        f"Cannot infer ROS 2 type for {topic!r}. Use a YAML config "
        f"(`-p config:=...`) to specify the type explicitly."
    )


def _config_from_cli_args(operator: str, dds_domain: int, topics: list) -> dict:
    return {
        "operator": operator,
        "domain_id": int(dds_domain),
        "ros2_to_spatialdds": [
            {"ros2_topic": t, "ros2_type": _infer_type_for_topic(t)}
            for t in topics
        ],
        # No reverse-direction mappings via CLI — use YAML if you need them.
        "spatialdds_to_ros2": [],
    }


# ---- The node ---------------------------------------------------------------

def _image_chunks(encoder, msg, mapping):
    """
    The blob chunks that accompany a frame, if this mapping produces any.

    A `VisionFrame` is metadata plus a `BlobRef`; the image bytes travel
    separately on the shared blob topic, and `BlobRef.blob_id` ties the two
    together. Only the compressed-image encoder has bytes to send, so every
    other mapping yields nothing and the caller's loop simply does not run.

    Keyed off the encoder rather than the message type so a mapping that
    reuses the encoder gets the same treatment automatically.
    """
    if encoder is not _enc_compressed_image:
        return
    sensor_id = mapping.get("sensor_id") or "cam"
    seq = int(getattr(msg.header.stamp, "sec", 0) or 0)
    yield from compressed_image_blob(msg, sensor_id, frame_seq=seq)


class _TypedWriters:
    """
    Lazily-built typed writers, one per SpatialDDS topic.

    The ROS 2 side names a topic and a §3.3.2 type for each mapping; this
    resolves the type to its generated class and opens a writer on the lane
    §3.3.3 assigns it. Writers are built on first use because which topics a
    run touches depends on its config.

    An unresolvable type raises rather than being skipped: this bridge is the
    producer, and a producer that cannot build the type it is about to
    advertise has nothing to write.
    """

    def __init__(self, participant):
        self._participant = participant
        self._writers: Dict[str, Any] = {}

    def write(self, topic: str, type_name: str, payload: Any) -> None:
        writer = self._writers.get(topic)
        if writer is None:
            datatype = topic_types.resolve(type_name)
            writer = tt.TypedDictWriter(
                self._participant, topic, datatype,
                topic_types.profile_for(type_name))
            self._writers[topic] = writer
        writer.write(payload)


def main():  # pragma: no cover - Tier-3 only
    import argparse
    import sys as _sys

    # Split off "--ros-args ..." so argparse only sees our own flags.
    own_argv: list = []
    saw_ros_args = False
    for a in _sys.argv[1:]:
        if a == "--ros-args":
            saw_ros_args = True
        if saw_ros_args:
            continue
        own_argv.append(a)

    parser = argparse.ArgumentParser(
        description="SpatialDDS ↔ ROS 2 bridge node",
        add_help=True,
    )
    parser.add_argument("--operator",
                         help="Operator namespace for the SpatialDDS side")
    parser.add_argument("--ros-domain", type=int,
                         help="ROS 2 domain id (sets ROS_DOMAIN_ID for this process)")
    parser.add_argument("--dds-domain", type=int,
                         help="SpatialDDS CycloneDDS domain id")
    parser.add_argument("--topics", nargs="+", default=[],
                         help="ROS 2 topics to bridge (type auto-inferred from name)")
    parser.add_argument("--config", default="",
                         help="Path to YAML config (overrides individual flags)")
    args = parser.parse_args(own_argv)

    if args.ros_domain is not None:
        import os as _os
        _os.environ["ROS_DOMAIN_ID"] = str(args.ros_domain)

    _import_ros2()
    import rclpy
    from rclpy.node import Node
    import yaml

    rclpy.init()
    node = Node("spatialdds_ros2_bridge")

    config_path = args.config or node.declare_parameter("config", "").value
    if config_path:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    elif args.operator and args.topics:
        if args.dds_domain is None:
            node.get_logger().error("--dds-domain is required when not using --config")
            rclpy.shutdown()
            return 1
        try:
            config = _config_from_cli_args(args.operator, args.dds_domain, args.topics)
        except ValueError as exc:
            node.get_logger().error(str(exc))
            rclpy.shutdown()
            return 1
    else:
        node.get_logger().error(
            "Need either --config <yaml> or --operator + --dds-domain + --topics")
        rclpy.shutdown()
        return 1

    operator = str(config["operator"])
    domain_id = int(config.get("domain_id", 1))
    fm = FrameMapper(operator)

    from cyclonedds.domain import DomainParticipant

    # One participant for both directions, so the SpatialDDS-side readers can
    # use IGNORE_LOCAL_PARTICIPANT and never see this bridge's own writes.
    participant = DomainParticipant(domain_id)
    writers = _TypedWriters(participant)

    # ROS 2 → SpatialDDS subscriptions
    for mapping in config.get("ros2_to_spatialdds", []) or []:
        ros2_type_str = mapping["ros2_type"]
        ros2_topic = mapping["ros2_topic"]
        ros2_cls, encoder = _resolve_ros2_class(ros2_type_str)

        def make_callback(_encoder=encoder, _mapping=mapping):
            def cb(msg):
                topic, msg_type, payload = _encoder(msg, operator, fm, _mapping)
                try:
                    writers.write(topic, msg_type, payload)
                except Exception as exc:
                    # An unresolvable type, or a payload that is not a
                    # well-formed sample of it. Reported here rather than
                    # left for some later consumer to discover.
                    node.get_logger().error(
                        f"ros2->sdds {topic} ({msg_type}): {exc}")
                    return
                # Image bytes do not ride in the frame message: a VisionFrame
                # is metadata plus a BlobRef, and the bytes travel as blob
                # chunks on the shared blob topic.
                for chunk in _image_chunks(_encoder, msg, _mapping):
                    writers.write(blob.BLOB_TOPIC, blob.BLOB_TYPE, chunk)
            return cb

        node.create_subscription(ros2_cls, ros2_topic, make_callback(), 10)
        node.get_logger().info(f"ros2→sdds  {ros2_topic} ({ros2_type_str})")

    # SpatialDDS → ROS 2 publishers (lazy: created on first matching envelope)
    sdds_to_ros2_cfg = list(config.get("spatialdds_to_ros2", []) or [])
    ros2_pubs: Dict[str, Any] = {}

    def find_mapping(logical_topic: str, msg_type: str) -> Optional[dict]:
        for entry in sdds_to_ros2_cfg:
            pattern = entry.get("spatialdds_pattern", "")
            if fnmatch.fnmatchcase(logical_topic, pattern):
                return entry
        return None

    reassembler = blob.Reassembler()
    pending_images: Dict[str, tuple] = {}

    def sample_callback(msg_type: str, logical_topic: str, payload: dict,
                        _stamp_ns: int):
        if msg_type == blob.BLOB_TYPE:
            _on_blob_chunk(payload)
            return
        decoder = msg_type_to_decoder(msg_type)
        if decoder is None:
            return
        mapping = find_mapping(logical_topic, msg_type)
        if mapping is None:
            return

        # Resolve outbound ROS 2 type from decoder result
        if decoder is detection3d_set_to_array:
            ros2_name, ros2_module = "Detection3DArray", "vision_msgs.msg"
        elif decoder is framed_pose_to_pose_stamped:
            ros2_name, ros2_module = "PoseStamped", "geometry_msgs.msg"
        elif decoder is geo_pose_to_nav_sat_fix:
            ros2_name, ros2_module = "NavSatFix", "sensor_msgs.msg"
        elif decoder is imu_sample_to_imu:
            ros2_name, ros2_module = "Imu", "sensor_msgs.msg"
        elif decoder is vision_frame_to_compressed_image:
            ros2_name, ros2_module = "CompressedImage", "sensor_msgs.msg"
        else:
            return

        factory = _OUTBOUND_FACTORIES.get(ros2_name)
        if factory is None:
            node.get_logger().warning(
                f"sdds→ros2: no outbound factory for {ros2_name} (TODO)")
            return

        # Resolve the actual ROS 2 topic name (templated by source_operator)
        if "ros2_topic" in mapping:
            out_topic = mapping["ros2_topic"]
        else:
            template = mapping.get("ros2_topic_template", "")
            src = payload.get("source_operator", "unknown")
            out_topic = template.format(source_operator=src)

        # Lazy-create the publisher
        pub_key = (ros2_name, out_topic)
        if pub_key not in ros2_pubs:
            import importlib
            mod = importlib.import_module(ros2_module)
            cls = getattr(mod, ros2_name)
            ros2_pubs[pub_key] = node.create_publisher(cls, out_topic, 10)
            node.get_logger().info(f"sdds→ros2  {logical_topic} → {out_topic} ({ros2_name})")

        try:
            if ros2_name == "CompressedImage":
                # Hold the frame until its blob arrives. Publishing metadata
                # with an empty image would look like a working bridge —
                # which is what the old inline `data_hex` path did once the
                # field stopped surviving the wire.
                blob_id = vision_frame_blob_id(payload)
                if blob_id:
                    pending_images[blob_id] = (pub_key, payload)
                    return
            ros2_pubs[pub_key].publish(factory(payload, fm))
        except Exception as exc:
            node.get_logger().error(f"failed to publish {out_topic}: {exc}")

    def _on_blob_chunk(payload: dict) -> None:
        """Reassemble image bytes and release the frame that was waiting."""
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.spatial.core import BlobChunk

        try:
            data = reassembler.feed(from_json(BlobChunk, payload))
        except blob.CorruptChunk as exc:
            node.get_logger().warning(str(exc))
            return
        if data is None:
            return
        blob_id = str(payload.get("blob_id") or "")
        waiting = pending_images.pop(blob_id, None)
        if waiting is None:
            return
        pub_key, frame = waiting
        try:
            ros2_pubs[pub_key].publish(_make_compressed_image(frame, fm, data))
        except Exception as exc:
            node.get_logger().error(f"failed to publish image {blob_id}: {exc}")

    subscriber = StreamSubscriber(participant, sample_callback,
                                  ignore_local=True)

    def _poll_sdds() -> None:
        subscriber.poll(stamp_ns=time.time_ns())

    # Polled on the rclpy executor rather than a thread, so every ROS 2
    # publish happens on the node's own thread.
    node.create_timer(0.02, _poll_sdds)

    node.get_logger().info(
        f"bridge ready  operator={operator}  spatialdds_domain={domain_id}"
    )
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main() or 0)
