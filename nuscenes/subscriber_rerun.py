#!/usr/bin/env python3
"""Subscribe to SpatialDDS nuScenes logical topics and visualize in Rerun."""

from __future__ import annotations

import argparse
import json
import queue
import threading
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import rerun as rr

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import topic_types, typed_transport as tt
from spatialdds_demo.json_mapping import to_json


def _stamp_ns(stamp: Dict[str, Any]) -> int:
    return int(stamp.get("sec", 0)) * 1_000_000_000 + int(stamp.get("nanosec", 0))


def _set_time(stamp: Dict[str, Any]) -> None:
    rr.set_time_nanos("timestamp", _stamp_ns(stamp))


def _xyz(value: Any, default=(0.0, 0.0, 0.0)) -> List[float]:
    """A Vec3 as [x, y, z]. Vec3 is ``double[3]``, so it arrives as an array."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return [float(v) for v in value[:3]]
    if isinstance(value, dict):
        return [float(value.get(k, d)) for k, d in zip("xyz", default)]
    return list(default)


def _q_xyzw(q: Any) -> List[float]:
    """A QuaternionXYZW as [x, y, z, w]. Identity when absent."""
    if isinstance(q, (list, tuple)) and len(q) >= 4:
        return [float(v) for v in q[:4]]
    q = q if isinstance(q, dict) else {}
    return [float(q.get("x", 0.0)), float(q.get("y", 0.0)),
            float(q.get("z", 0.0)), float(q.get("w", 1.0))]


class RerunSubscriber:
    """
    A typed reader per lane the nuScenes publisher owns.

    Named rather than discovery-driven: this demo is one publisher and one
    subscriber that ship together, so the two share the publisher's lane
    table directly. The multi-operator demo, where services come and go,
    reads announces instead.
    """

    def __init__(self, dataroot: Path, domain: int) -> None:
        self.dataroot = dataroot
        self.inbox: "queue.Queue[tuple]" = queue.Queue()
        self._participant = DomainParticipant(domain)
        self._readers = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        import publisher as pub

        # (lane key, topic, lane). Cameras are per-channel, so they are
        # built from CAM_CHANNELS rather than named one by one.
        lanes = [
            ("ego", pub._topic_ego(), pub.TYPE_EGO_POSE),
            ("geo", pub._topic_geo(), pub.TYPE_GEO_POSE),
            ("lidar_meta", pub._topic_lidar_meta(), pub.TYPE_LIDAR_META),
            ("lidar", pub._topic_lidar(), pub.TYPE_LIDAR),
            ("det3d", pub._topic_det3d(), pub.TYPE_DET3D),
        ]
        for cam in pub.CAM_CHANNELS:
            lanes.append(("vision_meta", pub._topic_vision_meta(cam),
                          pub.TYPE_VISION_META))
            lanes.append(("vision", pub._topic_vision(cam), pub.TYPE_VISION))
        for radar in pub.RADAR_CHANNELS:
            lanes.append(("radar", pub._topic_radar(radar), pub.TYPE_RAD_DET))

        for key, topic, (type_name, profile) in lanes:
            self._readers[topic] = (
                tt.make_reader(self._participant, topic,
                               topic_types.resolve(type_name), profile),
                key,
            )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _poll(self) -> None:
        while not self._stop.is_set():
            for _topic, (reader, key) in self._readers.items():
                for sample in tt.take_samples(reader):
                    self.inbox.put((key, to_json(sample)))
            self._stop.wait(0.02)

    def spin(self, max_frames: int = 0) -> None:
        seen = 0
        while True:
            key, payload = self.inbox.get()
            self._handle(key, payload)
            if key in {"ego", "det3d"}:
                seen += 1
            if max_frames > 0 and seen >= max_frames:
                return

    def _handle(self, lane: str, payload: Dict[str, Any]) -> None:
        if lane == "ego":
            self._handle_ego(payload)
        elif lane == "geo":
            self._handle_geo(payload)
        elif lane == "vision_meta":
            self._handle_vision_meta(payload)
        elif lane == "vision":
            self._handle_vision_frame(payload)
        elif lane == "lidar":
            self._handle_lidar(payload)
        elif lane == "radar":
            self._handle_radar(payload)
        elif lane == "det3d":
            self._handle_det3d(payload)

    def _handle_ego(self, payload: Dict[str, Any]) -> None:
        """A FramedPose: the pose, and the frame it means something in."""
        _set_time(payload.get("stamp", {"sec": 0, "nanosec": 0}))
        pose = payload.get("pose", {})
        rr.log(
            "world/ego_vehicle",
            rr.Transform3D(
                translation=_xyz(pose.get("t")),
                rotation=rr.Quaternion(xyzw=_q_xyzw(pose.get("q"))),
                relation=rr.TransformRelation.ParentFromChild,
            ),
        )

    def _handle_geo(self, payload: Dict[str, Any]) -> None:
        """The geographic pose, which is its own type on its own lane."""
        _set_time(payload.get("stamp", {"sec": 0, "nanosec": 0}))
        rr.log("world/ego_vehicle", rr.GeoPoints(
            lat_lon=[(float(payload["lat_deg"]), float(payload["lon_deg"]))]))

    def _handle_vision_meta(self, payload: Dict[str, Any]) -> None:
        # `K` — the camera matrix. The old code read a `cam` field VisionMeta
        # has never had, so the pinhole was always the fallback resolution.
        cam = payload.get("K") or payload.get("cam") or {}
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
        pts = [_xyz(d.get("xyz_m"))
               for d in (payload.get("dets") or payload.get("detections") or [])]
        if pts:
            stream_id = payload.get("stream_id", "RADAR")
            rr.log(f"world/ego_vehicle/{stream_id}", rr.Points3D(np.array(pts), colors=[255, 200, 0]))

    def _handle_det3d(self, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {"sec": 0, "nanosec": 0}))
        dets = payload.get("dets") or payload.get("detections") or []
        if not dets:
            return
        centers, sizes, quats, labels = [], [], [], []
        for row in dets:
            # OperatorDetectionSet composes the spec Detection3D; a bare
            # Detection3DSet is flat.
            d = row.get("detection", row) if isinstance(row, dict) else row
            centers.append(_xyz(d.get("center")))
            sx, sy, sz = _xyz(d.get("size"))
            sizes.append([sx, sz, sy])
            quats.append(_q_xyzw(d.get("q")))
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
