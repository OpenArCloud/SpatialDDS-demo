#!/usr/bin/env python3
"""Subscribe to DeepSense SpatialDDS topics and log to Rerun."""

from __future__ import annotations

import argparse
import json
import queue
import threading
import sys
from pathlib import Path
from typing import Any, Dict, Optional, List

import matplotlib

matplotlib.use("Agg")

import numpy as np
import rerun as rr
from scipy.io import loadmat

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deepsense.radar_processing import radar_cube_to_range_angle, radar_cube_to_range_doppler, render_beam_polar
from cyclonedds.domain import DomainParticipant

from spatialdds_demo import blob as blob_transfer, topic_types, typed_transport as tt
from spatialdds_demo.json_mapping import to_json


def set_time(stamp: Dict[str, Any]) -> None:
    rr.set_time("timestamp", timestamp=(int(stamp.get("sec", 0)) + int(stamp.get("nanosec", 0)) / 1e9))


class Subscriber:
    """
    A typed reader per lane the DeepSense publisher owns.

    Named rather than discovery-driven: this demo is one publisher and one
    subscriber that ship together, so the lane table is shared directly. The
    multi-operator demo, where services come and go, reads announces.

    Routing is on the topic rather than the announced type because two lanes
    here carry the same type — unit1 and unit2 are both `geopose`, and which
    entity they belong to is the topic's business.
    """

    def __init__(self, dataroot: Path, domain: int) -> None:
        self.dataroot = dataroot
        self.inbox: "queue.Queue[tuple]" = queue.Queue()
        self._participant = DomainParticipant(domain)
        self._readers = {}
        self._blobs = blob_transfer.Reassembler()
        self._lidar_blobs: Dict[str, Dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        from publisher import LANES  # the publisher's own lane table

        self.lanes = dict(LANES)
        for key, (topic, type_name, profile) in self.lanes.items():
            self._readers[key] = (
                tt.make_reader(self._participant, topic,
                               topic_types.resolve(type_name), profile),
                topic,
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
            for key, (reader, _topic) in self._readers.items():
                for sample in tt.take_samples(reader):
                    self.inbox.put((key, to_json(sample)))
            self._stop.wait(0.02)

    def spin(self, max_frames: int = 0) -> None:
        seen = 0
        while True:
            key, payload = self.inbox.get()
            self.handle(key, payload)
            if key == "beam_frame":
                seen += 1
            if max_frames > 0 and seen >= max_frames:
                return

    def handle(self, lane: str, payload: Dict[str, Any]) -> None:
        if lane == "beam_frame":
            self.handle_beam(payload)
        elif lane == "radar_tensor":
            self.handle_radar(payload)
        elif lane == "vision_frame":
            self.handle_vision(payload)
        elif lane == "unit1_geo":
            self.handle_geo("world/unit1", payload)
        elif lane == "unit2_geo":
            self.handle_geo("world/unit2", payload)
        elif lane == "lidar_frame":
            self.handle_lidar(payload)
        elif lane == "blob":
            self.handle_blob(payload)
        elif lane == "detection2d":
            self.handle_det2d(payload)

    def handle_beam(self, payload: Dict[str, Any]) -> None:
        # RfBeamFrame has no top-level stamp; the frame header carries it.
        set_time(payload["hdr"]["t_start"])
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
        """
        A LidarFrame names its blob; the points arrive as chunks.

        The frame usually lands before the last chunk, so it is held until
        the blob completes rather than rendered empty.
        """
        set_time(payload["hdr"]["t_start"])
        blobs = payload["hdr"]["blobs"]
        if not blobs:
            return
        blob_id = blobs[0]["blob_id"]
        pending = self._lidar_blobs.pop(blob_id, None)
        if pending is None:
            self._lidar_blobs[blob_id] = payload
            return
        self._render_lidar(pending)

    def handle_blob(self, payload: Dict[str, Any]) -> None:
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.oarc_demo import BlobChunk

        try:
            data = self._blobs.feed(from_json(BlobChunk, payload))
        except blob_transfer.CorruptChunk as exc:
            print(f"[deepsense-subscriber] {exc}", file=sys.stderr)
            return
        if data is None:
            return
        blob_id = str(payload.get("blob_id") or "")
        frame = self._lidar_blobs.pop(blob_id, None)
        if frame is None:
            # Chunks arrived first; hold the bytes for the frame.
            self._lidar_blobs[blob_id] = data
            return
        self._render_lidar(data)

    def _render_lidar(self, data) -> None:
        if not isinstance(data, (bytes, bytearray)):
            return
        points = np.frombuffer(data, dtype=np.float32).reshape(-1, 4)
        rr.log("world/unit1/lidar", rr.Points2D(points[:, :2]))

    def handle_det2d(self, payload: Dict[str, Any]) -> None:
        set_time(payload["stamp"])
        dets = payload.get("dets") or payload.get("detections") or []
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
