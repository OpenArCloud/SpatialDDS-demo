#!/usr/bin/env python3
"""Infrastructure (base station) publisher.

Replays DeepSense Scenario 9 data (60 GHz phased-array beams, FMCW
radar tensor, RGB camera, LiDAR) under the ``spatialdds/infrastructure/``
namespace and synthesizes a Detection3D from the Tx-vehicle GPS track so
the platform fuser has a BS-sourced detection to correlate with AV
operator observations.

Fixed operator name: ``infrastructure``. Topics:

  spatialdds/infrastructure/rf_beam/unit1_60ghz/frame/v1
  spatialdds/infrastructure/rad/unit1_radar/tensor/v1
  spatialdds/infrastructure/vision/unit1_cam/frame/v1
  spatialdds/infrastructure/lidar/unit1_lidar/frame/v1
  spatialdds/infrastructure/geo/unit1/pose/v1
  spatialdds/infrastructure/geo/unit2/pose/v1
  spatialdds/infrastructure/sensing/detection3d/v1
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
NUSCENES_DIR = REPO_ROOT / "nuscenes"
DEEPSENSE_DIR = REPO_ROOT / "deepsense"
# Reverse order of insert(0, ...) so NUSCENES_DIR lands first in sys.path.
# This makes ``import spatialdds_types`` resolve to ``nuscenes/spatialdds_types.py``
# (the real dataclasses) rather than ``deepsense/spatialdds_types.py``
# (a thin re-export shim that depends on a package-qualified import).
for _p in (DEEPSENSE_DIR, REPO_ROOT, NUSCENES_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

SOURCE_OPERATOR = "infrastructure"
SCENE_FRAME_FQN = "scene/intersection"
TOPIC_PREFIX = f"spatialdds/{SOURCE_OPERATOR}"
EARTH_M_PER_DEG_LAT = 111_000.0


def gps_to_enu(
    bs_lat: float, bs_lon: float,
    veh_lat: float, veh_lon: float,
) -> Tuple[float, float]:
    """Flat-earth conversion of (veh - bs) GPS to a local ENU (east, north) in meters.

    Uses cos(bs_lat) for the longitudinal scale. Good to ~0.1% over hundreds
    of meters at mid-latitudes — ample for a conceptual demo.
    """
    dlat = veh_lat - bs_lat
    dlon = veh_lon - bs_lon
    east = dlon * EARTH_M_PER_DEG_LAT * math.cos(math.radians(bs_lat))
    north = dlat * EARTH_M_PER_DEG_LAT
    return east, north


def make_detection3d_payload(
    frame_seq: int,
    stamp: Dict[str, int],
    east: float, north: float, up: float,
    velocity: Tuple[float, float, float],
    class_id: str = "vehicle",
    score: float = 0.9,
) -> Dict:
    """
    One ``OperatorDetectionSet`` per frame — the Tx vehicle's GPS-derived
    position, observed from the base station.

    Built with the same helpers the synthetic publisher uses, so the two
    infrastructure sources cannot drift into different payload shapes; that
    drift is exactly what the round-trip test used to catch after the fact.
    """
    from multi_operator_fusion.spatialdds_types import (
        make_detection, make_detection_set, make_detection_with_velocity,
    )

    timestamp_s = stamp["sec"] + stamp["nanosec"] / 1e9
    det = make_detection(
        det_id=f"infra-{frame_seq}", class_id=class_id, score=float(score),
        center=(float(east), float(north), float(up)),
        size=(2.0, 1.6, 4.5),                       # generic car bbox
        q=(0.0, 0.0, 0.0, 1.0),
        frame_ref_fqn=SCENE_FRAME_FQN, timestamp_s=timestamp_s,
        source_id=SOURCE_OPERATOR,
    )
    return make_detection_set(
        set_id=f"infra-{frame_seq}", source_operator=SOURCE_OPERATOR,
        frame_ref_fqn=SCENE_FRAME_FQN,
        dets=[make_detection_with_velocity(det, velocity=velocity,
                                           source_modality="radar")],
        frame_seq=frame_seq, timestamp_s=timestamp_s,
    )


def _velocity_from_history(
    prev: Optional[Tuple[float, float, int]],
    curr: Tuple[float, float, int],
) -> Tuple[float, float, float]:
    """Finite-difference velocity in m/s given two (east, north, frame_seq) samples.

    DeepSense Scenario 9 runs at 10 Hz (stamp = idx/10s), so dt = (Δseq)/10.
    """
    if prev is None:
        return (0.0, 0.0, 0.0)
    dx = curr[0] - prev[0]
    dy = curr[1] - prev[1]
    dt = max(1, curr[2] - prev[2]) / 10.0
    return (dx / dt, dy / dt, 0.0)


def _apply_offset(det: Dict, offset: Tuple[float, float, float]) -> None:
    """Shift one DetectionWithVelocity's centre. Vec3 is an array, not {x,y,z}."""
    centre = det["detection"]["center"]
    det["detection"]["center"] = [c + o for c, o in zip(centre, offset)]


