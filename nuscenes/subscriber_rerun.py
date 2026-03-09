#!/usr/bin/env python3
"""Subscribe to SpatialDDS nuScenes logical topics and visualize in Rerun."""

from __future__ import annotations

import argparse
import json
import queue
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import rerun as rr

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dds_envelope_transport import EnvelopeTransport


def _stamp_ns(stamp: Dict[str, Any]) -> int:
    return int(stamp.get("sec", 0)) * 1_000_000_000 + int(stamp.get("nanosec", 0))


def _set_time(stamp: Dict[str, Any]) -> None:
    rr.set_time_nanos("timestamp", _stamp_ns(stamp))


def _q_xyzw(q: Dict[str, float]) -> List[float]:
    return [float(q.get("x", 0.0)), float(q.get("y", 0.0)), float(q.get("z", 0.0)), float(q.get("w", 1.0))]


class RerunSubscriber:
    def __init__(self, dataroot: Path, domain: int) -> None:
        self.dataroot = dataroot
        self.inbox: "queue.Queue[object]" = queue.Queue()
        self.transport = EnvelopeTransport(
            on_message_callback=self._on_envelope,
            domain_id=domain,
            local_sender_id="nuscenes-rerun-subscriber",
        )

    def _on_envelope(self, envelope: object) -> None:
        if not envelope.logical_topic.startswith("spatialdds/nuscenes/"):
            return
        self.inbox.put(envelope)

    def start(self) -> None:
        self.transport.start()

    def stop(self) -> None:
        self.transport.stop()

    def spin(self, max_frames: int = 0) -> None:
        seen = 0
        while True:
            env = self.inbox.get()
            payload = json.loads(env.payload_json)
            self._handle(env.msg_type, env.logical_topic, payload)
            if env.msg_type in {"NUSC_EGO_POSE", "NUSC_DET3D_SET"}:
                seen += 1
            if max_frames > 0 and seen >= max_frames:
                return

    def _handle(self, msg_type: str, logical_topic: str, payload: Dict[str, Any]) -> None:
        if msg_type == "NUSC_EGO_POSE":
            self._handle_ego(payload)
        elif msg_type == "NUSC_VISION_META":
            self._handle_vision_meta(payload)
        elif msg_type == "NUSC_VISION_FRAME":
            self._handle_vision_frame(payload)
        elif msg_type == "NUSC_LIDAR_FRAME":
            self._handle_lidar(payload)
        elif msg_type == "NUSC_RAD_DET_SET":
            self._handle_radar(payload)
        elif msg_type == "NUSC_DET3D_SET":
            self._handle_det3d(payload)

    def _handle_ego(self, payload: Dict[str, Any]) -> None:
        stamp = payload.get("stamp", {"sec": 0, "nanosec": 0})
        _set_time(stamp)
        pose = payload.get("pose_se3", {})
        t = pose.get("t", {})
        q = pose.get("q", {})
        rr.log(
            "world/ego_vehicle",
            rr.Transform3D(
                translation=[float(t.get("x", 0.0)), float(t.get("y", 0.0)), float(t.get("z", 0.0))],
                rotation=rr.Quaternion(xyzw=_q_xyzw(q)),
                relation=rr.TransformRelation.ParentFromChild,
            ),
        )

        geo = payload.get("geopose")
        if geo:
            rr.log(
                "world/ego_vehicle",
                rr.GeoPoints(lat_lon=[(float(geo["lat_deg"]), float(geo["lon_deg"]))]),
            )

    def _handle_vision_meta(self, payload: Dict[str, Any]) -> None:
        cam = payload.get("cam", {})
        stream_id = payload.get("stream_id", "camera")
        rr.log(
            f"world/ego_vehicle/{stream_id}",
            rr.Pinhole(
                resolution=[int(cam.get("width", 1600)), int(cam.get("height", 900))],
                focal_length=[float(cam.get("fx", 1.0)), float(cam.get("fy", 1.0))],
                principal_point=[float(cam.get("cx", 0.0)), float(cam.get("cy", 0.0))],
            ),
        )

    def _handle_vision_frame(self, payload: Dict[str, Any]) -> None:
        hdr = payload.get("hdr", {})
        _set_time(hdr.get("t_start", {"sec": 0, "nanosec": 0}))
        stream_id = payload.get("stream_id", "camera")
        blobs = hdr.get("blobs", [])
        if not blobs:
            return
        image_path = self.dataroot / blobs[0].get("blob_id", "")
        if image_path.exists():
            rr.log(f"world/ego_vehicle/{stream_id}", rr.EncodedImage(path=str(image_path)))

    def _handle_lidar(self, payload: Dict[str, Any]) -> None:
        hdr = payload.get("hdr", {})
        _set_time(hdr.get("t_start", {"sec": 0, "nanosec": 0}))
        blobs = hdr.get("blobs", [])
        if not blobs:
            return
        lidar_path = self.dataroot / blobs[0].get("blob_id", "")
        if not lidar_path.exists():
            return
        arr = np.fromfile(str(lidar_path), dtype=np.float32)
        if arr.size % 5 != 0:
            return
        points = arr.reshape((-1, 5))[:, :3]
        rr.log("world/ego_vehicle/LIDAR_TOP", rr.Points3D(points))

    def _handle_radar(self, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {"sec": 0, "nanosec": 0}))
        detections = payload.get("detections", [])
        pts = []
        for d in detections:
            xyz = d.get("xyz_m", {})
            pts.append([float(xyz.get("x", 0.0)), float(xyz.get("y", 0.0)), float(xyz.get("z", 0.0))])
        if pts:
            stream_id = payload.get("stream_id", "RADAR")
            rr.log(f"world/ego_vehicle/{stream_id}", rr.Points3D(np.array(pts), colors=[255, 200, 0]))

    def _handle_det3d(self, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {"sec": 0, "nanosec": 0}))
        dets = payload.get("detections", [])
        if not dets:
            return
        centers = []
        sizes = []
        quats = []
        labels = []
        for d in dets:
            c = d.get("center", {})
            s = d.get("size", {})
            q = d.get("q", {})
            centers.append([float(c.get("x", 0.0)), float(c.get("y", 0.0)), float(c.get("z", 0.0))])
            sizes.append([float(s.get("x", 0.0)), float(s.get("z", 0.0)), float(s.get("y", 0.0))])
            quats.append(_q_xyzw(q))
            labels.append(str(d.get("class_id", "unknown")))
        rr.log(
            "world/anns",
            rr.Boxes3D(
                centers=np.array(centers),
                sizes=np.array(sizes),
                quaternions=np.array(quats, dtype=np.float32),
                labels=labels,
            ),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SpatialDDS nuScenes Rerun subscriber")
    parser.add_argument("--dataroot", required=True)
    parser.add_argument("--domain", type=int, default=1)
    parser.add_argument("--app-id", default="spatialdds_nuscenes_demo")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--spawn-viewer", action="store_true")
    parser.add_argument("--connect-grpc", default="", help="Connect to a running Rerun gRPC endpoint, e.g. 127.0.0.1:9876")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rr.init(args.app_id, spawn=args.spawn_viewer)
    if args.connect_grpc:
        rr.connect_grpc(args.connect_grpc)
    rr.log("description", rr.TextDocument("SpatialDDS nuScenes Demo"))

    sub = RerunSubscriber(Path(args.dataroot), args.domain)
    sub.start()
    try:
        sub.spin(max_frames=args.max_frames)
    except KeyboardInterrupt:
        pass
    finally:
        sub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
