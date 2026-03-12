#!/usr/bin/env python3
"""DeepSense-specific SpatialDDS-like dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from spatialdds_types import FrameHeader, StreamMeta, Time


@dataclass
class TensorAxis:
    name: str
    size: int


@dataclass
class RfBeamMeta:
    stream_id: str
    base: StreamMeta
    center_freq_ghz: float
    n_elements: int
    n_beams: int
    fov_az_deg: float
    codebook_type: str
    power_unit: str
    schema_version: str


@dataclass
class RfBeamFrame:
    stream_id: str
    frame_seq: int
    sweep_type: str
    power: List[float]
    has_best_beam: bool
    best_beam_idx: int
    best_beam_power: float
    has_blockage_state: bool
    is_blocked: bool
    blockage_confidence: float
    has_quality: bool
    schema_version: str
    stamp: Time


@dataclass
class RadTensorMeta:
    stream_id: str
    base: StreamMeta
    axes: List[TensorAxis]
    voxel_type: str
    layout: str
    center_freq_hz: float
    bandwidth_hz: float
    samples_per_chirp: int
    chirps_per_frame: int
    num_tx: int
    num_rx: int
    schema_version: str


@dataclass
class RadTensorFrame:
    stream_id: str
    hdr: FrameHeader
    shape: List[int]
    dtype: str
    schema_version: str


@dataclass
class BBox2D:
    x: float
    y: float
    w: float
    h: float


@dataclass
class Detection2D:
    det_id: str
    bbox: BBox2D
    class_id: str
    score: float


@dataclass
class Detection2DSet:
    frame_seq: int
    stamp: Time
    detections: List[Detection2D] = field(default_factory=list)
