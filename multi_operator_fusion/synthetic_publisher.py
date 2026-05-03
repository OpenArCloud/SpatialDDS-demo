#!/usr/bin/env python3
"""Synthetic multi-operator Detection3D publisher.

Generates a continuous stream of ``NUSC_DET3D_SET`` envelopes from N fake
operators with overlapping fields-of-view, so the fusion service can
merge them into multi-source tracks. No dataset prerequisites — the
positions are computed from a closed-form path so the demo works on a
fresh AWS deployment with nothing pre-staged.

Topic shape (matches nuScenes / multi_operator_fusion publishers):

    logical_topic = spatialdds/{operator}/sensing/detection3d/v1
    msg_type      = NUSC_DET3D_SET
    payload       = {
        "frame_seq": int,
        "stamp": {"sec": int, "nanosec": int},
        "source_operator": str,
        "detections": [
            {"det_id": str, "center": {x,y,z}, "size": {x,y,z},
             "q": {x,y,z,w}, "class_id": str, "score": float,
             "has_velocity": true, "velocity": {x,y,z}},
            ...
        ]
    }

Run standalone:

    python -m multi_operator_fusion.synthetic_publisher \\
        --domain 0 --operators 3 --rate 10 --objects-per-operator 5
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List

# Reach the shared bridges/envelope_io.py (RELIABLE + KEEP_ALL writer).
# This matters because we publish 3 operators back-to-back from ONE process
# — the legacy ``EnvelopeTransport`` uses default ``KEEP_LAST(1)`` history
# and silently drops all but the last sample in such bursts. The lossless
# writer holds everything until it's acked.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
# ``sys.path.insert(0, p)`` inserts at the FRONT, so the last value
# iterated lands at sys.path[0]. We want _HERE first so the local
# multi_operator_fusion/spatialdds_types module shadows the unrelated
# nuscenes/spatialdds_types — iterate it last.
for p in (str(_REPO_ROOT), str(_REPO_ROOT / "nuscenes"),
          str(_REPO_ROOT / "bridges"), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from envelope_io import EnvelopePublisher  # noqa: E402
from spatialdds_types import (  # noqa: E402
    make_planned_trajectory,
    make_planned_waypoint,
)


MSG_TYPE = "NUSC_DET3D_SET"
TOPIC_FMT = "spatialdds/{operator}/sensing/detection3d/v1"

EGO_POSE_MSG_TYPE = "NUSC_EGO_POSE"
EGO_POSE_TOPIC_FMT = "spatialdds/{operator}/ego/pose/v1"

PLAN_MSG_TYPE = "PlannedTrajectory"
PLAN_TOPIC_FMT = "spatialdds/{operator}/plan/{operator}_ego/trajectory/v1"

# Visible classes — keep small so the dashboard is readable.
CLASSES = ["vehicle.car", "vehicle.truck", "human.pedestrian", "vehicle.bicycle"]

# ── Ego trajectories — designed to surface a real conflict ──────────────
# Three operators crossing one intersection. A and B converge on the
# centre at t_demo ≈ 6 s; C tracks parallel to B at y = -10 so it never
# gets within the conflict distance. The fusion service's
# TrajectoryConflictDetector should fire one event for the (A, B) pair
# and stay silent on (A, C) and (B, C) — exactly the behaviour the plan
# asks the dashboard to demo.
EGO_PATHS = {
    # operator_a: south → north
    0: {"x0":   0.0, "y0": -30.0, "vx":  0.0, "vy":  5.0},
    # operator_b: east  → west
    1: {"x0":  30.0, "y0":   0.0, "vx": -5.0, "vy":  0.0},
    # operator_c: east  → west, parallel to B at y = -10 (close-but-safe)
    2: {"x0":  30.0, "y0": -10.0, "vx": -5.0, "vy":  0.0},
}

# All ego paths repeat every EGO_CYCLE_S so the (A, B) crossing recurs and
# the dashboard demo isn't a one-shot at process start. With vx=vy=5 m/s
# and start positions ±30 m, a 12 s cycle takes each ego from start →
# past the centre → 30 m past it, then snaps back to start. The (A, B)
# crossing happens at t_local ≈ 6 s every cycle.
EGO_CYCLE_S = 12.0


def _ego_state(op_idx: int, t_demo: float) -> Dict[str, float]:
    """Closed-form straight-line ego state for the configured paths.

    The trajectories live on the local map frame (not lat/lon); the demo
    just needs them to be deterministic and to cross at a known time so
    the conflict detector and the dashboard have something predictable.
    Time is taken modulo ``EGO_CYCLE_S`` so the crossing event recurs.
    """
    t_local = t_demo % EGO_CYCLE_S
    # Default to the publisher offset for any operator beyond the three
    # we explicitly designed paths for.
    if op_idx in EGO_PATHS:
        p = EGO_PATHS[op_idx]
        x = p["x0"] + p["vx"] * t_local
        y = p["y0"] + p["vy"] * t_local
        vx, vy = p["vx"], p["vy"]
    else:
        ox, oy = _operator_offset(op_idx)
        x = ox + 0.5 * t_local
        y = oy
        vx, vy = 0.5, 0.0
    return {"x": x, "y": y, "z": 0.5, "vx": vx, "vy": vy, "vz": 0.0}


def _build_ego_pose(op_idx: int, ego: Dict[str, float], t_wall: float,
                     frame_seq: int) -> Dict:
    """FramedPose-shape payload for the operator's current ego pose."""
    yaw = math.atan2(ego["vy"], ego["vx"]) if (ego["vx"] or ego["vy"]) else 0.0
    return {
        "frame_seq": int(frame_seq),
        "stamp": {"sec": int(t_wall), "nanosec": int((t_wall % 1) * 1e9)},
        "source_operator": _operator_id(op_idx),
        "frame_ref": {"uuid": "", "fqn": f"{_operator_id(op_idx)}/map"},
        "pose": {
            "t": {"x": ego["x"], "y": ego["y"], "z": ego["z"]},
            "q": _quat_yaw(yaw),
        },
        "has_velocity": True,
        "velocity": {"x": ego["vx"], "y": ego["vy"], "z": ego["vz"]},
    }


