#!/usr/bin/env python3
"""Multi-operator nuScenes publisher.

Wraps the nuScenes -> SpatialDDS mapping with per-operator namespacing,
spatial offset, and sensor-modality filtering. Each operator process
publishes under topic prefix ``spatialdds/{operator}/...`` and stamps
``source_operator`` into every JSON payload for fusion-side provenance.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
NUSCENES_DIR = REPO_ROOT / "nuscenes"
for p in (REPO_ROOT, NUSCENES_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

CAM_CHANNELS = [
    "CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT",
    "CAM_BACK", "CAM_BACK_LEFT", "CAM_BACK_RIGHT",
]
RADAR_CHANNELS = [
    "RADAR_FRONT", "RADAR_FRONT_LEFT", "RADAR_FRONT_RIGHT",
    "RADAR_BACK_LEFT", "RADAR_BACK_RIGHT",
]
LIDAR_CHANNEL = "LIDAR_TOP"

SENSOR_FILTERS: Dict[str, set] = {
    "full":        {"camera", "lidar", "radar"},
    "camera":      {"camera"},
    "lidar_radar": {"lidar", "radar"},
}


def _topic(operator: str, *parts: str) -> str:
    return "/".join(("spatialdds", operator, *parts))


def _offset_vec(payload: Dict, path: Iterable[str],
                offset: Tuple[float, float, float]) -> None:
    """
    Shift a Vec3 in place. Vec3 is ``double[3]``, not ``{x, y, z}``.

    Missing paths are skipped — several are conditional on which sensors the
    run enabled.
    """
    path = list(path)
    node = payload
    for key in path[:-1]:
        if not isinstance(node, dict) or key not in node:
            return
        node = node[key]
    last = path[-1]
    if not isinstance(node, dict):
        return
    vec = node.get(last)
    if isinstance(vec, dict):
        # The converters emit arrays now; tolerate the older object form so a
        # stray one is offset rather than silently left in place.
        vec = [float(vec.get(k, 0.0)) for k in "xyz"]
    if isinstance(vec, (list, tuple)) and len(vec) >= 3:
        node[last] = [float(v) + o for v, o in zip(vec, offset)]


# (topic suffix, §3.3.2 type, §3.3.3 QoS profile) per lane. The operator
# lives in the topic name, which is where DDS expects that kind of identity —
# the old payloads stamped a `source_operator` field onto every message,
# including spec types that have no such field.
LANES = {
    "ego_pose":    (("ego", "pose", "v1"), "framed_pose", "POSE_RT"),
    "geo_pose":    (("geo", "ego", "pose", "v1"), "geopose", "POSE_RT"),
    "vision_meta": None,   # per-camera, built below
    "vision_frame": None,
    "lidar_meta":  (("lidar", LIDAR_CHANNEL, "meta", "v1"),
                    "lidar_meta", "MAP_META"),
    "lidar_frame": (("lidar", LIDAR_CHANNEL, "frame", "v1"),
                    "lidar_frame", "GEOM_TILE"),
    "detection3d": (("sensing", "detection3d", "v1"),
                    "detection3d", "RADAR_RT"),
}
VISION_META_TYPE = ("video_meta", "MAP_META")
VISION_FRAME_TYPE = ("video_frame", "VIDEO_LIVE")
RADAR_TYPE = ("radar_detection", "RADAR_RT")


def _detections_with_velocity(nusc, sample, frame_seq: int, operator: str,
                              offset: Tuple[float, float, float]) -> Dict:
    """
    nuScenes annotations as an ``oarc_demo::Detection3DSet``.

    The velocity comes from ``nusc.box_velocity``; NaN means "not
    determinable from adjacent frames", which is the presence flag's job
    rather than a zero vector's.
    """
    import math

    from spatialdds_demo import payloads
    from sensor_types import to_dict
    from nuscenes_to_spatialdds import annotation_to_detection3d

    dets = []
    for token in sample["anns"]:
        detection = to_dict(annotation_to_detection3d(nusc, token))
        _offset_vec(detection, ("center",), offset)
        raw = nusc.box_velocity(token)
        # NaN means "not determinable from adjacent frames", which is the
        # presence flag's job rather than a zero vector's.
        if raw is not None and not any(math.isnan(float(v)) for v in raw):
            detection["has_velocity"] = True
            detection["velocity"] = [float(v) for v in raw]
        dets.append(detection)
    return payloads.detection_set(
        set_id=f"{operator}-{frame_seq}", source_operator=operator,
        frame_ref_fqn=f"{operator}/map", dets=dets, frame_seq=frame_seq,
        timestamp_s=int(sample["timestamp"]) / 1e6,
    )


def _iter_scene_samples(nusc: NuScenes, scene_name: str):
    scene = next((s for s in nusc.scene if s["name"] == scene_name), None)
    if scene is None:
        raise ValueError(f"Scene not found: {scene_name}")
    token = scene["first_sample_token"]
    while token:
        sample = nusc.get("sample", token)
        yield sample
        token = sample["next"]


def run(args: argparse.Namespace) -> int:
    from nuscenes.nuscenes import NuScenes
    from nuscenes.utils.data_classes import RadarPointCloud
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo import payloads, topic_types, typed_transport as tt
    from sensor_types import to_dict
    from nuscenes_to_spatialdds import (
        camera_to_vision_meta,
        ego_pose_to_spatialdds,
        lidar_to_meta_and_frame,
        radar_to_detection_set,
        sample_annotations_to_set,
        sample_data_to_vision_frame,
    )

    dataroot = Path(args.dataroot)
    if not dataroot.exists():
        raise FileNotFoundError(f"dataroot does not exist: {dataroot}")

    operator = args.operator
    offset = (args.offset_x, args.offset_y, args.offset_z)
    enabled = SENSOR_FILTERS[args.sensor_filter]

    nusc = NuScenes(version=args.version, dataroot=str(dataroot), verbose=not args.quiet)
    participant = DomainParticipant(args.domain)
    writers: Dict[str, tt.TypedDictWriter] = {}

    def publish(topic_parts, lane, payload) -> None:
        """Write one sample, building it into the lane's announced type."""
        type_name, profile = lane
        topic = _topic(operator, *topic_parts)
        writer = writers.get(topic)
        if writer is None:
            writer = tt.TypedDictWriter(
                participant, topic, topic_types.resolve(type_name), profile)
            writers[topic] = writer
        writer.write(payload)

    sent_meta: set = set()
    frame_seq = 0
    delay = 1.0 / max(0.1, args.rate_hz)

    try:
        for sample in _iter_scene_samples(nusc, args.scene):
            frame_seq += 1

            lidar_sd = nusc.get("sample_data", sample["data"][LIDAR_CHANNEL])
            ego_pose = nusc.get("ego_pose", lidar_sd["ego_pose_token"])
            pose, geopose = ego_pose_to_spatialdds(ego_pose)
            stamp = {"sec": ego_pose["timestamp"] // 1_000_000,
                     "nanosec": (ego_pose["timestamp"] % 1_000_000) * 1000}
            # A local pose and a geographic one are two types, so two lanes.
            # The old payload bundled them with a frame_seq — a shape no spec
            # type has.
            ego_payload = {
                "pose": to_dict(pose),
                "frame_ref": payloads.frame_ref(f"{operator}/map"),
                "cov": dict(payloads.COV_NONE),
                "stamp": stamp,
            }
            _offset_vec(ego_payload, ("pose", "t"), offset)
            publish(LANES["ego_pose"][0], LANES["ego_pose"][1:], ego_payload)
            publish(LANES["geo_pose"][0], LANES["geo_pose"][1:],
                    {**to_dict(geopose), "stamp": stamp})

            if "camera" in enabled:
                for cam in CAM_CHANNELS:
                    if cam not in sample["data"]:
                        continue
                    sd = nusc.get("sample_data", sample["data"][cam])
                    cs = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
                    if cam not in sent_meta:
                        # Metadata is its own latched lane, so a late joiner
                        # gets the calibration without waiting for a
                        # republish. It used to share the frame topic, which
                        # meant two types on one topic.
                        publish(("vision", cam, "meta", "v1"),
                                VISION_META_TYPE,
                                to_dict(camera_to_vision_meta(sd, cs)))
                        sent_meta.add(cam)
                    publish(("vision", cam, "frame", "v1"), VISION_FRAME_TYPE,
                            to_dict(sample_data_to_vision_frame(sd, frame_seq)))

            if "lidar" in enabled and LIDAR_CHANNEL in sample["data"]:
                sd = nusc.get("sample_data", sample["data"][LIDAR_CHANNEL])
                cs = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
                lidar_meta, lidar_frame = lidar_to_meta_and_frame(sd, cs, frame_seq)
                if LIDAR_CHANNEL not in sent_meta:
                    publish(LANES["lidar_meta"][0], LANES["lidar_meta"][1:],
                            to_dict(lidar_meta))
                    sent_meta.add(LIDAR_CHANNEL)
                publish(LANES["lidar_frame"][0], LANES["lidar_frame"][1:],
                        to_dict(lidar_frame))

            if "radar" in enabled:
                for radar in RADAR_CHANNELS:
                    if radar not in sample["data"]:
                        continue
                    sd = nusc.get("sample_data", sample["data"][radar])
                    radar_path = dataroot / sd["filename"]
                    if not radar_path.exists():
                        continue
                    radar_pc = RadarPointCloud.from_file(str(radar_path))
                    det_set = to_dict(
                        radar_to_detection_set(sd, radar_pc.points, frame_seq))
                    for det in det_set.get("dets", []):
                        _offset_vec(det, ("xyz_m",), offset)
                    publish(("rad", radar, "frame", "v1"), RADAR_TYPE, det_set)

            # This lane feeds the fusion service, which gates association on
            # velocity — so it carries oarc.detection3d_velocity, not a bare
            # Detection3DSet. nuScenes has the velocity (box_velocity), and
            # semantics::Detection3D has no field for it; composing rather
            # than replacing means a conformant consumer lifts the spec type
            # straight out.
            det_set = _detections_with_velocity(nusc, sample, frame_seq,
                                                operator, offset)
            publish(LANES["detection3d"][0], LANES["detection3d"][1:], det_set)

            if not args.quiet:
                print(f"[{operator}] frame_seq={frame_seq} sample={sample['token']}", file=sys.stderr)

            time.sleep(delay)
            if args.max_samples > 0 and frame_seq >= args.max_samples:
                break
    finally:
        pass
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-operator nuScenes -> SpatialDDS publisher")
    p.add_argument("--operator", required=True,
                   help="Operator namespace (e.g. operator_a, operator_b, operator_c)")
    p.add_argument("--sensor-filter", choices=sorted(SENSOR_FILTERS), default="full",
                   help="Which sensor modalities to publish (always publishes det3d + ego)")
    p.add_argument("--offset-x", type=float, default=0.0)
    p.add_argument("--offset-y", type=float, default=0.0)
    p.add_argument("--offset-z", type=float, default=0.0)
    p.add_argument("--dataroot", required=True)
    p.add_argument("--version", default="v1.0-mini")
    p.add_argument("--scene", default="scene-0061")
    p.add_argument("--domain", type=int, default=1)
    p.add_argument("--rate-hz", type=float, default=2.0)
    p.add_argument("--max-samples", type=int, default=0, help="0 = all samples")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
