#!/usr/bin/env python3
"""Map DeepSense Scenario 9 rows to SpatialDDS-like payloads."""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import scipy.io

from deepsense_types import (
    BBox2D, Detection2D, Detection2DSet, RadTensorFrame, RadTensorMeta,
    RfBeamFrame, RfBeamMeta,
)
from sensor_types import (
    BlobRef, CamIntrinsics, FrameHeader, FrameQuality, FrameRef, GeoPose,
    PoseSE3, QuaternionXYZW, StreamMeta, Time, Vec3, VisionFrame, VisionMeta,
)
from spatialdds_idl.spatial.core import CovMatrix
from spatialdds_idl.spatial.sensing.common import Axis, AxisSpec, Linspace

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
    return PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0])


def _frame_ref(uuid: str, fqn: str) -> FrameRef:
    return FrameRef(uuid=uuid, fqn=fqn, has_coord_convention=True,
                    coord_convention="ENU")


def _stream_meta(stream_id: str, uuid: str, fqn: str,
                 rate_hz: float = 10.0) -> StreamMeta:
    return StreamMeta(
        stream_id=stream_id,
        frame_ref=_frame_ref(uuid, fqn),
        T_bus_sensor=_identity_pose(),
        nominal_rate_hz=rate_hz,
        schema_version="spatial.sensing.common/1.7",
    )


def _frame_quality() -> FrameQuality:
    return FrameQuality(has_snr_db=False, snr_db=0.0, percent_valid=100.0,
                        health="OK", note="")


def _frame_header(stream_id: str, frame_seq: int, stamp: Time,
                  blobs) -> FrameHeader:
    """A complete FrameHeader — `sensor_pose` is presence-flagged, not absent."""
    return FrameHeader(
        stream_id=stream_id, frame_seq=frame_seq, t_start=stamp, t_end=stamp,
        has_sensor_pose=False, sensor_pose=_identity_pose(), blobs=list(blobs),
    )


def _axis(name: str, unit: str, count: int) -> Axis:
    """One tensor axis, described as a linspace over its bin indices."""
    return Axis(name=name, unit=unit,
                spec=AxisSpec(lin=Linspace(start=0.0, step=1.0, count=count)))


def make_beam_meta() -> RfBeamMeta:
    return RfBeamMeta(
        stream_id="unit1_60ghz",
        base=_stream_meta("unit1_60ghz", "unit1-60ghz", "unit1/phased_array"),
        center_freq_ghz=60.0,
        has_bandwidth=False,
        bandwidth_ghz=0.0,
        n_elements=16,
        n_beams=64,
        fov_az_deg=90.0,
        # A single azimuth-only array: no elevation sweep, no MIMO.
        has_fov_el=False,
        fov_el_deg=0.0,
        has_array_index=False,
        array_index=0,
        array_label="unit1",
        codebook_type="DFT-64",
        has_mimo_config=False,
        n_tx=1,
        n_rx=1,
        power_unit="DBM",
        schema_version="spatial.sensing.rf_beam/1.7",
    )


def make_radar_meta() -> RadTensorMeta:
    return RadTensorMeta(
        stream_id="unit1_radar",
        base=_stream_meta("unit1_radar", "unit1-radar", "unit1/fmcw_radar"),
        sensor_type="MEDIUM_RANGE",
        layout="CH_FAST_SLOW",
        axes=[_axis("channel", "", 4), _axis("fast_time", "s", 256),
              _axis("slow_time", "s", 128)],
        voxel_type="CF32",
        physical_meaning="raw ADC, pre-FFT",
        has_antenna_config=True,
        num_tx=1,
        num_rx=4,
        num_virtual_channels=4,
        has_waveform_params=True,
        bandwidth_hz=750e6,
        center_freq_hz=78.5e9,
        chirp_duration_s=0.0,
        samples_per_chirp=256,
        chirps_per_frame=128,
        payload_kind="DENSE_TILES",
        codec="CODEC_NONE",
        has_quant_scale=False,
        quant_scale=0.0,
        tile_size=0,
        schema_version="spatial.sensing.rad/1.7",
    )


def make_vision_meta() -> VisionMeta:
    return VisionMeta(
        stream_id="unit1_cam",
        base=_stream_meta("unit1_cam", "unit1-cam", "unit1/camera"),
        # `K` — the camera matrix, named as it is in every calibration
        # convention. The old code called it `cam`.
        K=CamIntrinsics(
            model="PINHOLE",
            width=IMAGE_WIDTH,
            height=IMAGE_HEIGHT,
            fx=800.0,
            fy=800.0,
            cx=IMAGE_WIDTH / 2.0,
            cy=IMAGE_HEIGHT / 2.0,
            dist="NONE",
            dist_params=[],
            shutter_us=0.0,
            readout_us=0.0,
            pix="RGB8",
            color="SRGB",
            calib_version="deepsense-s9",
        ),
        role="FRONT",
        rig_id="unit1",
        codec="JPEG",
        pix="RGB8",
        color="SRGB",
        schema_version="spatial.sensing.vision/1.7",
    )


