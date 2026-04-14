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


def _offset_xyz(payload: Dict, keys_path: Iterable[Iterable[str]], offset: Tuple[float, float, float]) -> None:
    """Apply offset to nested x/y/z fields in-place.

    keys_path is a list of key paths; each path is a tuple of dict keys
    leading to a {x,y,z} dict. Missing paths are silently skipped.
    """
    ox, oy, oz = offset
    for path in keys_path:
        node = payload
        for k in path:
            if not isinstance(node, dict) or k not in node:
                node = None
                break
            node = node[k]
        if isinstance(node, dict) and "x" in node and "y" in node and "z" in node:
            node["x"] = float(node["x"]) + ox
            node["y"] = float(node["y"]) + oy
            node["z"] = float(node["z"]) + oz


def _stamp_operator(payload: Dict, operator: str) -> None:
    payload["source_operator"] = operator


def _publish(transport: EnvelopeTransport, topic: str, msg_type: str, payload: Dict) -> None:
    transport.publish(
        logical_topic=topic,
        msg_type=msg_type,
        payload_json=json.dumps(payload),
        request_id=str(payload.get("frame_seq", "")),
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
    from dds_envelope_transport import EnvelopeTransport
    from spatialdds_types import to_dict
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
    transport = EnvelopeTransport(
        on_message_callback=lambda _env: None,
        domain_id=args.domain,
        local_sender_id=f"multi-op-publisher-{operator}",
    )
    transport.start()

    sent_meta: set = set()
    frame_seq = 0
    delay = 1.0 / max(0.1, args.rate_hz)

    try:
        for sample in _iter_scene_samples(nusc, args.scene):
            frame_seq += 1

            lidar_sd = nusc.get("sample_data", sample["data"][LIDAR_CHANNEL])
            ego_pose = nusc.get("ego_pose", lidar_sd["ego_pose_token"])
            pose, geopose = ego_pose_to_spatialdds(ego_pose)
            ego_payload = {
                "frame_seq": frame_seq,
                "stamp": {"sec": ego_pose["timestamp"] // 1_000_000,
                          "nanosec": (ego_pose["timestamp"] % 1_000_000) * 1000},
                "pose_se3": to_dict(pose),
                "geopose": to_dict(geopose),
            }
            _offset_xyz(ego_payload, [("pose_se3", "t")], offset)
            _stamp_operator(ego_payload, operator)
            _publish(transport, _topic(operator, "ego", "pose", "v1"), "NUSC_EGO_POSE", ego_payload)

            if "camera" in enabled:
                for cam in CAM_CHANNELS:
                    if cam not in sample["data"]:
                        continue
                    sd = nusc.get("sample_data", sample["data"][cam])
                    cs = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
                    cam_topic = _topic(operator, "vision", cam, "frame", "v1")
                    if cam not in sent_meta:
                        meta = to_dict(camera_to_vision_meta(sd, cs))
                        _stamp_operator(meta, operator)
                        _publish(transport, cam_topic, "NUSC_VISION_META", meta)
                        sent_meta.add(cam)
                    frame = to_dict(sample_data_to_vision_frame(sd, frame_seq))
                    _stamp_operator(frame, operator)
                    _publish(transport, cam_topic, "NUSC_VISION_FRAME", frame)

            if "lidar" in enabled and LIDAR_CHANNEL in sample["data"]:
                sd = nusc.get("sample_data", sample["data"][LIDAR_CHANNEL])
                cs = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
                lidar_meta, lidar_frame = lidar_to_meta_and_frame(sd, cs, frame_seq)
                lidar_topic = _topic(operator, "lidar", LIDAR_CHANNEL, "frame", "v1")
                if LIDAR_CHANNEL not in sent_meta:
                    meta_d = to_dict(lidar_meta)
                    _stamp_operator(meta_d, operator)
                    _publish(transport, lidar_topic, "NUSC_LIDAR_META", meta_d)
                    sent_meta.add(LIDAR_CHANNEL)
                frame_d = to_dict(lidar_frame)
                _stamp_operator(frame_d, operator)
                _publish(transport, lidar_topic, "NUSC_LIDAR_FRAME", frame_d)

            if "radar" in enabled:
                for radar in RADAR_CHANNELS:
                    if radar not in sample["data"]:
                        continue
                    sd = nusc.get("sample_data", sample["data"][radar])
                    radar_path = dataroot / sd["filename"]
                    if not radar_path.exists():
                        continue
                    radar_pc = RadarPointCloud.from_file(str(radar_path))
                    det_set = to_dict(radar_to_detection_set(sd, radar_pc.points, frame_seq))
                    # offset radar detection xyz_m
                    for det in det_set.get("detections", []):
                        _offset_xyz(det, [("xyz_m",)], offset)
                    _stamp_operator(det_set, operator)
                    _publish(transport, _topic(operator, "rad", radar, "frame", "v1"), "NUSC_RAD_DET_SET", det_set)

            det_set = to_dict(sample_annotations_to_set(nusc, sample, frame_seq))
            for det in det_set.get("detections", []):
                _offset_xyz(det, [("center",)], offset)
            _stamp_operator(det_set, operator)
            _publish(transport, _topic(operator, "sensing", "detection3d", "v1"), "NUSC_DET3D_SET", det_set)

            if not args.quiet:
                print(f"[{operator}] frame_seq={frame_seq} sample={sample['token']}", file=sys.stderr)

            time.sleep(delay)
            if args.max_samples > 0 and frame_seq >= args.max_samples:
                break
    finally:
        transport.stop()
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
