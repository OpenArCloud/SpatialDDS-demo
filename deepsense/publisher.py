#!/usr/bin/env python3
"""Publish DeepSense Scenario 9 data over SpatialDDS envelope DDS."""

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
from nuscenes.dds_envelope_transport import EnvelopeTransport


def publish_json(transport: EnvelopeTransport, topic: str, msg_type: str, payload: dict) -> None:
    transport.publish(topic, msg_type, json.dumps(payload), str(payload.get("frame_seq", "")))


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
    transport = EnvelopeTransport(lambda _env: None, args.domain, "deepsense-publisher")
    transport.start()
    sent_meta = False
    delay = 0.1 / max(args.speed, 0.01)

    try:
        for idx, row in enumerate(rows, start=1):
            if not sent_meta:
                publish_json(transport, "spatialdds/deepsense/rf_beam/unit1_60ghz/meta/v1", "DEEPSENSE_RF_BEAM_META", to_dict(make_beam_meta()))
                publish_json(transport, "spatialdds/deepsense/rad/unit1_radar/meta/v1", "DEEPSENSE_RAD_TENSOR_META", to_dict(make_radar_meta()))
                publish_json(transport, "spatialdds/deepsense/vision/unit1_cam/meta/v1", "DEEPSENSE_VISION_META", to_dict(make_vision_meta()))
                sent_meta = True

            beam = row_to_beam_frame(row, dataroot)
            publish_json(transport, "spatialdds/deepsense/rf_beam/unit1_60ghz/frame/v1", "DEEPSENSE_RF_BEAM_FRAME", to_dict(beam))

            radar_frame, _cube = row_to_radar_tensor(row, dataroot)
            publish_json(transport, "spatialdds/deepsense/rad/unit1_radar/tensor/v1", "DEEPSENSE_RAD_TENSOR_FRAME", to_dict(radar_frame))

            vision = row_to_vision_frame(row)
            publish_json(transport, "spatialdds/deepsense/vision/unit1_cam/frame/v1", "DEEPSENSE_VISION_FRAME", to_dict(vision))

            bs_geo, veh_geo = row_to_geoposes(row, dataroot)
            publish_json(transport, "spatialdds/deepsense/geo/unit1/pose/v1", "DEEPSENSE_UNIT1_GEOPOSE", to_dict(bs_geo))
            publish_json(transport, "spatialdds/deepsense/geo/unit2/pose/v1", "DEEPSENSE_UNIT2_GEOPOSE", to_dict(veh_geo))

            lidar_points = row_to_lidar_points(row, dataroot)
            publish_json(
                transport,
                "spatialdds/deepsense/lidar/unit1_lidar/frame/v1",
                "DEEPSENSE_LIDAR2D_FRAME",
                {"frame_seq": int(row["index"]), "stamp": {"sec": int(row["index"]) // 10, "nanosec": (int(row["index"]) % 10) * 100_000_000}, "points": lidar_points.tolist()},
            )

            det2d = row_to_detection2d(row, dataroot)
            publish_json(transport, "spatialdds/deepsense/semantics/det2d/v1", "DEEPSENSE_DET2D_SET", to_dict(det2d))

            print(f"[deepsense-publisher] frame_seq={row['index']} seq={row['seq_index']}", file=sys.stderr)
            if args.max_samples > 0 and idx >= args.max_samples:
                break
            time.sleep(delay)
    finally:
        transport.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
