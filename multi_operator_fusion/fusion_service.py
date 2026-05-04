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
for p in (REPO_ROOT, NUSCENES_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
# Force _HERE to sys.path[0]. The earlier ``if not in sys.path`` guard
# silently skipped this insert when a test runner had already added
# multi_operator_fusion/, in which case nuscenes/spatialdds_types
# shadowed the local v1.6 helper module.
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))

from fusion import (  # noqa: E402
    Detection3D,
    Position,
    TrackFusion,
    Velocity,
    coverage_metrics,
)
from spatialdds_types import (  # noqa: E402
    make_component_ref,
    make_entity_binding,
)

DET3D_TOPIC_SUFFIX = "sensing/detection3d/v1"
DET3D_TOPIC_FMT = "spatialdds/{operator}/sensing/detection3d/v1"
PLAN_TOPIC_SUFFIX = "trajectory/v1"
TRACK_TOPIC = "spatialdds/platform/fusion/track/v1"
COVERAGE_TOPIC = "spatialdds/platform/fusion/coverage/v1"
TRAJ_CONFLICT_TOPIC = "spatialdds/platform/events/trajectory_conflict/v1"
ENTITY_BINDING_TOPIC = "spatialdds/platform/entity/binding/v1"
TRACK_MSG_TYPE = "NUSC_FUSED_TRACK_SET"
TRAJ_CONFLICT_MSG_TYPE = "SpatialEvent"
COVERAGE_MSG_TYPE = "NUSC_FUSION_COVERAGE"
ENTITY_BINDING_MSG_TYPE = "EntityBinding"


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
        det_id=str(raw.get("det_id", "")),
    )


class TrajectoryConflictDetector:
    """Spatial-temporal conflict checker for ``PlannedTrajectory`` payloads.

    For every pair of currently-known plans, scans waypoints whose
    timestamps land within ``time_tolerance_s`` of each other and reports
    the closest spatial approach. If that approach is below
    ``conflict_distance_m`` the pair is flagged as a conflict.

    The detector is stateful — call ``update`` whenever a new plan
    arrives, and call ``check_conflicts`` from the service tick loop.
    Plans older than ``stale_threshold_s`` are evicted automatically so
    a stopped publisher doesn't keep firing stale conflicts.
    """

    def __init__(self,
                 conflict_distance_m: float = 5.0,
                 time_tolerance_s: float = 0.4,
                 stale_threshold_s: float = 5.0) -> None:
        self.conflict_distance_m = float(conflict_distance_m)
        self.time_tolerance_s = float(time_tolerance_s)
        self.stale_threshold_s = float(stale_threshold_s)
        # agent_id → (received_wall_t, payload)
        self._plans: dict = {}

    def update(self, payload: dict, received_at: float) -> None:
        agent_id = payload.get("agent_id")
        if not agent_id:
            return
        self._plans[str(agent_id)] = (float(received_at), payload)

    def _stamp_seconds(self, stamp: dict) -> float:
        return float(stamp.get("sec", 0)) + float(stamp.get("nanosec", 0)) / 1e9

    def _evict_stale(self, t_now: float) -> None:
        cutoff = t_now - self.stale_threshold_s
        for agent_id in list(self._plans):
            if self._plans[agent_id][0] < cutoff:
                del self._plans[agent_id]

    def check_conflicts(self, t_now: float) -> list:
        self._evict_stale(t_now)
        conflicts = []
        agents = sorted(self._plans)
        for i in range(len(agents)):
            for j in range(i + 1, len(agents)):
                a, b = agents[i], agents[j]
                conflict = self._check_pair(a, self._plans[a][1],
                                              b, self._plans[b][1])
                if conflict is not None:
                    conflicts.append(conflict)
        return conflicts

    def _check_pair(self, agent_a: str, traj_a: dict,
                     agent_b: str, traj_b: dict) -> Optional[dict]:
        min_dist = float("inf")
        conflict_time: Optional[float] = None
        conflict_pos: Optional[dict] = None

        for wp_a in traj_a.get("waypoints", []) or []:
            t_a = self._stamp_seconds(wp_a.get("stamp") or {})
            pos_a = (wp_a.get("pose") or {}).get("t") or {}
            for wp_b in traj_b.get("waypoints", []) or []:
                t_b = self._stamp_seconds(wp_b.get("stamp") or {})
                if abs(t_a - t_b) > self.time_tolerance_s:
                    continue
                pos_b = (wp_b.get("pose") or {}).get("t") or {}
                dx = float(pos_a.get("x", 0.0)) - float(pos_b.get("x", 0.0))
                dy = float(pos_a.get("y", 0.0)) - float(pos_b.get("y", 0.0))
                dz = float(pos_a.get("z", 0.0)) - float(pos_b.get("z", 0.0))
                dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                if dist < min_dist:
                    min_dist = dist
                    conflict_time = (t_a + t_b) / 2.0
                    conflict_pos = {
                        "x": (float(pos_a.get("x", 0.0)) + float(pos_b.get("x", 0.0))) / 2.0,
                        "y": (float(pos_a.get("y", 0.0)) + float(pos_b.get("y", 0.0))) / 2.0,
                        "z": (float(pos_a.get("z", 0.0)) + float(pos_b.get("z", 0.0))) / 2.0,
                    }

        if min_dist >= self.conflict_distance_m or conflict_time is None:
            return None

        traj_a_stamp = self._stamp_seconds(traj_a.get("stamp") or {})
        time_to_conflict = conflict_time - traj_a_stamp if traj_a_stamp else None

        return {
            "event_type": "trajectory_conflict",
            "agents": [agent_a, agent_b],
            "min_distance_m": round(min_dist, 3),
            "conflict_time": conflict_time,
            "conflict_position": conflict_pos,
            "time_to_conflict": (round(time_to_conflict, 2)
                                  if time_to_conflict is not None else None),
        }


