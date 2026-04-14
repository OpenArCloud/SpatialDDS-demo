"""Cross-operator track fusion.

Ported from the SpatialDDS v5 PoC algorithm:
  * Nearest-neighbour association with position + velocity gating.
  * Confirmed/tentative/lost lifecycle via consecutive hit/miss counters.
  * Uncertainty-weighted position & velocity fusion (1 / sigma^2).
  * Independent-confirmation confidence boost: 1 - prod(1 - conf_i).
  * Provenance accumulation (source_operators, source_modalities).

This module has no DDS, nuScenes, or I/O dependencies — it operates on
plain dataclasses so the algorithm is testable in isolation. The DDS
adapter lives in ``fusion_service.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class Position:
    x: float
    y: float
    z: float


@dataclass
class Velocity:
    vx: float
    vy: float
    vz: float


@dataclass
class Detection3D:
    """Single observation from one operator's fleet."""
    position: Position
    velocity: Velocity
    source_operator: str
    source_modality: str
    object_class: str
    confidence: float
    position_uncertainty: float


@dataclass
class FusedTrack:
    """Cross-source correlated track published by the platform fuser."""
    track_id: str
    position: Position
    velocity: Velocity
    position_uncertainty: float
    object_class: str
    confidence: float
    source_operators: List[str]
    source_modalities: List[str]
    source_count: int
    timestamp: float
    track_age: float


@dataclass
class _FusionTrack:
    track_id: str
    position: Position
    velocity: Velocity
    position_uncertainty: float
    source_operators: Set[str] = field(default_factory=set)
    source_modalities: Set[str] = field(default_factory=set)
    object_class: str = "unknown"
    confidence: float = 0.0
    first_seen_t: float = 0.0
    last_seen_t: float = 0.0
    consecutive_hits: int = 0
    consecutive_misses: int = 0
    status: str = "tentative"
    _hit_this_tick: bool = False


def _dist_3d(a: Position, b: Position) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _vel_dist(a: Velocity, b: Velocity) -> float:
    return math.sqrt((a.vx - b.vx) ** 2 + (a.vy - b.vy) ** 2 + (a.vz - b.vz) ** 2)