def _stamp_from_index(idx: int) -> Dict[str, int]:
    return {"sec": idx // 10, "nanosec": (idx % 10) * 100_000_000}


def run(args: argparse.Namespace) -> int:
    from dds_envelope_transport import EnvelopeTransport  # noqa: E402
    from spatialdds_types import to_dict  # noqa: E402
    from deepsense.deepsense_to_spatialdds import (  # noqa: E402
        iter_sequence, load_rows,
        make_beam_meta, make_radar_meta, make_vision_meta,
        row_to_beam_frame, row_to_geoposes, row_to_lidar_points,
        row_to_radar_tensor, row_to_vision_frame,
    )

    dataroot = Path(args.dataroot)
    rows = list(iter_sequence(load_rows(dataroot / "scenario9.csv"), args.sequence))
    transport = EnvelopeTransport(lambda _env: None, args.domain, "multi-op-infra-publisher")
    transport.start()

    offset = (args.offset_x, args.offset_y, args.offset_z)
    delay = 0.1 / max(args.speed, 0.01)
    sent_meta = False
    prev_enu: Optional[Tuple[float, float, int]] = None

    def publish(topic: str, msg_type: str, payload: Dict) -> None:
        payload.setdefault("source_operator", SOURCE_OPERATOR)
        transport.publish(topic, msg_type, json.dumps(payload),
                          str(payload.get("frame_seq", "")))

    # Track which optional raw streams have already warned, so a missing
    # subset doesn't spam every frame.
    warned: set = set()

    def _try(stream: str, fn):
        """Run fn() and suppress FileNotFoundError/OSError so the pruned-dataset
        case (subset without radar cubes / beam files / lidar) degrades into
        "Detection3D still flows, raw sensor streams skipped"."""
        try:
            return fn()
        except (FileNotFoundError, OSError) as exc:
            if stream not in warned:
                warned.add(stream)
                print(f"[infrastructure] {stream} unavailable — skipping this "
                      f"stream for the rest of the run ({exc})", file=sys.stderr)
            return None

    try:
        for idx, row in enumerate(rows, start=1):
            frame_seq = int(row["index"])
            stamp = _stamp_from_index(frame_seq)

            if not sent_meta:
                publish(f"{TOPIC_PREFIX}/rf_beam/unit1_60ghz/meta/v1",
                        "DEEPSENSE_RF_BEAM_META", to_dict(make_beam_meta()))
                publish(f"{TOPIC_PREFIX}/rad/unit1_radar/meta/v1",
                        "DEEPSENSE_RAD_TENSOR_META", to_dict(make_radar_meta()))
                publish(f"{TOPIC_PREFIX}/vision/unit1_cam/meta/v1",
                        "DEEPSENSE_VISION_META", to_dict(make_vision_meta()))
                sent_meta = True

            beam = _try("rf_beam", lambda: row_to_beam_frame(row, dataroot))
            if beam is not None:
                publish(f"{TOPIC_PREFIX}/rf_beam/unit1_60ghz/frame/v1",
                        "DEEPSENSE_RF_BEAM_FRAME", to_dict(beam))

            radar = _try("rad_tensor", lambda: row_to_radar_tensor(row, dataroot))
            if radar is not None:
                radar_frame, _ = radar
                publish(f"{TOPIC_PREFIX}/rad/unit1_radar/tensor/v1",
                        "DEEPSENSE_RAD_TENSOR_FRAME", to_dict(radar_frame))

            # Vision frame is just metadata + blob_id; subscriber skips the
            # image load if the blob doesn't exist.
            publish(f"{TOPIC_PREFIX}/vision/unit1_cam/frame/v1",
                    "DEEPSENSE_VISION_FRAME", to_dict(row_to_vision_frame(row)))

            lidar_points = _try("lidar", lambda: row_to_lidar_points(row, dataroot))
            if lidar_points is not None:
                publish(f"{TOPIC_PREFIX}/lidar/unit1_lidar/frame/v1",
                        "DEEPSENSE_LIDAR2D_FRAME",
                        {"frame_seq": frame_seq, "stamp": stamp,
                         "points": lidar_points.tolist()})

            bs_geo, veh_geo = row_to_geoposes(row, dataroot)
            publish(f"{TOPIC_PREFIX}/geo/unit1/pose/v1", "DEEPSENSE_UNIT1_GEOPOSE", to_dict(bs_geo))
            publish(f"{TOPIC_PREFIX}/geo/unit2/pose/v1", "DEEPSENSE_UNIT2_GEOPOSE", to_dict(veh_geo))

            east, north = gps_to_enu(bs_geo.lat_deg, bs_geo.lon_deg,
                                     veh_geo.lat_deg, veh_geo.lon_deg)
            velocity = _velocity_from_history(prev_enu, (east, north, frame_seq))
            prev_enu = (east, north, frame_seq)

            det_payload = make_detection3d_payload(
                frame_seq=frame_seq, stamp=stamp,
                east=east, north=north, up=0.0,
                velocity=velocity,
            )
            for det in det_payload["detections"]:
                _apply_offset(det, offset)
            publish(f"{TOPIC_PREFIX}/sensing/detection3d/v1",
                    "INFRA_DET3D_SET", det_payload)

            if not args.quiet:
                print(f"[infrastructure] frame_seq={frame_seq} "
                      f"tx_enu=({east:.1f},{north:.1f}) "
                      f"v=({velocity[0]:.2f},{velocity[1]:.2f})", file=sys.stderr)

            if args.max_samples > 0 and idx >= args.max_samples:
                break
            time.sleep(delay)
    finally:
        transport.stop()
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Infrastructure (base station) SpatialDDS publisher")
    p.add_argument("--dataroot", required=True, help="DeepSense Scenario 9 dataroot")
    p.add_argument("--sequence", type=int, default=1)
    p.add_argument("--domain", type=int, default=1)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--max-samples", type=int, default=0)
    p.add_argument("--offset-x", type=float, default=0.0,
                   help="ENU east offset applied to Detection3D centers (m)")
    p.add_argument("--offset-y", type=float, default=0.0)
    p.add_argument("--offset-z", type=float, default=0.0)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