class FusionService:
    def __init__(self, transport, fuser: TrackFusion, tick_hz: float, default_sigma: float, quiet: bool):
        self._transport = transport
        self._fuser = fuser
        self._dt = 1.0 / max(0.1, tick_hz)
        self._default_sigma = default_sigma
        self._quiet = quiet
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._conflict_detector = TrajectoryConflictDetector()
        self._reported_conflicts: set = set()  # dedupes (agent_a, agent_b) pairs

    def on_envelope(self, envelope) -> None:
        """Legacy ``EnvelopeTransport`` callback path — accepts an envelope
        object with ``.logical_topic`` and ``.payload_json``."""
        topic = getattr(envelope, "logical_topic", "") or ""
        try:
            payload = json.loads(envelope.payload_json)
        except (json.JSONDecodeError, TypeError):
            return
        self._dispatch(topic, payload)

    def on_message(self, msg_type: str, logical_topic: str,
                    payload: dict, stamp_ns: int) -> None:
        """``bridges/envelope_io.EnvelopeSubscriber`` callback path —
        called by the lossless RELIABLE+KEEP_ALL reader. Same dispatch
        logic as the legacy path; just a different signature."""
        self._dispatch(logical_topic or "", payload)

    def _dispatch(self, topic: str, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        if topic.endswith(DET3D_TOPIC_SUFFIX):
            self._on_detection_set(topic, payload)
        elif topic.endswith(PLAN_TOPIC_SUFFIX) and "/plan/" in topic:
            self._conflict_detector.update(payload, time.time())

    def _on_detection_set(self, topic: str, payload: dict) -> None:
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
            self._publish_trajectory_conflicts(t)
            self._publish_entity_bindings(tracks, t)
            if not self._quiet:
                m = coverage_metrics(tracks)
                print(f"[fusion] t={t:.1f} tracks={m['track_count']} "
                      f"multi_src={m['multi_source_count']} "
                      f"improvement={m['coverage_improvement']:.2f}x",
                      file=sys.stderr)
            self._stop.wait(self._dt)

    def _publish_trajectory_conflicts(self, t: float) -> None:
        conflicts = self._conflict_detector.check_conflicts(t)
        for c in conflicts:
            # Dedupe by agent pair so the dashboard gets one alert per
            # conflict, not one per fusion tick. The dedupe key is
            # cleared lazily — if the conflict resolves and reappears
            # later we report it again.
            key = tuple(sorted(c["agents"]))
            if key in self._reported_conflicts:
                continue
            self._reported_conflicts.add(key)
            payload = {
                "schema_version": "spatial.events/1.5",
                "stamp": {"sec": int(t), "nanosec": int((t % 1) * 1e9)},
                "source_operator": "platform",
                **c,
            }
            self._transport.publish(
                logical_topic=TRAJ_CONFLICT_TOPIC,
                msg_type=TRAJ_CONFLICT_MSG_TYPE,
                payload=payload,
                request_id=str(int(t * 1000)),
            )
            if not self._quiet:
                print(f"[fusion] conflict: {c['agents']} dist={c['min_distance_m']}m "
                      f"in {c['time_to_conflict']}s at "
                      f"({c['conflict_position']['x']:.1f}, "
                      f"{c['conflict_position']['y']:.1f})",
                      file=sys.stderr)
        # Re-arm the dedupe set every tick — not only when this tick
        # had conflicts. Otherwise during the no-conflict half of the
        # ego cycle the set is never cleared, and when the (A, B)
        # crossing recurs in the next cycle the event is suppressed.
        # The dashboard only sees one conflict ever, instead of one
        # per cycle.
        still_active = {tuple(sorted(c["agents"])) for c in conflicts}
        self._reported_conflicts &= still_active

    def _publish_entity_bindings(self, tracks, t: float) -> None:
        """One EntityBinding per confirmed track. Components are:
          1. The fused track itself (so subscribers can deref the track
             after receiving the binding alone),
          2. one ComponentRef per contributing operator pointing at the
             most-recent detection that fed this track.
        """
        for trk in tracks:
            components = [make_component_ref(topic=TRACK_TOPIC,
                                                key=trk.track_id)]
            for op, det_id in trk.last_det_per_operator.items():
                if not det_id:
                    continue
                components.append(make_component_ref(
                    topic=DET3D_TOPIC_FMT.format(operator=op),
                    key=det_id,
                ))
            payload = make_entity_binding(
                entity_id=f"entity_{trk.track_id}",
                entity_class=trk.object_class,
                components=components,
                pose={
                    "t": dataclasses.asdict(trk.position),
                    "q": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                },
                source_id="platform-fusion",
                timestamp_s=t,
            )
            self._transport.publish(
                logical_topic=ENTITY_BINDING_TOPIC,
                msg_type=ENTITY_BINDING_MSG_TYPE,
                payload=payload,
                request_id=trk.track_id,
            )

    def _publish_tracks(self, tracks, t: float) -> None:
        payload = {
            "stamp": {"sec": int(t), "nanosec": int((t % 1) * 1e9)},
            "source_operator": "platform",
            "tracks": [dataclasses.asdict(trk) for trk in tracks],
        }
        self._transport.publish(
            logical_topic=TRACK_TOPIC,
            msg_type=TRACK_MSG_TYPE,
            payload=payload,
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
            payload=payload,
            request_id=str(int(t * 1000)),
        )


def run(args: argparse.Namespace) -> int:
    # Use the lossless RELIABLE+KEEP_ALL transport from bridges/envelope_io.
    # The legacy EnvelopeTransport's reader QoS (KEEP_LAST(1)) collapses
    # back-to-back writes from one publisher in a single poll interval —
    # which is exactly what the synthetic publisher does (3 operators, each
    # writing detection + ego pose + plan per cycle). With KEEP_LAST(1) the
    # fusion service ends up seeing only the last operator's plan in each
    # poll, so the trajectory conflict detector never has all 3 ego paths
    # in its window. Same fix as the web bridge — see commit b19a54b.
    from bridges.envelope_io import EnvelopePublisher, EnvelopeSubscriber

    fuser = TrackFusion(
        gate_distance_m=args.gate_distance_m,
        gate_velocity_mps=args.gate_velocity_mps,
        confirm_frames=args.confirm_frames,
        lost_frames=args.lost_frames,
    )

    service_holder: dict = {}

    def on_msg(msg_type: str, logical_topic: str, payload: dict, stamp_ns: int):
        svc = service_holder.get("svc")
        if svc is not None:
            svc.on_message(msg_type, logical_topic, payload, stamp_ns)

    publisher = EnvelopePublisher(domain_id=args.domain)
    subscriber = EnvelopeSubscriber(domain_id=args.domain, callback=on_msg)

    svc = FusionService(
        transport=publisher, fuser=fuser, tick_hz=args.tick_hz,
        default_sigma=args.default_sigma, quiet=args.quiet,
    )
    service_holder["svc"] = svc

    subscriber.start()
    svc.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop()
        subscriber.stop()
        publisher.close()
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