class TrackFusion:
    """Fuses Detection3D streams from all operators into unified FusedTracks.

    Usage:
        fuser = TrackFusion()
        for det in detections_this_tick:
            fuser.on_detection(det)
        tracks = fuser.tick(t=now)
    """

    def __init__(
        self,
        gate_distance_m: float = 5.0,
        gate_velocity_mps: float = 5.0,
        confirm_frames: int = 2,
        lost_frames: int = 6,
    ):
        self._gate_distance_m = gate_distance_m
        self._gate_velocity_mps = gate_velocity_mps
        self._confirm_frames = confirm_frames
        self._lost_frames = lost_frames
        self._pending: List[Detection3D] = []
        self._tracks: Dict[str, _FusionTrack] = {}
        self._next_id: int = 0

    def on_detection(self, det: Detection3D) -> None:
        self._pending.append(det)

    def tick(self, t: float) -> List[FusedTrack]:
        for trk in self._tracks.values():
            trk._hit_this_tick = False

        unmatched: List[Detection3D] = []
        for det in self._pending:
            best_id, best_dist = None, float("inf")
            for tid, trk in self._tracks.items():
                d = _dist_3d(det.position, trk.position)
                vd = _vel_dist(det.velocity, trk.velocity)
                if d <= self._gate_distance_m and vd <= self._gate_velocity_mps and d < best_dist:
                    best_id, best_dist = tid, d
            if best_id is not None:
                self._update_track(self._tracks[best_id], det, t)
            else:
                unmatched.append(det)

        # Same-tick merging for unmatched detections before creating new tracks.
        new_ids: List[str] = []
        for det in unmatched:
            merged = False
            for ntid in new_ids:
                trk = self._tracks[ntid]
                if (_dist_3d(det.position, trk.position) <= self._gate_distance_m
                        and _vel_dist(det.velocity, trk.velocity) <= self._gate_velocity_mps):
                    self._update_track(trk, det, t)
                    merged = True
                    break
            if not merged:
                tid = f"fused-{self._next_id}"
                self._next_id += 1
                self._tracks[tid] = _FusionTrack(
                    track_id=tid,
                    position=det.position,
                    velocity=det.velocity,
                    position_uncertainty=det.position_uncertainty,
                    source_operators={det.source_operator},
                    source_modalities={det.source_modality},
                    object_class=det.object_class,
                    confidence=det.confidence,
                    first_seen_t=t,
                    last_seen_t=t,
                    consecutive_hits=1,
                    consecutive_misses=0,
                    status="tentative",
                    _hit_this_tick=True,
                )
                new_ids.append(tid)

        remove: List[str] = []
        for tid, trk in self._tracks.items():
            if not trk._hit_this_tick:
                trk.consecutive_misses += 1
                trk.consecutive_hits = 0
            if trk.status == "tentative" and trk.consecutive_hits >= self._confirm_frames:
                trk.status = "confirmed"
            if trk.consecutive_misses >= self._lost_frames:
                remove.append(tid)
        for tid in remove:
            del self._tracks[tid]

        self._pending.clear()

        return [
            FusedTrack(
                track_id=trk.track_id,
                position=trk.position,
                velocity=trk.velocity,
                position_uncertainty=trk.position_uncertainty,
                object_class=trk.object_class,
                confidence=trk.confidence,
                source_operators=sorted(trk.source_operators),
                source_modalities=sorted(trk.source_modalities),
                source_count=len(trk.source_operators),
                timestamp=trk.last_seen_t,
                track_age=trk.last_seen_t - trk.first_seen_t,
            )
            for trk in self._tracks.values()
            if trk.status == "confirmed"
        ]

    def _update_track(self, trk: _FusionTrack, det: Detection3D, t: float) -> None:
        w_trk = 1.0 / (trk.position_uncertainty ** 2) if trk.position_uncertainty > 0 else 1.0
        w_det = 1.0 / (det.position_uncertainty ** 2) if det.position_uncertainty > 0 else 1.0
        w = w_trk + w_det

        trk.position = Position(
            x=(w_trk * trk.position.x + w_det * det.position.x) / w,
            y=(w_trk * trk.position.y + w_det * det.position.y) / w,
            z=(w_trk * trk.position.z + w_det * det.position.z) / w,
        )
        trk.velocity = Velocity(
            vx=(w_trk * trk.velocity.vx + w_det * det.velocity.vx) / w,
            vy=(w_trk * trk.velocity.vy + w_det * det.velocity.vy) / w,
            vz=(w_trk * trk.velocity.vz + w_det * det.velocity.vz) / w,
        )
        trk.position_uncertainty = 1.0 / math.sqrt(w)

        trk.source_operators.add(det.source_operator)
        trk.source_modalities.add(det.source_modality)

        if det.object_class != "unknown":
            trk.object_class = det.object_class

        trk.confidence = 1.0 - (1.0 - trk.confidence) * (1.0 - det.confidence)

        trk.last_seen_t = t
        trk.consecutive_hits += 1
        trk.consecutive_misses = 0
        trk._hit_this_tick = True


def coverage_metrics(tracks: List[FusedTrack]) -> Dict[str, float]:
    """Compute per-tick metrics for the coverage dashboard.

    Returns a plain dict suitable for JSON publication on
    ``spatialdds/platform/fusion/coverage/v1``.

    multi_source_pct is the fraction of confirmed tracks seen by 2+
    independent operators — the headline "no single source could build
    this alone" metric.
    """
    n = len(tracks)
    multi = sum(1 for t in tracks if t.source_count >= 2)
    per_op: Dict[str, int] = {}
    for t in tracks:
        for op in t.source_operators:
            per_op[op] = per_op.get(op, 0) + 1
    best_single = max(per_op.values(), default=0)
    return {
        "track_count": n,
        "multi_source_count": multi,
        "multi_source_pct": (multi / n) if n else 0.0,
        "best_single_operator_count": best_single,
        "coverage_improvement": (n / best_single) if best_single else 0.0,
        "per_operator_track_count": per_op,
    }
