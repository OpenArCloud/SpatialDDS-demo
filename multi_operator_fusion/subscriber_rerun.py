#!/usr/bin/env python3
"""Rerun subscriber for the multi-operator fusion demo.

Routes each operator's streams under ``world/{operator}/…`` and the
platform fuser's output under ``world/fused/…``, giving a split-screen
view of who-sees-what versus the unified intersection model.

Subscribed topics (matched by ``logical_topic`` prefix / ``msg_type``):
  * spatialdds/{operator}/ego/pose/v1                 NUSC_EGO_POSE
  * spatialdds/{operator}/vision/{cam}/frame/v1       NUSC_VISION_{META,FRAME}
  * spatialdds/{operator}/lidar/{ch}/frame/v1         NUSC_LIDAR_{META,FRAME}
  * spatialdds/{operator}/rad/{ch}/frame/v1           NUSC_RAD_DET_SET
  * spatialdds/{operator}/sensing/detection3d/v1      NUSC_DET3D_SET
  * spatialdds/platform/fusion/track/v1               NUSC_FUSED_TRACK_SET
  * spatialdds/platform/fusion/coverage/v1            NUSC_FUSION_COVERAGE
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import rerun as rr

REPO_ROOT = Path(__file__).resolve().parents[1]
NUSCENES_DIR = REPO_ROOT / "nuscenes"
for _p in (REPO_ROOT, NUSCENES_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dds_envelope_transport import EnvelopeTransport  # noqa: E402

from routing import color_for_operator, operator_from_topic  # noqa: E402


def _stamp_ns(stamp: Dict[str, Any]) -> int:
    return int(stamp.get("sec", 0)) * 1_000_000_000 + int(stamp.get("nanosec", 0))


def _set_time(stamp: Dict[str, Any]) -> None:
    rr.set_time_nanos("timestamp", _stamp_ns(stamp))


def _q_xyzw(q: Dict[str, float]) -> List[float]:
    return [float(q.get("x", 0.0)), float(q.get("y", 0.0)),
            float(q.get("z", 0.0)), float(q.get("w", 1.0))]


class RerunMultiOpSubscriber:
    def __init__(self, dataroot: Path, domain: int) -> None:
        self.dataroot = dataroot
        self.inbox: "queue.Queue[object]" = queue.Queue()
        self.transport = EnvelopeTransport(
            on_message_callback=self._on_envelope,
            domain_id=domain,
            local_sender_id="multi-op-rerun-subscriber",
        )

    def _on_envelope(self, envelope: object) -> None:
        topic = getattr(envelope, "logical_topic", "") or ""
        if not topic.startswith("spatialdds/"):
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
            try:
                payload = json.loads(env.payload_json)
            except (json.JSONDecodeError, TypeError):
                continue
            operator = operator_from_topic(env.logical_topic) or "unknown"
            self._dispatch(env.msg_type, env.logical_topic, operator, payload)
            if env.msg_type in {"NUSC_EGO_POSE", "NUSC_DET3D_SET", "NUSC_FUSED_TRACK_SET"}:
                seen += 1
            if max_frames > 0 and seen >= max_frames:
                return

    def _dispatch(self, msg_type: str, topic: str, operator: str, payload: Dict[str, Any]) -> None:
        if msg_type == "NUSC_EGO_POSE":
            self._handle_ego(operator, payload)
        elif msg_type == "NUSC_VISION_META":
            self._handle_vision_meta(operator, payload)
        elif msg_type == "NUSC_VISION_FRAME":
            self._handle_vision_frame(operator, payload)
        elif msg_type == "NUSC_LIDAR_FRAME":
            self._handle_lidar(operator, payload)
        elif msg_type == "NUSC_RAD_DET_SET":
            self._handle_radar(operator, payload)
        elif msg_type == "NUSC_DET3D_SET":
            self._handle_det3d(operator, payload)
        elif msg_type == "NUSC_FUSED_TRACK_SET":
            self._handle_fused_tracks(payload)
        elif msg_type == "NUSC_FUSION_COVERAGE":
            self._handle_coverage(payload)

    def _handle_ego(self, operator: str, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        pose = payload.get("pose_se3", {})
        t = pose.get("t", {})
        q = pose.get("q", {})
        rr.log(
            f"world/{operator}/ego_vehicle",
            rr.Transform3D(
                translation=[float(t.get("x", 0.0)), float(t.get("y", 0.0)), float(t.get("z", 0.0))],
                rotation=rr.Quaternion(xyzw=_q_xyzw(q)),
                relation=rr.TransformRelation.ParentFromChild,
            ),
        )
        geo = payload.get("geopose")
        if geo:
            rr.log(
                f"world/{operator}/ego_vehicle",
                rr.GeoPoints(lat_lon=[(float(geo["lat_deg"]), float(geo["lon_deg"]))]),
            )

    def _handle_vision_meta(self, operator: str, payload: Dict[str, Any]) -> None:
        cam = payload.get("cam", {})
        stream = payload.get("stream_id", "camera")
        rr.log(
            f"world/{operator}/ego_vehicle/{stream}",
            rr.Pinhole(
                resolution=[int(cam.get("width", 1600)), int(cam.get("height", 900))],
                focal_length=[float(cam.get("fx", 1.0)), float(cam.get("fy", 1.0))],
                principal_point=[float(cam.get("cx", 0.0)), float(cam.get("cy", 0.0))],
            ),
        )

    def _handle_vision_frame(self, operator: str, payload: Dict[str, Any]) -> None:
        hdr = payload.get("hdr", {})
        _set_time(hdr.get("t_start", {}))
        stream = payload.get("stream_id", "camera")
        blobs = hdr.get("blobs", [])
        if not blobs:
            return
        path = self.dataroot / blobs[0].get("blob_id", "")
        if path.exists():
            rr.log(f"world/{operator}/ego_vehicle/{stream}", rr.EncodedImage(path=str(path)))

    def _handle_lidar(self, operator: str, payload: Dict[str, Any]) -> None:
        hdr = payload.get("hdr", {})
        _set_time(hdr.get("t_start", {}))
        blobs = hdr.get("blobs", [])
        if not blobs:
            return
        path = self.dataroot / blobs[0].get("blob_id", "")
        if not path.exists():
            return
        arr = np.fromfile(str(path), dtype=np.float32)
        if arr.size % 5 != 0:
            return
        points = arr.reshape((-1, 5))[:, :3]
        rr.log(f"world/{operator}/ego_vehicle/LIDAR_TOP",
               rr.Points3D(points, colors=color_for_operator(operator)))

    def _handle_radar(self, operator: str, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        dets = payload.get("detections", [])
        pts = [[float(d.get("xyz_m", {}).get("x", 0.0)),
                float(d.get("xyz_m", {}).get("y", 0.0)),
                float(d.get("xyz_m", {}).get("z", 0.0))] for d in dets]
        if pts:
            stream = payload.get("stream_id", "RADAR")
            rr.log(f"world/{operator}/ego_vehicle/{stream}",
                   rr.Points3D(np.array(pts), colors=color_for_operator(operator)))

    def _handle_det3d(self, operator: str, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        dets = payload.get("detections", [])
        if not dets:
            return
        centers, sizes, quats, labels = [], [], [], []
        for d in dets:
            c = d.get("center", {}); s = d.get("size", {}); q = d.get("q", {})
            centers.append([float(c.get("x", 0.0)), float(c.get("y", 0.0)), float(c.get("z", 0.0))])
            sizes.append([float(s.get("x", 0.0)), float(s.get("z", 0.0)), float(s.get("y", 0.0))])
            quats.append(_q_xyzw(q))
            labels.append(str(d.get("class_id", "unknown")))
        rr.log(
            f"world/{operator}/detections",
            rr.Boxes3D(
                centers=np.array(centers),
                sizes=np.array(sizes),
                quaternions=np.array(quats, dtype=np.float32),
                labels=labels,
                colors=color_for_operator(operator),
            ),
        )

    def _handle_fused_tracks(self, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        tracks = payload.get("tracks", [])
        if not tracks:
            rr.log("world/fused/tracks", rr.Clear(recursive=False))
            return
        centers, labels, colors = [], [], []
        for t in tracks:
            p = t.get("position", {})
            centers.append([float(p.get("x", 0.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0))])
            ops = t.get("source_operators", [])
            labels.append(f"{t.get('object_class', '?')} [n={t.get('source_count', 0)}] "
                          f"{','.join(ops)}")
            colors.append(color_for_operator("platform"))
        rr.log(
            "world/fused/tracks",
            rr.Points3D(np.array(centers), labels=labels, colors=colors, radii=0.7),
        )

    def _handle_coverage(self, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        m = payload.get("metrics", {})
        per_op = m.get("per_operator_track_count", {})
        per_op_str = ", ".join(f"{op}:{n}" for op, n in sorted(per_op.items())) or "—"
        rr.log("fusion/metrics", rr.TextLog(
            f"tracks={m.get('track_count', 0)} "
            f"multi_source={m.get('multi_source_count', 0)} "
            f"({m.get('multi_source_pct', 0.0) * 100:.0f}%) "
            f"improvement={m.get('coverage_improvement', 0.0):.2f}x "
            f"per_op=[{per_op_str}]"
        ))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-operator SpatialDDS Rerun subscriber")
    p.add_argument("--dataroot", required=True,
                   help="nuScenes dataroot (blobs for vision/lidar frames)")
    p.add_argument("--domain", type=int, default=1)
    p.add_argument("--app-id", default="spatialdds_multi_op_fusion")
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--spawn-viewer", action="store_true")
    p.add_argument("--connect-grpc", default="",
                   help="Connect to a running Rerun gRPC endpoint (e.g. 127.0.0.1:9876)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    rr.init(args.app_id, spawn=args.spawn_viewer)
    if args.connect_grpc:
        rr.connect_grpc(args.connect_grpc)
    rr.log("description", rr.TextDocument("SpatialDDS Multi-Operator Fusion Demo"))

    sub = RerunMultiOpSubscriber(Path(args.dataroot), args.domain)
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
