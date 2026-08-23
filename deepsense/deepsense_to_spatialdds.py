#!/usr/bin/env python3
"""Map DeepSense Scenario 9 rows to SpatialDDS-like payloads."""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import scipy.io

from deepsense_types import BBox2D, Detection2D, Detection2DSet, RadTensorFrame, RadTensorMeta, RfBeamFrame, RfBeamMeta, TensorAxis
from sensor_types import BlobRef, CamIntrinsics, FrameHeader, FrameRef, GeoPose, PoseSE3, QuaternionXYZW, StreamMeta, Time, Vec3, VisionFrame, VisionMeta

BLOCKAGE_THRESHOLD = 0.12
IMAGE_WIDTH = 960
IMAGE_HEIGHT = 540


def to_dict(obj: object) -> Dict:
    return asdict(obj)


def load_rows(index_csv: Path) -> List[Dict[str, str]]:
    with index_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [row for row in reader]


def _time_from_row(row: Dict[str, str]) -> Time:
    idx = int(row["index"])
    return Time(sec=idx // 10, nanosec=(idx % 10) * 100_000_000)


def _identity_pose() -> PoseSE3:
    return PoseSE3(t=Vec3(0.0, 0.0, 0.0), q=QuaternionXYZW(0.0, 0.0, 0.0, 1.0))


def make_beam_meta() -> RfBeamMeta:
    return RfBeamMeta(
        stream_id="unit1_60ghz",
        base=StreamMeta(
            stream_id="unit1_60ghz",
            frame_ref=FrameRef(uuid="unit1-60ghz", fqn="unit1/phased_array"),
            T_bus_sensor=_identity_pose(),
            nominal_rate_hz=10.0,
        ),
        center_freq_ghz=60.0,
        n_elements=16,
        n_beams=64,
        fov_az_deg=90.0,
        codebook_type="DFT-64",
        power_unit="DBM",
        schema_version="spatial.sensing.rf_beam/1.7",
    )


def make_radar_meta() -> RadTensorMeta:
    return RadTensorMeta(
        stream_id="unit1_radar",
        base=StreamMeta(
            stream_id="unit1_radar",
            frame_ref=FrameRef(uuid="unit1-radar", fqn="unit1/fmcw_radar"),
            T_bus_sensor=_identity_pose(),
            nominal_rate_hz=10.0,
        ),
        axes=[TensorAxis("rx", 4), TensorAxis("range_bin", 256), TensorAxis("doppler_bin", 128)],
        voxel_type="CF32",
        layout="CH_FAST_SLOW",
        center_freq_hz=78.5e9,
        bandwidth_hz=750e6,
        samples_per_chirp=256,
        chirps_per_frame=128,
        num_tx=1,
        num_rx=4,
        schema_version="spatial.sensing.rad/1.7",
    )


def make_vision_meta() -> VisionMeta:
    return VisionMeta(
        stream_id="unit1_cam",
        base=StreamMeta(
            stream_id="unit1_cam",
            frame_ref=FrameRef(uuid="unit1-cam", fqn="unit1/camera"),
            T_bus_sensor=_identity_pose(),
            nominal_rate_hz=10.0,
        ),
        pix="RGB8",
        codec="JPEG",
        cam=CamIntrinsics(
            model="PINHOLE",
            fx=800.0,
            fy=800.0,
            cx=IMAGE_WIDTH / 2.0,
            cy=IMAGE_HEIGHT / 2.0,
            width=IMAGE_WIDTH,
            height=IMAGE_HEIGHT,
        ),
        rig_id="unit1",
        schema_version="spatial.sensing.vision/1.7",
    )


def row_to_beam_frame(row: Dict[str, str], dataroot: Path) -> RfBeamFrame:
    path = dataroot / row["unit1_pwr_60ghz"].lstrip("./")
    power = np.loadtxt(path, dtype=np.float32).reshape(-1)
    best_idx = int(np.argmax(power))
    best_power = float(np.max(power))
    return RfBeamFrame(
        stream_id="unit1_60ghz",
        frame_seq=int(row["index"]),
        sweep_type="EXHAUSTIVE",
        power=power.tolist(),
        has_best_beam=True,
        best_beam_idx=best_idx,
        best_beam_power=best_power,
        has_blockage_state=True,
        is_blocked=best_power < BLOCKAGE_THRESHOLD,
        blockage_confidence=0.8,
        has_quality=False,
        schema_version="spatial.sensing.rf_beam/1.7",
        stamp=_time_from_row(row),
    )


def row_to_radar_tensor(row: Dict[str, str], dataroot: Path) -> Tuple[RadTensorFrame, np.ndarray]:
    path = dataroot / row["unit1_radar"].lstrip("./")
    cube = np.asarray(scipy.io.loadmat(path)["data"], dtype=np.complex64)
    stamp = _time_from_row(row)
    frame = RadTensorFrame(
        stream_id="unit1_radar",
        hdr=FrameHeader(
            stream_id="unit1_radar",
            frame_seq=int(row["index"]),
            t_start=stamp,
            t_end=stamp,
            blobs=[BlobRef(blob_id=row["unit1_radar"].lstrip("./"), role="radar_cube")],
        ),
        shape=list(cube.shape),
        dtype="complex64",
        schema_version="spatial.sensing.rad/1.7",
    )
    return frame, cube


def row_to_vision_frame(row: Dict[str, str]) -> VisionFrame:
    stamp = _time_from_row(row)
    return VisionFrame(
        stream_id="unit1_cam",
        hdr=FrameHeader(
            stream_id="unit1_cam",
            frame_seq=int(row["index"]),
            t_start=stamp,
            t_end=stamp,
            blobs=[BlobRef(blob_id=row["unit1_rgb"].lstrip("./"), role="image")],
        ),
        schema_version="spatial.sensing.vision/1.7",
    )


def _load_latlon(path: Path) -> Tuple[float, float]:
    values = np.loadtxt(path, dtype=np.float64).reshape(-1)
    return float(values[0]), float(values[1])


def row_to_geoposes(row: Dict[str, str], dataroot: Path) -> Tuple[GeoPose, GeoPose]:
    bs_lat, bs_lon = _load_latlon(dataroot / row["unit1_loc"].lstrip("./"))
    veh_lat, veh_lon = _load_latlon(dataroot / row["unit2_loc_cal"].lstrip("./"))
    stamp = _time_from_row(row)
    bs = GeoPose(bs_lat, bs_lon, 0.0, [0.0, 0.0, 0.0, 1.0], stamp)
    veh = GeoPose(veh_lat, veh_lon, 0.0, [0.0, 0.0, 0.0, 1.0], stamp)
    return bs, veh


def row_to_lidar_points(row: Dict[str, str], dataroot: Path) -> np.ndarray:
    path = dataroot / row["unit1_lidar"].lstrip("./")
    return np.asarray(scipy.io.loadmat(path)["data"], dtype=np.float32)


def row_to_detection2d(row: Dict[str, str], dataroot: Path) -> Detection2DSet:
    image_name = Path(row["unit1_rgb"]).stem
    label_path = dataroot / "resources" / "annotations" / "bbox" / f"{image_name}.txt"
    detections: List[Detection2D] = []
    if label_path.exists():
        for i, line in enumerate(label_path.read_text(encoding="utf-8", errors="ignore").splitlines()):
            parts = line.split()
            if len(parts) != 5:
                continue
            cls, x_center, y_center, width, height = map(float, parts)
            w_px = width * IMAGE_WIDTH
            h_px = height * IMAGE_HEIGHT
            x_px = (x_center * IMAGE_WIDTH) - w_px / 2.0
            y_px = (y_center * IMAGE_HEIGHT) - h_px / 2.0
            class_id = "Tx" if int(cls) == 0 else "Distractor"
            detections.append(
                Detection2D(
                    det_id=f"{image_name}_{i}",
                    bbox=BBox2D(x=x_px, y=y_px, w=w_px, h=h_px),
                    class_id=class_id,
                    score=1.0,
                )
            )
    return Detection2DSet(frame_seq=int(row["index"]), stamp=_time_from_row(row), detections=detections)


def iter_sequence(rows: Iterable[Dict[str, str]], sequence: int) -> Iterable[Dict[str, str]]:
    for row in rows:
        if int(row["seq_index"]) == sequence:
            yield row
