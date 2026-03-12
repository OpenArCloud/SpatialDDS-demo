#!/usr/bin/env python3
"""Subscribe to DeepSense SpatialDDS topics and log to Rerun."""

from __future__ import annotations

import argparse
import json
import queue
import sys
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")

import numpy as np
import rerun as rr
from scipy.io import loadmat

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deepsense.radar_processing import radar_cube_to_range_angle, radar_cube_to_range_doppler, render_beam_polar
from nuscenes.dds_envelope_transport import EnvelopeTransport


def set_time(stamp: Dict[str, Any]) -> None:
    rr.set_time("timestamp", timestamp=(int(stamp.get("sec", 0)) + int(stamp.get("nanosec", 0)) / 1e9))


class Subscriber:
    def __init__(self, dataroot: Path, domain: int) -> None:
        self.dataroot = dataroot
        self.inbox: "queue.Queue[object]" = queue.Queue()
        self.transport = EnvelopeTransport(self.on_envelope, domain, "deepsense-rerun-subscriber")

    def on_envelope(self, envelope: object) -> None:
        if envelope.logical_topic.startswith("spatialdds/deepsense/"):
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
            self.handle(env.msg_type, payload)
            if env.msg_type == "DEEPSENSE_RF_BEAM_FRAME":
                seen += 1
            if max_frames > 0 and seen >= max_frames:
                return

    def handle(self, msg_type: str, payload: Dict[str, Any]) -> None:
        if msg_type == "DEEPSENSE_RF_BEAM_FRAME":
            self.handle_beam(payload)
        elif msg_type == "DEEPSENSE_RAD_TENSOR_FRAME":
            self.handle_radar(payload)
        elif msg_type == "DEEPSENSE_VISION_FRAME":
            self.handle_vision(payload)
        elif msg_type == "DEEPSENSE_UNIT1_GEOPOSE":
            self.handle_geo("world/unit1", payload)
        elif msg_type == "DEEPSENSE_UNIT2_GEOPOSE":
            self.handle_geo("world/unit2", payload)
        elif msg_type == "DEEPSENSE_LIDAR2D_FRAME":
            self.handle_lidar(payload)
        elif msg_type == "DEEPSENSE_DET2D_SET":
            self.handle_det2d(payload)

    def handle_beam(self, payload: Dict[str, Any]) -> None:
        set_time(payload["stamp"])
        power = np.asarray(payload["power"], dtype=np.float32)
        best_idx = int(payload["best_beam_idx"])
        polar = render_beam_polar(power, best_idx)
        rr.log("world/unit1/beam_power", rr.Image(polar))
        status = f"Best beam: {best_idx} | Power: {payload['best_beam_power']:.3f} | Blocked: {payload['is_blocked']}"
        rr.log("status", rr.TextDocument(status))

    def handle_radar(self, payload: Dict[str, Any]) -> None:
        hdr = payload["hdr"]
        set_time(hdr["t_start"])
        blob = hdr["blobs"][0]["blob_id"]
        cube = np.asarray(loadmat(self.dataroot / blob)["data"], dtype=np.complex64)
        rr.log("world/unit1/radar/range_angle", rr.Image(radar_cube_to_range_angle(cube)))
        rr.log("world/unit1/radar/range_doppler", rr.Image(radar_cube_to_range_doppler(cube)))

    def handle_vision(self, payload: Dict[str, Any]) -> None:
        hdr = payload["hdr"]
        set_time(hdr["t_start"])
        blob = hdr["blobs"][0]["blob_id"]
        path = self.dataroot / blob
        if path.exists():
            rr.log("world/unit1/camera", rr.EncodedImage(path=str(path)))

    def handle_geo(self, entity: str, payload: Dict[str, Any]) -> None:
        set_time(payload["stamp"])
        rr.log(entity, rr.GeoPoints(lat_lon=[(float(payload["lat_deg"]), float(payload["lon_deg"]))]))

    def handle_lidar(self, payload: Dict[str, Any]) -> None:
        set_time(payload["stamp"])
        points = np.asarray(payload["points"], dtype=np.float32)
        rr.log("world/unit1/lidar", rr.Points2D(points[:, :2]))

    def handle_det2d(self, payload: Dict[str, Any]) -> None:
        set_time(payload["stamp"])
        dets = payload.get("detections", [])
        if not dets:
            return
        mins = []
        sizes = []
        labels = []
        for d in dets:
            bbox = d["bbox"]
            mins.append([bbox["x"], bbox["y"]])
            sizes.append([bbox["w"], bbox["h"]])
            labels.append(d["class_id"])
        rr.log("world/unit1/camera", rr.Boxes2D(mins=np.asarray(mins), sizes=np.asarray(sizes), labels=labels))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeepSense SpatialDDS Rerun subscriber")
    parser.add_argument("--dataroot", required=True)
    parser.add_argument("--domain", type=int, default=1)
    parser.add_argument("--app-id", default="spatialdds_deepsense_demo")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--spawn-viewer", action="store_true")
    parser.add_argument("--connect-grpc", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rr.init(args.app_id, spawn=args.spawn_viewer)
    if args.connect_grpc:
        rr.connect_grpc(args.connect_grpc)
    rr.log("description", rr.TextDocument("DeepSense 6G Scenario 9 SpatialDDS Demo"))
    sub = Subscriber(Path(args.dataroot), args.domain)
    sub.start()
    try:
        sub.spin(args.max_frames)
    except KeyboardInterrupt:
        pass
    finally:
        sub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
