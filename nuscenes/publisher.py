#!/usr/bin/env python3
"""Publish nuScenes sample data on SpatialDDS logical topics."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import numpy as np
from nuscenes.nuscenes import NuScenes
from nuscenes.utils.data_classes import RadarPointCloud

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from spatialdds_demo import payloads
from sensor_types import to_dict
from nuscenes_to_spatialdds import (
    camera_to_vision_meta,
    ego_pose_to_spatialdds,
    lidar_to_meta_and_frame,
    radar_to_detection_set,
    sample_annotations_to_set,
    sample_data_to_vision_frame,
)

CAM_CHANNELS = [
    "CAM_FRONT",
    "CAM_FRONT_LEFT",
    "CAM_FRONT_RIGHT",
    "CAM_BACK",
    "CAM_BACK_LEFT",
    "CAM_BACK_RIGHT",
]
RADAR_CHANNELS = [
    "RADAR_FRONT",
    "RADAR_FRONT_LEFT",
    "RADAR_FRONT_RIGHT",
    "RADAR_BACK_LEFT",
    "RADAR_BACK_RIGHT",
]
LIDAR_CHANNEL = "LIDAR_TOP"


def _topic_vision(cam: str) -> str:
    return f"spatialdds/nuscenes/vision/{cam}/frame/v1"


def _topic_lidar() -> str:
    return "spatialdds/nuscenes/lidar/LIDAR_TOP/frame/v1"


def _topic_radar(ch: str) -> str:
    return f"spatialdds/nuscenes/rad/{ch}/frame/v1"


def _topic_det3d() -> str:
    return "spatialdds/nuscenes/semantics/det3d/v1"


def _topic_ego() -> str:
    return "spatialdds/nuscenes/ego/pose/v1"


def _topic_geo() -> str:
    return "spatialdds/nuscenes/geo/ego/pose/v1"


def _topic_vision_meta(cam: str) -> str:
    # Metadata is its own lane: a different type, and a latched one, so a
    # late joiner gets the calibration without waiting for a republish.
    return f"spatialdds/nuscenes/vision/{cam}/meta/v1"


def _topic_lidar_meta() -> str:
    return "spatialdds/nuscenes/lidar/LIDAR_TOP/meta/v1"


def _iter_scene_samples(nusc: NuScenes, scene_name: str):
    scene = next((s for s in nusc.scene if s["name"] == scene_name), None)
    if scene is None:
        raise ValueError(f"Scene not found: {scene_name}")

    token = scene["first_sample_token"]
    while token:
        sample = nusc.get("sample", token)
        yield sample
        token = sample["next"]


# Each lane: (§3.3.2 type name, §3.3.3 QoS profile). The announce and the
# writers are built from this, so a lane cannot be announced under one type
# and published as another.
TYPE_EGO_POSE   = ("framed_pose", "POSE_RT")
TYPE_GEO_POSE   = ("geopose", "POSE_RT")
TYPE_VISION_META = ("video_meta", "MAP_META")
TYPE_VISION      = ("video_frame", "VIDEO_LIVE")
TYPE_LIDAR_META  = ("lidar_meta", "MAP_META")
TYPE_LIDAR       = ("lidar_frame", "GEOM_TILE")
TYPE_RAD_DET     = ("radar_detection", "RADAR_RT")
TYPE_DET3D       = ("detection3d", "RADAR_RT")


def run(args: argparse.Namespace) -> int:
    dataroot = Path(args.dataroot)
    if not dataroot.exists():
        raise FileNotFoundError(f"dataroot does not exist: {dataroot}")

    nusc = NuScenes(version=args.version, dataroot=str(dataroot), verbose=not args.quiet)

    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo import topic_types, typed_transport as tt

    participant = DomainParticipant(args.domain)
    writers: Dict[str, tt.TypedDictWriter] = {}

    def publish(topic: str, lane, payload: Dict) -> None:
        """Write one sample on `topic`, building it into the lane's type."""
        type_name, profile = lane
        writer = writers.get(topic)
        if writer is None:
            writer = tt.TypedDictWriter(
                participant, topic, topic_types.resolve(type_name), profile)
            writers[topic] = writer
        writer.write(payload)

    sent_meta = set()
    frame_seq = 0
    sample_delay = 1.0 / max(0.1, args.rate_hz)

    try:
        for sample in _iter_scene_samples(nusc, args.scene):
            frame_seq += 1

            lidar_sd = nusc.get("sample_data", sample["data"][LIDAR_CHANNEL])
            ego_pose = nusc.get("ego_pose", lidar_sd["ego_pose_token"])
            pose, geopose = ego_pose_to_spatialdds(ego_pose)
            stamp = {"sec": ego_pose["timestamp"] // 1_000_000,
                     "nanosec": (ego_pose["timestamp"] % 1_000_000) * 1000}
            # The old payload bundled pose_se3 and geopose in one message
            # with a frame_seq — a shape no spec type has. A local pose and a
            # geographic one are two types, so they are two lanes; a consumer
            # that wants only the geographic pose subscribes to only that.
            publish(_topic_ego(), TYPE_EGO_POSE, {
                "pose": to_dict(pose),
                "frame_ref": payloads.frame_ref("nuscenes/map"),
                "cov": dict(payloads.COV_NONE),
                "stamp": stamp,
            })
            publish(_topic_geo(), TYPE_GEO_POSE, {**to_dict(geopose),
                                                  "stamp": stamp})

            for cam in CAM_CHANNELS:
                if cam not in sample["data"]:
                    continue
                sd = nusc.get("sample_data", sample["data"][cam])
                cs = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])

                if cam not in sent_meta:
                    meta = camera_to_vision_meta(sd, cs)
                    publish(_topic_vision_meta(cam), TYPE_VISION_META, to_dict(meta))
                    sent_meta.add(cam)

                frame = sample_data_to_vision_frame(sd, frame_seq)
                publish(_topic_vision(cam), TYPE_VISION, to_dict(frame))

            if LIDAR_CHANNEL in sample["data"]:
                sd = nusc.get("sample_data", sample["data"][LIDAR_CHANNEL])
                cs = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
                lidar_meta, lidar_frame = lidar_to_meta_and_frame(sd, cs, frame_seq)
                if LIDAR_CHANNEL not in sent_meta:
                    publish(_topic_lidar_meta(), TYPE_LIDAR_META, to_dict(lidar_meta))
                    sent_meta.add(LIDAR_CHANNEL)
                publish(_topic_lidar(), TYPE_LIDAR, to_dict(lidar_frame))

            for radar in RADAR_CHANNELS:
                if radar not in sample["data"]:
                    continue
                sd = nusc.get("sample_data", sample["data"][radar])
                radar_path = dataroot / sd["filename"]
                if not radar_path.exists():
                    continue
                radar_pc = RadarPointCloud.from_file(str(radar_path))
                det_set = radar_to_detection_set(sd, radar_pc.points, frame_seq)
                publish(_topic_radar(radar), TYPE_RAD_DET, to_dict(det_set))

            det_set = sample_annotations_to_set(nusc, sample, frame_seq)
            publish(_topic_det3d(), TYPE_DET3D, to_dict(det_set))

            if not args.quiet:
                print(f"[publisher] frame_seq={frame_seq} sample={sample['token']}", file=sys.stderr)

            time.sleep(sample_delay)

            if args.max_samples > 0 and frame_seq >= args.max_samples:
                break
    finally:
        pass

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="nuScenes SpatialDDS publisher")
    parser.add_argument("--dataroot", required=True)
    parser.add_argument("--version", default="v1.0-mini")
    parser.add_argument("--scene", default="scene-0061")
    parser.add_argument("--domain", type=int, default=1)
    parser.add_argument("--rate-hz", type=float, default=2.0)
    parser.add_argument("--max-samples", type=int, default=0, help="0 = all samples")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
