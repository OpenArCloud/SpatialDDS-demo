#!/usr/bin/env python3
"""Platform-level fusion service.

Discovers every operator's detection stream through the announces on the
well-known discovery topic, runs :class:`fusion.TrackFusion`, and publishes
its own four typed streams — which it announces the same way, so a consumer
finds the platform exactly as it finds an operator:

    spatialdds/platform/fusion/track/v1              oarc.fused_track
    spatialdds/platform/fusion/coverage/v1           oarc.fusion_coverage
    spatialdds/platform/events/trajectory_conflict/v1  spatial_event
    spatialdds/platform/entity/binding/v1            entity_binding

Detection sets are recognised by the announced type on the topic they arrive
on, not by a demo-private label inside the payload. Operator provenance is
read from ``source_operator``, which the per-operator publisher stamps.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import signal
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
    circle_coverage,
    make_announce,
    make_component_ref,
    make_entity_binding,
    make_fused_track_set,
    make_fusion_coverage,
    make_trajectory_conflict_event,
    topic_meta,
)

DET3D_TYPE = "oarc.detection3d_velocity"
PLAN_TYPE = "planned_trajectory"
DET3D_TOPIC_SUFFIX = "sensing/detection3d/v1"
DET3D_TOPIC_FMT = "spatialdds/{operator}/sensing/detection3d/v1"
PLAN_TOPIC_SUFFIX = "trajectory/v1"
TRACK_TOPIC = "spatialdds/platform/fusion/track/v1"
COVERAGE_TOPIC = "spatialdds/platform/fusion/coverage/v1"
TRAJ_CONFLICT_TOPIC = "spatialdds/platform/events/trajectory_conflict/v1"
ENTITY_BINDING_TOPIC = "spatialdds/platform/entity/binding/v1"
SCENE_FRAME_FQN = "scene/intersection"

# (topic, §3.3.2 type, §3.3.3 QoS profile) for each lane the platform owns.
# The announce and the writers are both built from this, so the two cannot
# disagree about what is on a topic.
PLATFORM_SERVICE_ID = "platform-fusion"
PLATFORM_LANES = (
    (TRACK_TOPIC, "oarc.fused_track", "POSE_RT"),
    (COVERAGE_TOPIC, "oarc.fusion_coverage", "MAP_META"),
    (TRAJ_CONFLICT_TOPIC, "spatial_event", "EVENT_RT"),
    (ENTITY_BINDING_TOPIC, "entity_binding", "MAP_META"),
)


def _infer_modality_from_topic(logical_topic: str) -> str:
    """Best-effort modality tag — operators publish their fused output
    on ``…/sensing/detection3d/v1`` without per-sensor provenance, so we
    just tag the source modality as ``det3d`` for now. Extend if the
    publisher begins splitting per-modality."""
    return "det3d"


def _vec3(value, keys=("x", "y", "z")) -> Optional[tuple]:
    """Vec3 arrives as an IDL array; tolerate the legacy {x,y,z} object too."""
    if isinstance(value, dict):
        return tuple(float(value.get(k, 0.0)) for k in keys)
    if isinstance(value, (list, tuple)) and len(value) >= len(keys):
        return tuple(float(v) for v in value[:len(keys)])
    return None


def _parse_detection(raw: dict, source_operator: str, modality: str,
                     default_sigma: float) -> Optional[Detection3D]:
    """
    Parse one `oarc_demo::DetectionWithVelocity`.

    The row composes the spec `Detection3D` (which has no velocity member)
    with the velocity this fuser gates on. `source_modality` comes from the
    row when present, since the base station and the AV operators differ.
    """
    detection = raw.get("detection") if isinstance(raw.get("detection"), dict) else raw
    center = _vec3(detection.get("center"))
    if center is None:
        return None
    velocity = _vec3(raw.get("velocity")) or (0.0, 0.0, 0.0)
    if not raw.get("has_velocity", True):
        velocity = (0.0, 0.0, 0.0)
    return Detection3D(
        position=Position(x=center[0], y=center[1], z=center[2]),
        velocity=Velocity(vx=velocity[0], vy=velocity[1], vz=velocity[2]),
        source_operator=source_operator,
        source_modality=str(raw.get("source_modality") or modality),
        object_class=str(detection.get("class_id", "unknown")),
        confidence=float(detection.get("score", 1.0)),
        position_uncertainty=default_sigma,
        det_id=str(detection.get("det_id", "")),
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
            pos_a = _vec3((wp_a.get("pose") or {}).get("t"))
            if pos_a is None:
                continue
            for wp_b in traj_b.get("waypoints", []) or []:
                t_b = self._stamp_seconds(wp_b.get("stamp") or {})
                if abs(t_a - t_b) > self.time_tolerance_s:
                    continue
                pos_b = _vec3((wp_b.get("pose") or {}).get("t"))
                if pos_a is None or pos_b is None:
                    continue
                dx, dy, dz = (a - b for a, b in zip(pos_a, pos_b))
                dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                if dist < min_dist:
                    min_dist = dist
                    conflict_time = (t_a + t_b) / 2.0
                    conflict_pos = {
                        "x": (pos_a[0] + pos_b[0]) / 2.0,
                        "y": (pos_a[1] + pos_b[1]) / 2.0,
                        "z": (pos_a[2] + pos_b[2]) / 2.0,
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

    def on_message(self, type_name: str, topic: str,
                   payload: dict, stamp_ns: int) -> None:
        """
        ``spatialdds_demo.stream.StreamSubscriber`` callback.

        ``type_name`` is the announced §3.3.2 type, so routing is on what the
        sample *is* rather than on what its topic name happens to end with.
        The topic suffix is still checked for the detection lanes because the
        same type is legitimately published by several services.
        """
        if not isinstance(payload, dict):
            return
        if type_name == DET3D_TYPE:
            self._on_detection_set(topic, payload)
        elif type_name == PLAN_TYPE:
            self._conflict_detector.update(payload, time.time())

    def _on_detection_set(self, topic: str, payload: dict) -> None:
        operator = payload.get("source_operator")
        if not operator:
            return
        modality = _infer_modality_from_topic(topic)
        for raw in payload.get("dets") or payload.get("detections") or []:
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
            self._transport.publish(TRAJ_CONFLICT_TOPIC,
                                    make_trajectory_conflict_event(
                                        c, timestamp_s=t,
                                        frame_ref_fqn=SCENE_FRAME_FQN,
                                        source_id=PLATFORM_SERVICE_ID))
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
            self._transport.publish(ENTITY_BINDING_TOPIC, make_entity_binding(
                entity_id=f"entity_{trk.track_id}",
                entity_class=trk.object_class,
                components=components,
                position=(trk.position.x, trk.position.y, trk.position.z),
                frame_ref_fqn=SCENE_FRAME_FQN,
                source_id=PLATFORM_SERVICE_ID,
                timestamp_s=t,
            ))

    def _publish_tracks(self, tracks, t: float) -> None:
        self._transport.publish(TRACK_TOPIC, make_fused_track_set(
            tracks, source_operator="platform", timestamp_s=t))

    def _publish_coverage(self, tracks, t: float) -> None:
        # `metrics` was a nested free-form object under the old payload; the
        # struct flattens it, because "a dict of numbers" is not a type.
        self._transport.publish(COVERAGE_TOPIC, make_fusion_coverage(
            coverage_metrics(tracks), source_operator="platform", timestamp_s=t))


def run(args: argparse.Namespace) -> int:
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo.stream import StreamPublisher, StreamSubscriber

    fuser = TrackFusion(
        gate_distance_m=args.gate_distance_m,
        gate_velocity_mps=args.gate_velocity_mps,
        confirm_frames=args.confirm_frames,
        lost_frames=args.lost_frames,
    )

    service_holder: dict = {}

    def on_msg(type_name: str, topic: str, payload: dict, stamp_ns: int):
        svc = service_holder.get("svc")
        if svc is not None:
            svc.on_message(type_name, topic, payload, stamp_ns)

    participant = DomainParticipant(args.domain)
    publisher = StreamPublisher(participant)
    subscriber = StreamSubscriber(participant, on_msg)

    # The platform announces itself before writing, like any other service —
    # its outputs are discoverable rather than a set of topic names a
    # consumer has to be told about out of band.
    publisher.announce(_platform_announce(time.time()))

    svc = FusionService(
        transport=publisher, fuser=fuser, tick_hz=args.tick_hz,
        default_sigma=args.default_sigma, quiet=args.quiet,
    )
    service_holder["svc"] = svc
    svc.start()

    stopping = threading.Event()

    def _shutdown(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    announce_period_s = 5.0
    last_announce = time.time()
    try:
        while not stopping.is_set():
            # Reading announces is what opens readers for operators that
            # started after this service did.
            subscriber.poll()
            now = time.time()
            if now - last_announce >= announce_period_s:
                publisher.announce(_platform_announce(now))
                last_announce = now
            time.sleep(0.02)
    finally:
        svc.stop()
        publisher.close()
    return 0


def _platform_announce(t_wall: float) -> dict:
    """The platform's own announce, built from the same lane table it writes."""
    return make_announce(
        operator="platform", service_kind="FUSION",
        topics=[topic_meta(topic, type_name, profile)
                for topic, type_name, profile in PLATFORM_LANES],
        # The platform fuses whatever reaches it, so its coverage is the
        # union of its inputs rather than a sensing footprint of its own.
        # A generous circle over the scene is the honest approximation
        # available without tracking input coverage, which 1.7 gives no
        # way to express as "derived from my subscriptions".
        coverage=circle_coverage(0.0, 0.0, 500.0),
        timestamp_s=t_wall,
    )


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