def _build_planned_trajectory(op_idx: int, t_demo: float, t_wall: float,
                                horizon_s: float = 5.0,
                                dt_plan: float = 0.5,
                                replan_rate_hz: float = 2.0) -> Dict:
    """Project the ego state forward at constant velocity.

    Uncertainty grows with horizon, confidence decays — both linear in
    the number of steps. ``waypoint.stamp`` is wall-clock so two
    operators' plans can be aligned by the conflict detector.
    """
    n = max(1, int(round(horizon_s / dt_plan)))
    waypoints = []
    operator = _operator_id(op_idx)
    # Predict from the operator's CURRENT demo time, not from t=0.
    for i in range(1, n + 1):
        t_future_demo = t_demo + i * dt_plan
        future = _ego_state(op_idx, t_future_demo)
        waypoints.append(make_planned_waypoint(
            x=future["x"], y=future["y"], z=future["z"],
            timestamp_s=t_wall + i * dt_plan,
            vx=future["vx"], vy=future["vy"], vz=future["vz"],
            uncertainty_m=0.5 + 0.3 * i,
            confidence=max(0.3, 1.0 - 0.07 * i),
        ))
    return make_planned_trajectory(
        agent_id=f"{operator}_ego",
        plan_id=f"plan_{int(t_wall * 1000)}",
        plan_revision=int(t_wall * replan_rate_hz),
        frame_ref_fqn=f"{operator}/map",
        waypoints=waypoints,
        horizon_sec=n * dt_plan,
        replan_rate_hz=replan_rate_hz,
        timestamp_s=t_wall,
    )


def _operator_id(idx: int) -> str:
    return f"operator_{chr(ord('a') + idx)}"


def _operator_offset(idx: int) -> tuple:
    """Place each operator's nominal viewpoint a few meters apart so
    they end up with overlapping but not-identical detection sets."""
    angle = (2.0 * math.pi / max(1, 3)) * idx
    return (12.0 * math.cos(angle), 12.0 * math.sin(angle))


def _object_path(t: float, op_idx: int, obj_idx: int) -> Dict[str, float]:
    """Closed-form motion for synthetic object ``(op_idx, obj_idx)``.

    Each object orbits a small circle plus an operator offset, which gives
    overlapping FOVs across operators (perfect for the fusion service to
    merge into multi-source tracks). The angular speed varies per object
    so they don't all move in lockstep."""
    ox, oy = _operator_offset(op_idx)
    radius = 4.0 + 2.0 * (obj_idx % 3)
    omega = 0.4 + 0.07 * obj_idx
    phase = 0.6 * obj_idx + 0.3 * op_idx
    cx = ox + radius * math.cos(omega * t + phase)
    cy = oy + radius * math.sin(omega * t + phase)
    cz = 0.5 + 0.2 * math.sin(0.5 * t + obj_idx)
    # Velocity (derivative of position)
    vx = -radius * omega * math.sin(omega * t + phase)
    vy = radius * omega * math.cos(omega * t + phase)
    vz = 0.1 * math.cos(0.5 * t + obj_idx)
    yaw = math.atan2(vy, vx)
    return {
        "cx": cx, "cy": cy, "cz": cz,
        "vx": vx, "vy": vy, "vz": vz,
        "yaw": yaw,
    }


def _quat_yaw(yaw: float) -> Dict[str, float]:
    """Quaternion (x,y,z,w) for a pure yaw rotation around z."""
    return {
        "x": 0.0, "y": 0.0,
        "z": math.sin(yaw / 2.0),
        "w": math.cos(yaw / 2.0),
    }


