#!/usr/bin/env python3
"""Platform-level fusion service.

Subscribes to every operator's Detection3D stream via the shared
SpatialDDS envelope transport, runs :class:`fusion.TrackFusion`, and
publishes FusedTracks plus coverage metrics on platform topics:

    spatialdds/platform/fusion/track/v1       NUSC_FUSED_TRACK_SET
    spatialdds/platform/fusion/coverage/v1    NUSC_FUSION_COVERAGE

Detection3D payloads are recognized by ``msg_type == "NUSC_DET3D_SET"``
with a logical_topic matching ``spatialdds/{operator}/sensing/detection3d/v1``.
Operator provenance is read from the top-level ``source_operator`` field
stamped by the per-operator publisher.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent             # multi_operator_fusion/
REPO_ROOT = _HERE.parent
NUSCENES_DIR = REPO_ROOT / "nuscenes"
# _HERE first so ``from fusion import ...`` resolves to the local module
# even when this file is loaded as ``multi_operator_fusion.fusion_service``
# (which doesn't put _HERE on sys.path automatically).
for p in (_HERE, REPO_ROOT, NUSCENES_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fusion import (  # noqa: E402
    Detection3D,
    Position,
    TrackFusion,
    Velocity,
    coverage_metrics,
)

DET3D_TOPIC_SUFFIX = "sensing/detection3d/v1"
TRACK_TOPIC = "spatialdds/platform/fusion/track/v1"
COVERAGE_TOPIC = "spatialdds/platform/fusion/coverage/v1"
TRACK_MSG_TYPE = "NUSC_FUSED_TRACK_SET"
COVERAGE_MSG_TYPE = "NUSC_FUSION_COVERAGE"


def _infer_modality_from_topic(logical_topic: str) -> str:
    """Best-effort modality tag — operators publish their fused output
    on ``…/sensing/detection3d/v1`` without per-sensor provenance, so we
    just tag the source modality as ``det3d`` for now. Extend if the
    publisher begins splitting per-modality."""
    return "det3d"


def _parse_detection(raw: dict, source_operator: str, modality: str,
                     default_sigma: float) -> Optional[Detection3D]:
    center = raw.get("center")
    if not isinstance(center, dict):
        return None
    velocity = raw.get("velocity") or {}
    vx = float(velocity.get("x", 0.0))
    vy = float(velocity.get("y", 0.0))
    vz = float(velocity.get("z", 0.0))
    if not raw.get("has_velocity", True):
        vx = vy = vz = 0.0
    return Detection3D(
        position=Position(x=float(center["x"]), y=float(center["y"]), z=float(center["z"])),
        velocity=Velocity(vx=vx, vy=vy, vz=vz),
        source_operator=source_operator,
        source_modality=modality,
        object_class=str(raw.get("class_id", "unknown")),
        confidence=float(raw.get("score", 1.0)),
        position_uncertainty=default_sigma,
    )


class FusionService:
    def __init__(self, transport, fuser: TrackFusion, tick_hz: float, default_sigma: float, quiet: bool):
        self._transport = transport
        self._fuser = fuser
        self._dt = 1.0 / max(0.1, tick_hz)
        self._default_sigma = default_sigma
        self._quiet = quiet
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)

    def on_envelope(self, envelope) -> None:
        topic = getattr(envelope, "logical_topic", "") or ""
        if not topic.endswith(DET3D_TOPIC_SUFFIX):
            return
        try:
            payload = json.loads(envelope.payload_json)
        except (json.JSONDecodeError, TypeError):
            return
        operator = payload.get("source_operator")
        if not operator:
            return
        modality = _infer_modality_from_topic(topic)
        for raw in payload.get("detections", []) or []:
            det = _parse_detection(raw, operator, modality, self._default_sigma)
            if det is not None:
                self._fuser.on_detection(det)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            t = time.time()
            tracks = self._fuser.tick(t=t)
            self._publish_tracks(tracks, t)
            self._publish_coverage(tracks, t)
            if not self._quiet:
                m = coverage_metrics(tracks)
                print(f"[fusion] t={t:.1f} tracks={m['track_count']} "
                      f"multi_src={m['multi_source_count']} "
                      f"improvement={m['coverage_improvement']:.2f}x",
                      file=sys.stderr)
            self._stop.wait(self._dt)

    def _publish_tracks(self, tracks, t: float) -> None:
        payload = {
            "stamp": {"sec": int(t), "nanosec": int((t % 1) * 1e9)},
            "source_operator": "platform",
            "tracks": [dataclasses.asdict(trk) for trk in tracks],
        }
        self._transport.publish(
            logical_topic=TRACK_TOPIC,
            msg_type=TRACK_MSG_TYPE,
            payload_json=json.dumps(payload),
            request_id=str(int(t * 1000)),
        )

    def _publish_coverage(self, tracks, t: float) -> None:
        metrics = coverage_metrics(tracks)
        payload = {
            "stamp": {"sec": int(t), "nanosec": int((t % 1) * 1e9)},
            "source_operator": "platform",
            "metrics": metrics,
        }
        self._transport.publish(
            logical_topic=COVERAGE_TOPIC,
            msg_type=COVERAGE_MSG_TYPE,
            payload_json=json.dumps(payload),
            request_id=str(int(t * 1000)),
        )


def run(args: argparse.Namespace) -> int:
    from dds_envelope_transport import EnvelopeTransport

    fuser = TrackFusion(
        gate_distance_m=args.gate_distance_m,
        gate_velocity_mps=args.gate_velocity_mps,
        confirm_frames=args.confirm_frames,
        lost_frames=args.lost_frames,
    )

    service_holder: dict = {}

    def on_msg(envelope):
        svc = service_holder.get("svc")
        if svc is not None:
            svc.on_envelope(envelope)

    transport = EnvelopeTransport(
        on_message_callback=on_msg,
        domain_id=args.domain,
        local_sender_id="platform-fusion",
    )
    svc = FusionService(
        transport=transport, fuser=fuser, tick_hz=args.tick_hz,
        default_sigma=args.default_sigma, quiet=args.quiet,
    )
    service_holder["svc"] = svc

    transport.start()
    svc.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop()
        transport.stop()
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-operator SpatialDDS fusion service")
    p.add_argument("--domain", type=int, default=1)
    p.add_argument("--tick-hz", type=float, default=2.0, help="Fusion tick rate")
    p.add_argument("--gate-distance-m", type=float, default=5.0)
    p.add_argument("--gate-velocity-mps", type=float, default=5.0)
    p.add_argument("--confirm-frames", type=int, default=2)
    p.add_argument("--lost-frames", type=int, default=6)
    p.add_argument("--default-sigma", type=float, default=0.5,
                   help="Default 1-sigma position uncertainty (m) when detections lack their own")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
