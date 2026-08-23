#!/usr/bin/env python3
"""Publish DeepSense Scenario 9 data on typed SpatialDDS topics.

Each stream gets its own topic, its own §3.3.2 type and its own §3.3.3 QoS
lane. Metadata is latched (MAP_META) so a late joiner gets the calibration
without waiting for a republish; frames are not.

The lidar sweep travels as blob chunks with a `LidarFrame` referencing them,
which is what the spec asks for — the old payload inlined the point array
under a `points` key no type has.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deepsense.deepsense_to_spatialdds import iter_sequence, load_rows, make_beam_meta, make_radar_meta, make_vision_meta, row_to_beam_frame, row_to_detection2d, row_to_geoposes, row_to_lidar_points, row_to_radar_tensor, row_to_vision_frame, to_dict
from cyclonedds.domain import DomainParticipant

from spatialdds_demo import blob, topic_types, typed_transport as tt

# (topic, §3.3.2 type, §3.3.3 QoS profile) for every lane this publisher owns.
LANES = {
    "beam_meta":    ("spatialdds/deepsense/rf_beam/unit1_60ghz/meta/v1",
                     "rf_beam_meta", "MAP_META"),
    "beam_frame":   ("spatialdds/deepsense/rf_beam/unit1_60ghz/frame/v1",
                     "rf_beam", "RF_BEAM_RT"),
    "radar_meta":   ("spatialdds/deepsense/rad/unit1_radar/meta/v1",
                     "radar_tensor_meta", "MAP_META"),
    "radar_tensor": ("spatialdds/deepsense/rad/unit1_radar/tensor/v1",
                     "radar_tensor", "RADAR_RT"),
    "vision_meta":  ("spatialdds/deepsense/vision/unit1_cam/meta/v1",
                     "video_meta", "MAP_META"),
    "vision_frame": ("spatialdds/deepsense/vision/unit1_cam/frame/v1",
                     "video_frame", "VIDEO_LIVE"),
    "unit1_geo":    ("spatialdds/deepsense/geo/unit1/pose/v1",
                     "geopose", "POSE_RT"),
    "unit2_geo":    ("spatialdds/deepsense/geo/unit2/pose/v1",
                     "geopose", "POSE_RT"),
    "lidar_frame":  ("spatialdds/deepsense/lidar/unit1_lidar/frame/v1",
                     "lidar_frame", "GEOM_TILE"),
    "detection2d":  ("spatialdds/deepsense/semantics/det2d/v1",
                     "detection2d", "RADAR_RT"),
    "blob":         (blob.BLOB_TOPIC, blob.BLOB_TYPE, blob.BLOB_PROFILE),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeepSense SpatialDDS publisher")
    parser.add_argument("--dataroot", required=True)
    parser.add_argument("--sequence", type=int, default=1)
    parser.add_argument("--domain", type=int, default=1)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--max-samples", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataroot = Path(args.dataroot)
    rows = list(iter_sequence(load_rows(dataroot / "scenario9.csv"), args.sequence))
    participant = DomainParticipant(args.domain)
    writers = {
        key: tt.TypedDictWriter(participant, topic,
                                topic_types.resolve(type_name), profile)
        for key, (topic, type_name, profile) in LANES.items()
    }

    def publish(key: str, payload) -> None:
        writers[key].write(payload)

    sent_meta = False
    delay = 0.1 / max(args.speed, 0.01)

    try:
        for idx, row in enumerate(rows, start=1):
            if not sent_meta:
                publish("beam_meta", to_dict(make_beam_meta()))
                publish("radar_meta", to_dict(make_radar_meta()))
                publish("vision_meta", to_dict(make_vision_meta()))
                sent_meta = True

            publish("beam_frame", to_dict(row_to_beam_frame(row, dataroot)))

            radar_frame, _cube = row_to_radar_tensor(row, dataroot)
            publish("radar_tensor", to_dict(radar_frame))

            publish("vision_frame", to_dict(row_to_vision_frame(row)))

            bs_geo, veh_geo = row_to_geoposes(row, dataroot)
            publish("unit1_geo", to_dict(bs_geo))
            publish("unit2_geo", to_dict(veh_geo))

            # The sweep itself goes as blob chunks with a LidarFrame naming
            # the blob. The old payload inlined the point array under a
            # `points` key LidarFrame does not have, so it never reached a
            # consumer as anything typed.
            lidar_points = row_to_lidar_points(row, dataroot)
            frame, chunks = _lidar_frame_and_blob(row, lidar_points)
            publish("lidar_frame", to_dict(frame))
            for chunk in chunks:
                publish("blob", chunk)

            publish("detection2d", to_dict(row_to_detection2d(row, dataroot)))

            print(f"[deepsense-publisher] frame_seq={row['index']} seq={row['seq_index']}", file=sys.stderr)
            if args.max_samples > 0 and idx >= args.max_samples:
                break
            time.sleep(delay)
    finally:
        pass
    return 0


def _lidar_frame_and_blob(row, points):
    """A LidarFrame plus the chunks carrying its sweep."""
    from deepsense_to_spatialdds import _frame_header, _frame_quality
    from sensor_types import BlobRef, LidarFrame

    index = int(row["index"])
    stamp = {"sec": index // 10, "nanosec": (index % 10) * 100_000_000}
    raw = points.astype("float32").tobytes()
    blob_id = f"unit1_lidar_{index}"
    frame = LidarFrame(
        stream_id="unit1_lidar",
        frame_seq=index,
        hdr=_frame_header("unit1_lidar", index, _time(stamp),
                          [BlobRef(**blob.blob_ref(blob_id, "lidar", raw))]),
        encoding="BIN_INTERLEAVED",
        codec="CODEC_NONE",
        layout="XYZ_I",
        has_per_point_timestamps=False,
        has_average_range_m=False,
        average_range_m=0.0,
        has_percent_valid=False,
        percent_valid=0.0,
        has_quality=False,
        quality=_frame_quality(),
    )
    return frame, list(blob.chunk(blob_id, raw))


def _time(stamp):
    from sensor_types import Time

    return Time(sec=stamp["sec"], nanosec=stamp["nanosec"])


if __name__ == "__main__":
    raise SystemExit(main())