def _build_detection(op_idx: int, obj_idx: int, t: float) -> Dict:
    p = _object_path(t, op_idx, obj_idx)
    cls = CLASSES[obj_idx % len(CLASSES)]
    if cls == "vehicle.truck":
        size = {"x": 7.0, "y": 2.4, "z": 3.0}
    elif cls == "vehicle.car":
        size = {"x": 4.5, "y": 1.8, "z": 1.6}
    elif cls == "vehicle.bicycle":
        size = {"x": 1.7, "y": 0.5, "z": 1.2}
    else:  # pedestrian
        size = {"x": 0.6, "y": 0.6, "z": 1.7}
    return {
        "det_id": f"obj_{obj_idx:02d}",
        "center": {"x": p["cx"], "y": p["cy"], "z": p["cz"]},
        "size": size,
        "q": _quat_yaw(p["yaw"]),
        "class_id": cls,
        "score": 0.85 + 0.05 * math.sin(t + obj_idx + op_idx),
        "has_velocity": True,
        "velocity": {"x": p["vx"], "y": p["vy"], "z": p["vz"]},
    }


def _build_set(op_idx: int, n_objects: int, t: float, frame_seq: int,
                jitter_seed: int = 0) -> Dict:
    """Build one Detection3DSet payload. Each operator drops a different
    one of the shared objects so the fusion service has both
    multi-source AND single-source tracks to work with — that exercises
    the code paths the fused-coverage metric is designed to surface."""
    detections: List[Dict] = []
    drop_idx = (op_idx + frame_seq // 50) % n_objects
    for obj_idx in range(n_objects):
        if obj_idx == drop_idx:
            continue
        detections.append(_build_detection(op_idx, obj_idx, t))
    return {
        "frame_seq": int(frame_seq),
        "stamp": {"sec": int(t), "nanosec": int((t % 1) * 1e9)},
        "source_operator": _operator_id(op_idx),
        "detections": detections,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", type=int, default=0, help="DDS domain")
    parser.add_argument("--operators", type=int, default=3,
                        help="Number of synthetic operators (default 3)")
    parser.add_argument("--objects-per-operator", type=int, default=5,
                        help="Number of fake objects in each operator's frame")
    parser.add_argument("--rate", type=float, default=10.0,
                        help="Per-operator publish rate (Hz)")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="Stop after N frames per operator (0 = forever)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.operators < 1:
        print("--operators must be >= 1", file=sys.stderr)
        return 1
    if args.rate <= 0:
        print("--rate must be > 0", file=sys.stderr)
        return 1

    transport = EnvelopePublisher(args.domain)

    period = 1.0 / args.rate
    frame_seq = 0
    # ``t_demo`` is the wall-clock seconds since this process started — the
    # ego paths are deterministic functions of it. Persisting wall-clock
    # ``t`` in waypoint stamps means the conflict detector can compare
    # equal-stamp waypoints across operators without sharing demo time.
    start_wall = time.time()
    plan_every_n = max(1, int(round(args.rate / 2.0)))   # 2 Hz replan
    print(f"[synthetic] domain={args.domain} operators={args.operators} "
          f"objects/op={args.objects_per_operator} rate={args.rate} Hz  "
          f"(plan every {plan_every_n} frames = "
          f"{args.rate / plan_every_n:.1f} Hz)",
          file=sys.stderr)

    try:
        while True:
            t = time.time()
            t_demo = t - start_wall
            for op_idx in range(args.operators):
                operator = _operator_id(op_idx)
                # Detection3DSet (10 Hz)
                payload = _build_set(op_idx, args.objects_per_operator, t,
                                       frame_seq)
                transport.publish(
                    logical_topic=TOPIC_FMT.format(operator=operator),
                    msg_type=MSG_TYPE,
                    payload=payload,
                )
                # Ego pose (10 Hz) — useful for the dashboard and for the
                # fusion service's future use.
                ego = _ego_state(op_idx, t_demo)
                transport.publish(
                    logical_topic=EGO_POSE_TOPIC_FMT.format(operator=operator),
                    msg_type=EGO_POSE_MSG_TYPE,
                    payload=_build_ego_pose(op_idx, ego, t, frame_seq),
                )
                # PlannedTrajectory (2 Hz by default)
                if frame_seq % plan_every_n == 0:
                    plan = _build_planned_trajectory(op_idx, t_demo, t)
                    transport.publish(
                        logical_topic=PLAN_TOPIC_FMT.format(operator=operator),
                        msg_type=PLAN_MSG_TYPE,
                        payload=plan,
                    )
            if not args.quiet and frame_seq % 50 == 0:
                print(f"[synthetic] frame_seq={frame_seq} "
                      f"t_demo={t_demo:.1f}", file=sys.stderr)
            frame_seq += 1
            if args.max_frames and frame_seq >= args.max_frames:
                break
            sleep_for = period - (time.time() - t)
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("[synthetic] stopped", file=sys.stderr)
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