def row_to_beam_frame(row: Dict[str, str], dataroot: Path) -> RfBeamFrame:
    path = dataroot / row["unit1_pwr_60ghz"].lstrip("./")
    power = np.loadtxt(path, dtype=np.float32).reshape(-1)
    best_idx = int(np.argmax(power))
    best_power = float(np.max(power))
    stamp = _time_from_row(row)
    return RfBeamFrame(
        stream_id="unit1_60ghz",
        frame_seq=int(row["index"]),
        hdr=_frame_header("unit1_60ghz", int(row["index"]), stamp, []),
        sweep_type="EXHAUSTIVE",
        power=power.tolist(),
        # An exhaustive sweep walks the codebook in order, so the beam index
        # is the position in `power`; no explicit index list needed.
        beam_indices=[],
        has_best_beam=True,
        best_beam_idx=best_idx,
        best_beam_power=best_power,
        has_blockage_state=True,
        is_blocked=best_power < BLOCKAGE_THRESHOLD,
        blockage_confidence=0.8,
        has_snr_db=False,
        snr_db=0.0,
        has_quality=False,
        quality=_frame_quality(),
    )


def row_to_radar_tensor(row: Dict[str, str], dataroot: Path) -> Tuple[RadTensorFrame, np.ndarray]:
    path = dataroot / row["unit1_radar"].lstrip("./")
    cube = np.asarray(scipy.io.loadmat(path)["data"], dtype=np.complex64)
    stamp = _time_from_row(row)
    # The cube's shape and dtype are in RadTensorMeta, which is latched on
    # its own topic — the frame says which blob holds the samples, not what
    # shape they are. The old payload carried `shape` and `dtype` fields
    # RadTensorFrame does not have.
    frame = RadTensorFrame(
        stream_id="unit1_radar",
        frame_seq=int(row["index"]),
        hdr=_frame_header("unit1_radar", int(row["index"]), stamp, [
            BlobRef(blob_id=row["unit1_radar"].lstrip("./"),
                    role="radar_cube", checksum=""),
        ]),
        payload_kind="DENSE_TILES",
        codec="CODEC_NONE",
        voxel_type_after_decode="CF32",
        has_quant_scale=False,
        quant_scale=0.0,
        quality=_frame_quality(),
        proc_chain="deepsense-scenario9",
    )
    return frame, cube


def row_to_vision_frame(row: Dict[str, str]) -> VisionFrame:
    stamp = _time_from_row(row)
    return VisionFrame(
        stream_id="unit1_cam",
        frame_seq=int(row["index"]),
        hdr=_frame_header("unit1_cam", int(row["index"]), stamp, [
            BlobRef(blob_id=row["unit1_rgb"].lstrip("./"), role="image",
                    checksum=""),
        ]),
        codec="JPEG",
        pix="RGB8",
        color="SRGB",
        has_line_readout_us=False,
        line_readout_us=0.0,
        rectified=False,
        is_key_frame=True,
        quality=_frame_quality(),
    )


def _load_latlon(path: Path) -> Tuple[float, float]:
    values = np.loadtxt(path, dtype=np.float64).reshape(-1)
    return float(values[0]), float(values[1])


def row_to_geoposes(row: Dict[str, str], dataroot: Path) -> Tuple[GeoPose, GeoPose]:
    bs_lat, bs_lon = _load_latlon(dataroot / row["unit1_loc"].lstrip("./"))
    veh_lat, veh_lon = _load_latlon(dataroot / row["unit2_loc_cal"].lstrip("./"))
    stamp = _time_from_row(row)
    bs = GeoPose(lat_deg=bs_lat, lon_deg=bs_lon, alt_m=0.0,
                 q=[0.0, 0.0, 0.0, 1.0], stamp=stamp, cov=CovMatrix(none=0))
    veh = GeoPose(lat_deg=veh_lat, lon_deg=veh_lon, alt_m=0.0,
                  q=[0.0, 0.0, 0.0, 1.0], stamp=stamp, cov=CovMatrix(none=0))
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
    return Detection2DSet(
        stream_id="unit1_cam",
        frame_seq=int(row["index"]),
        dets=detections,
        stamp=_time_from_row(row),
        source_id="deepsense",
    )


def iter_sequence(rows: Iterable[Dict[str, str]], sequence: int) -> Iterable[Dict[str, str]]:
    for row in rows:
        if int(row["seq_index"]) == sequence:
            yield row
