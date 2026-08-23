#!/usr/bin/env python3
"""Rerun subscriber for the multi-operator fusion demo.

Routes each operator's streams under ``world/{operator}/…`` and the
platform fuser's output under ``world/fused/…``, giving a top-down
intersection view of who-sees-what versus the unified fusion output.

Topics are not hardcoded. Each service announces its lanes with their
3.3.2 type and 3.3.3 QoS profile, and this subscriber opens one typed
reader per announced lane — so a stream that starts mid-run is picked up,
and a type this build cannot resolve is skipped rather than fatal.

Rendered types:

    oarc.framed_pose / geopose      ego pose and trail
    oarc.detection3d_velocity       per-operator detections
    video_frame, oarc.lidar_frame   raw sensor frames
    radar_detection                 radar detection sets
    planned_trajectory              intent, and the conflicts between plans
    oarc.fused_track                the platform's unified tracks
    oarc.fusion_coverage            the coverage headline
    entity_binding, spatial_event   cross-topic correlation, alerts
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import time as _t

import numpy as np
import rerun as rr

# Rerun 0.22+ internally calls np.asarray(quat_batch, copy=False) when it
# receives per-box quaternion arrays. copy= was only added to asarray in
# NumPy 2.0; with NumPy 1.x (required by nuscenes-devkit) Rerun logs a
# non-fatal RerunWarning on every Boxes3D log. Rotations still render
# correctly — the warning just spams stderr — so we silence it rather than
# let it drown out real diagnostic output.
warnings.filterwarnings(
    "ignore",
    message=r".*asarray\(\) got an unexpected keyword argument 'copy'.*",
)

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent
NUSCENES_DIR = REPO_ROOT / "nuscenes"
for _p in (REPO_ROOT, NUSCENES_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
if str(_HERE) in sys.path:
    sys.path.remove(str(_HERE))
sys.path.insert(0, str(_HERE))

from routing import operator_from_topic  # noqa: E402

# Announce is not a lane in TopicMeta — it *is* discovery — so it gets a
# routing key of its own rather than a registry type name.
ANNOUNCE_TYPE = "spatialdds/discovery/announce"


# ── Palette ────────────────────────────────────────────────────────────
# One source of truth so Rerun, the web dashboard, and slides all agree.
OPERATOR_COLORS: Dict[str, List[int]] = {
    "operator_a": [80, 200, 120],     # green
    "operator_b": [80, 140, 255],     # blue
    "operator_c": [255, 165, 60],     # orange
    "infrastructure": [220, 100, 220],  # magenta
}
FUSED_COLOR = [240, 240, 240]
CONFLICT_COLOR = [255, 40, 40]
BINDING_COLOR = [180, 180, 180]
DEFAULT_COLOR = [200, 200, 200]

# Cap how big a coverage ring we render. Rerun's Spatial3DView auto-fit
# uses the bounding box of every logged vertex, so a 200 m
# ``infrastructure`` circle would zoom the camera out far enough to make
# the actual ±30 m crossing action visually negligible. Geometric
# truth still travels on the wire (the Announce payload is unmodified);
# we just shrink what we draw.
COVERAGE_RENDER_MAX_M = 35.0


def _operator_color(operator: str, alpha: Optional[int] = None) -> List[int]:
    base = list(OPERATOR_COLORS.get(operator, DEFAULT_COLOR))
    if alpha is not None:
        return base + [int(max(0, min(255, alpha)))]
    return base


def _stamp_ns(stamp: Dict[str, Any]) -> int:
    return int(stamp.get("sec", 0)) * 1_000_000_000 + int(stamp.get("nanosec", 0))


def _set_time(stamp: Dict[str, Any], frame_num: int) -> None:
    """Set both timelines for this Rerun event. ``frame_num`` is passed
    explicitly so every handler logs its rr.log calls under the same
    counter value — no shared mutable state, no surprises from
    out-of-order increments across handlers.
      * ``frame``: monotonic int counter — the viewer auto-ranges to
        [0, current] so streamed data is always inside the window.
      * ``timestamp``: absolute wall-clock seconds via ``timestamp=``
        (NOT ``duration=`` — duration is seconds-since-stream-start
        and a 1.78e9 s value would look absurd in that mode).
    """
    rr.set_time("frame", sequence=frame_num)
    sec = float(stamp.get("sec", 0))
    nsec = float(stamp.get("nanosec", 0))
    if sec > 0:
        rr.set_time("timestamp", timestamp=sec + nsec / 1e9)


def _xyz(value: Any, default: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> List[float]:
    """
    A Vec3 as [x, y, z].

    The spec's Vec3 is ``double[3]``, so it arrives as an array. Older demo
    payloads used ``{"x":…, "y":…, "z":…}``; both are read here because the
    nuScenes and DeepSense converters have not been migrated yet, and this
    subscriber renders all of them.
    """
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return [float(v) for v in value[:3]]
    if isinstance(value, dict):
        return [float(value.get(k, d)) for k, d in zip("xyz", default)]
    return list(default)


def _q_xyzw(q: Any) -> List[float]:
    """A Quat as [x, y, z, w]. Identity when absent."""
    if isinstance(q, (list, tuple)) and len(q) >= 4:
        return [float(v) for v in q[:4]]
    if isinstance(q, dict):
        return [float(q.get("x", 0.0)), float(q.get("y", 0.0)),
                float(q.get("z", 0.0)), float(q.get("w", 1.0))]
    return [0.0, 0.0, 0.0, 1.0]


class RerunMultiOpSubscriber:
    def __init__(self, dataroot: Path, domain: int,
                  debug: bool = False) -> None:
        self.dataroot = dataroot
        self.debug = debug
        # StreamSubscriber hands us ``(type_name, topic, payload, stamp_ns)``
        # per typed sample; we route those through an inbox queue so the
        # rendering thread processes them serially under Rerun's main thread.
        self.inbox: "queue.Queue[tuple]" = queue.Queue()
        self.subscriber = None
        self._domain = domain
        # Per-operator ego trails. Bounded so we don't grow unboundedly
        # over a long demo run.
        self._trails: Dict[str, List[List[float]]] = {}
        self._trail_max = 200  # ~20 s at 10 Hz
        # Once-per-operator flag so Announce updates don't spam the log
        # with identical static circles every 5 s.
        self._coverage_logged: set = set()
        # Per-msg-type arrival counter for --debug mode. Helps diagnose
        # "I don't see PlannedTrajectory in the viewer" by confirming
        # whether the envelope is even reaching the subscriber.
        self._msg_counts: Dict[str, int] = {}
        self._next_debug_print = 0.0
        self._sample_payloads: Dict[str, bool] = {}

    def _on_message(self, type_name: str, topic: str,
                    payload: Dict[str, Any], stamp_ns: int) -> None:
        if not topic.startswith("spatialdds/"):
            return
        self.inbox.put((type_name, topic, payload))

    def _on_announce(self, service_id: str, announce: Dict[str, Any]) -> None:
        # Coverage circles come from the announce itself, not from a data
        # topic — which is what discovery is for.
        self.inbox.put((ANNOUNCE_TYPE, "spatialdds/discovery/announce/v1",
                        announce))

    def start(self) -> None:
        import threading

        from cyclonedds.domain import DomainParticipant

        from spatialdds_demo.stream import StreamSubscriber

        self.subscriber = StreamSubscriber(
            DomainParticipant(self._domain), self._on_message,
            on_announce=self._on_announce,
        )
        self._stop = threading.Event()

        def _pump() -> None:
            while not self._stop.is_set():
                self.subscriber.poll()
                _t.sleep(0.02)

        self._pump_thread = threading.Thread(target=_pump, daemon=True)
        self._pump_thread.start()

    def stop(self) -> None:
        stop = getattr(self, "_stop", None)
        if stop is not None:
            stop.set()
        thread = getattr(self, "_pump_thread", None)
        if thread is not None:
            thread.join(timeout=2)

    def spin(self, max_frames: int = 0) -> None:
        import time as _time
        seen = 0
        frame_num = 0
        while True:
            msg_type, topic, payload = self.inbox.get()
            frame_num += 1
            self._msg_counts[msg_type] = self._msg_counts.get(msg_type, 0) + 1
            if self.debug:
                # First payload per msg_type — raw JSON, truncated. Lets
                # us spot publisher-vs-handler key mismatches at a
                # glance ("handler reads payload['pose']['t'] but the
                # publisher emits payload['position']").
                if msg_type not in self._sample_payloads:
                    self._sample_payloads[msg_type] = True
                    blob = json.dumps(payload, default=str)
                    if len(blob) > 800:
                        blob = blob[:800] + "…"
                    print(f"FIRST {msg_type} from {topic} payload={blob}",
                          file=sys.stderr)
                if msg_type in ("planned_trajectory", "spatial_event",
                                "entity_binding", ANNOUNCE_TYPE):
                    suffix = ""
                    if msg_type == "spatial_event":
                        suffix = f" type={payload.get('type', '?')}"
                    print(f"GOT {msg_type} from {topic}{suffix}",
                          file=sys.stderr)
                now = _time.monotonic()
                if now >= self._next_debug_print:
                    self._next_debug_print = now + 5.0
                    print("[rerun] msg_counts: " +
                          ", ".join(f"{k}={v}" for k, v
                                    in sorted(self._msg_counts.items())),
                          file=sys.stderr)
            operator = operator_from_topic(topic) or "unknown"
            handle_sample(self, msg_type, topic, operator, payload, frame_num)
            if msg_type in {"oarc.framed_pose", "oarc.detection3d_velocity",
                            "oarc.fused_track"}:
                seen += 1
            if max_frames > 0 and seen >= max_frames:
                return

    # ── Existing handlers ────────────────────────────────────────────

    def _handle_ego(self, operator: str, payload: Dict[str, Any],
                     frame_num: int) -> None:
        if self.debug:
            print(f"RENDER ego_pose: {operator} frame={frame_num}", file=sys.stderr)
        _set_time(payload.get("stamp", {}), frame_num)
        # nuScenes publisher uses ``pose_se3``; the synthetic multi-op
        # publisher uses ``pose``. Accept either so the same Rerun
        # subscriber works against both.
        # FramedPose wraps the pose in a frame; the nuScenes publisher still
        # emits a bare pose_se3. Unwrap whichever arrived.
        pose = payload.get("pose_se3") or payload.get("pose") or {}
        if isinstance(pose.get("pose"), dict):
            pose = pose["pose"]
        x, y, z = _xyz(pose.get("t"))
        q = pose.get("q")
        if self.debug:
            print(f"EGO COORDS: op={operator} x={x:.1f} y={y:.1f} z={z:.1f} radius=4.0",
                  file=sys.stderr)
        rr.log(
            f"world/{operator}/ego_vehicle",
            rr.Transform3D(
                translation=[x, y, z],
                rotation=rr.Quaternion(xyzw=_q_xyzw(q)),
                relation=rr.TransformRelation.ParentFromChild,
            ),
            static=True,
        )
        # Labelled ego marker so each operator is identifiable from the
        # top-down view.
        rr.log(
            f"world/{operator}/ego",
            rr.Points3D([[x, y, z]],
                          radii=[4.0],
                          colors=[_operator_color(operator)],
                          labels=[operator]),
            static=True,
        )
        # Accumulate trail and re-log the bounded slice. Without a
        # cumulative trail you can't tell which way an operator came
        # from at a glance.
        trail = self._trails.setdefault(operator, [])
        trail.append([x, y, z])
        if len(trail) > self._trail_max:
            del trail[: len(trail) - self._trail_max]
        if len(trail) >= 2:
            rr.log(
                f"world/{operator}/trail",
                rr.LineStrips3D(
                    [np.array(trail)],
                    colors=[_operator_color(operator, alpha=120)],
                    radii=0.8,
                ),
                static=True,
            )
        geo = payload.get("geopose")
        if geo:
            rr.log(
                f"world/{operator}/ego_vehicle",
                rr.GeoPoints(lat_lon=[(float(geo["lat_deg"]), float(geo["lon_deg"]))]),
                static=True,
            )

    def _handle_vision_meta(self, operator: str, payload: Dict[str, Any],
                              frame_num: int) -> None:
        cam = payload.get("cam", {})
        stream = payload.get("stream_id", "camera")
        rr.log(
            f"world/{operator}/ego_vehicle/{stream}",
            rr.Pinhole(
                resolution=[int(cam.get("width", 1600)), int(cam.get("height", 900))],
                focal_length=[float(cam.get("fx", 1.0)), float(cam.get("fy", 1.0))],
                principal_point=[float(cam.get("cx", 0.0)), float(cam.get("cy", 0.0))],
            ),
            static=True,
        )

    def _handle_vision_frame(self, operator: str, payload: Dict[str, Any],
                                frame_num: int) -> None:
        hdr = payload.get("hdr", {})
        _set_time(hdr.get("t_start", {}), frame_num)
        stream = payload.get("stream_id", "camera")
        blobs = hdr.get("blobs", [])
        if not blobs:
            return
        path = self.dataroot / blobs[0].get("blob_id", "")
        if path.exists():
            rr.log(f"world/{operator}/ego_vehicle/{stream}",
                   rr.EncodedImage(path=str(path)), static=True)

    def _handle_lidar(self, operator: str, payload: Dict[str, Any],
                        frame_num: int) -> None:
        hdr = payload.get("hdr", {})
        _set_time(hdr.get("t_start", {}), frame_num)
        blobs = hdr.get("blobs", [])
        if not blobs:
            return
        path = self.dataroot / blobs[0].get("blob_id", "")
        if not path.exists():
            return
        arr = np.fromfile(str(path), dtype=np.float32)
        if arr.size % 5 != 0:
            return
        points = arr.reshape((-1, 5))[:, :3]
        rr.log(f"world/{operator}/ego_vehicle/LIDAR_TOP",
               rr.Points3D(points, colors=_operator_color(operator)),
               static=True)

    def _handle_radar(self, operator: str, payload: Dict[str, Any],
                        frame_num: int) -> None:
        _set_time(payload.get("stamp", {}), frame_num)
        dets = payload.get("dets") or payload.get("detections") or []
        pts = [_xyz(d.get("xyz_m")) for d in dets]
        if pts:
            stream = payload.get("stream_id", "RADAR")
            rr.log(f"world/{operator}/ego_vehicle/{stream}",
                   rr.Points3D(np.array(pts), colors=_operator_color(operator)),
                   static=True)

    def _handle_det3d(self, operator: str, payload: Dict[str, Any],
                        frame_num: int) -> None:
        _set_time(payload.get("stamp", {}), frame_num)
        # OperatorDetectionSet composes the spec Detection3D, so lift it out.
        dets = [d.get("detection", d) if isinstance(d, dict) else d
                for d in (payload.get("dets") or payload.get("detections") or [])]
        if self.debug:
            print(f"RENDER detections: {operator}, {len(dets)} dets frame={frame_num}",
                  file=sys.stderr)
        if not dets:
            return
        centers, sizes, quats, labels, colors = [], [], [], [], []
        base = _operator_color(operator)
        for d in dets:
            centers.append(_xyz(d.get("center")))
            # Rerun expects Boxes3D sizes as half-extents-ish (x, y, z)
            # — the prior code was permuting axes to match nuScenes
            # convention. Keep the same permutation for compatibility
            # with the existing per-operator detection look.
            sx, sy, sz = _xyz(d.get("size"), (2.0, 2.0, 1.5))
            sizes.append([sx, sz, sy])
            quats.append(_q_xyzw(d.get("q")))
            cls = str(d.get("class_id", "unknown"))
            score = float(d.get("score", 0.0))
            labels.append(f"{cls} {score:.0%}")
            alpha = int(60 + 180 * max(0.0, min(1.0, score)))
            colors.append(base + [alpha])
        if self.debug and centers:
            print(f"DET COORDS: op={operator} "
                  f"x={centers[0][0]:.1f} y={centers[0][1]:.1f} z={centers[0][2]:.1f} "
                  f"size={sizes[0]}", file=sys.stderr)
        rr.log(
            f"world/{operator}/detections",
            rr.Boxes3D(
                centers=np.array(centers),
                sizes=np.array(sizes),
                quaternions=np.array(quats, dtype=np.float32),
                labels=labels,
                colors=colors,
                fill_mode=rr.components.FillMode.MajorWireframe,
            ),
            static=True,
        )

    def _handle_fused_tracks(self, payload: Dict[str, Any],
                                frame_num: int) -> None:
        _set_time(payload.get("stamp", {}), frame_num)
        tracks = payload.get("tracks", [])
        if self.debug:
            print(f"RENDER fused_tracks: {len(tracks)} tracks frame={frame_num}",
                  file=sys.stderr)
        if not tracks:
            rr.log("world/fused/tracks", rr.Clear(recursive=False), static=True)
            return
        centers, sizes, labels, colors = [], [], [], []
        for t in tracks:
            centers.append(_xyz(t.get("position")))
            # At least [5, 2.5, 2] so fused tracks remain visible at the
            # 120 m bird's-eye altitude.
            sizes.append([5.0, 2.5, 2.0])
            ops = t.get("source_operators", [])
            cls = t.get("object_class", "?")
            n = t.get("source_count", 0)
            confidence = float(t.get("confidence", 0.0))
            op_str = "+".join(ops) if ops else "?"
            labels.append(f"{cls} [{op_str}] n={n}")
            alpha = int(120 + 135 * max(0.0, min(1.0, confidence)))
            colors.append(FUSED_COLOR + [alpha])
        if self.debug and centers:
            print(f"FUSED COORDS: x={centers[0][0]:.1f} "
                  f"y={centers[0][1]:.1f} z={centers[0][2]:.1f}",
                  file=sys.stderr)
        rr.log(
            "world/fused/tracks",
            rr.Boxes3D(
                centers=np.array(centers),
                sizes=np.array(sizes),
                labels=labels,
                colors=colors,
                fill_mode=rr.components.FillMode.MajorWireframe,
            ),
            static=True,
        )

    def _handle_coverage(self, payload: Dict[str, Any],
                            frame_num: int) -> None:
        _set_time(payload.get("stamp", {}), frame_num)
        # FusionCoverage names each metric as a field; the nuScenes publisher
        # still nests them under `metrics`.
        m = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
        if self.debug:
            print(f"RENDER coverage: {m.get('track_count', 0)} tracks "
                  f"frame={frame_num}", file=sys.stderr)
        per_op = m.get("per_operator_track_count") or []
        if isinstance(per_op, dict):                 # legacy JSON-object form
            per_op = [{"operator_id": k, "track_count": v}
                      for k, v in sorted(per_op.items())]
        per_op_str = ", ".join(
            f"{r.get('operator_id')}:{r.get('track_count')}" for r in per_op) or "—"
        rr.log("fusion/metrics", rr.TextLog(
            f"tracks={m.get('track_count', 0)} "
            f"multi_source={m.get('multi_source_count', 0)} "
            f"({m.get('multi_source_pct', 0.0) * 100:.0f}%) "
            f"improvement={m.get('coverage_improvement', 0.0):.2f}x "
            f"per_op=[{per_op_str}]"
        ))

    # ── v1.6 handlers ────────────────────────────────────────────────

    def _handle_planned_trajectory(self, operator: str,
                                     payload: Dict[str, Any],
                                     frame_num: int) -> None:
        """Render the upcoming waypoints as a semi-transparent line strip
        ahead of the ego, plus uncertainty circles (radius from
        ``position_uncertainty_m``) and a brighter goal marker."""
        _set_time(payload.get("stamp", {}), frame_num)
        waypoints = payload.get("waypoints") or []
        if self.debug:
            print(f"RENDER plan: {operator}, {len(waypoints)} waypoints",
                  file=sys.stderr)
        if not waypoints:
            return
        positions: List[List[float]] = []
        radii: List[float] = []
        for wp in waypoints:
            positions.append(_xyz((wp.get("pose") or {}).get("t")))
            if wp.get("has_uncertainty") and "position_uncertainty_m" in wp:
                radii.append(float(wp["position_uncertainty_m"]))
            else:
                radii.append(1.0)

        agent = payload.get("agent_id", f"{operator}_ego")
        path_color = _operator_color(operator, alpha=140)
        uncertainty_color = _operator_color(operator, alpha=50)
        goal_color = _operator_color(operator, alpha=220)
        if self.debug and positions:
            print(f"PLAN COORDS: op={operator} "
                  f"x={positions[0][0]:.1f} y={positions[0][1]:.1f} "
                  f"z={positions[0][2]:.1f}", file=sys.stderr)
        rr.log(
            f"world/{operator}/plan/{agent}",
            rr.LineStrips3D(
                [np.array(positions)],
                colors=[path_color],
                radii=1.0,
                labels=[f"{agent} plan"],
            ),
            static=True,
        )
        rr.log(
            f"world/{operator}/plan/{agent}/uncertainty",
            rr.Points3D(np.array(positions),
                          radii=np.array(radii, dtype=np.float32) * 2.0,
                          colors=[uncertainty_color]),
            static=True,
        )
        # Goal marker at the last waypoint.
        rr.log(
            f"world/{operator}/plan/{agent}/goal",
            rr.Points3D(np.array([positions[-1]]),
                          radii=[3.0],
                          colors=[goal_color],
                          labels=["goal"]),
            static=True,
        )

    def _handle_spatial_event(self, payload: Dict[str, Any],
                                 frame_num: int) -> None:
        """trajectory_conflict events get a bright red marker plus a
        WARN-level entry in the events log so the timeline highlights
        them."""
        event_type = payload.get("type", "OTHER")
        if self.debug:
            print(f"RENDER spatial_event: {event_type} frame={frame_num}",
                  file=sys.stderr)
        _set_time(payload.get("stamp", {}), frame_num)
        # A predicted trajectory conflict is published as PROXIMITY_ALERT —
        # the nearest registered EventType; 1.7 has no predicted-conflict
        # type. The event_id carries the pair, since SpatialEvent has no
        # typed slot for "these two agents" and MetaKV is a JSON string.
        if event_type != "PROXIMITY_ALERT":
            rr.log("events/log", rr.TextLog(
                f"{event_type}: {payload.get('description') or payload.get('event_id', '')}"[:200],
                level=rr.TextLogLevel.INFO))
            return
        pos = payload.get("position") if payload.get("has_position") else None
        agents = [a for a in str(payload.get("event_id", "")).split(":")[-1].split("|") if a]
        dist = (float(payload.get("min_distance_m", 0.0))
                if "min_distance_m" in payload
                else float(payload.get("measured_distance_m", 0.0)))
        px, py, pz = _xyz(pos)
        center = [px, py, pz + 1.0]
        agents_str = " × ".join(agents) if agents else "?"
        description = payload.get("description") or ""
        marker_label = f"⚠ CONFLICT {agents_str}"
        rr.log(
            "world/fused/conflicts",
            rr.Points3D(np.array([center]),
                         radii=8.0,
                         colors=[CONFLICT_COLOR + [200]],
                         labels=[marker_label]),
            static=True,
        )
        rr.log("events/log", rr.TextLog(
            f"CONFLICT: {agents_str} | dist={dist:.1f}m | "
            f"pos=({px:.0f}, {py:.0f}) | {description}",
            level=rr.TextLogLevel.WARN,
        ))

    def _handle_entity_binding(self, payload: Dict[str, Any],
                                  frame_num: int) -> None:
        if self.debug:
            print(f"RENDER binding: {payload.get('entity_id', '?')} "
                  f"comps={len(payload.get('components') or [])} "
                  f"frame={frame_num}", file=sys.stderr)
        """Each EntityBinding pins together one fused track and its
        contributing detections. We don't have a position cache for the
        component refs, so we just place a small grey marker at the
        binding's pose and label it with the source operators it pulled
        from. Click → see (topic, key) provenance in the inspector."""
        _set_time(payload.get("stamp", {}), frame_num)
        if not payload.get("has_pose", False):
            return
        # EntityBinding.pose is a FramedPose, so the PoseSE3 is one level in.
        framed = payload.get("pose") or {}
        pose = framed.get("pose") if isinstance(framed.get("pose"), dict) else framed
        bx, by, bz = _xyz(pose.get("t"))
        center = [bx, by, bz + 0.2]
        comps = payload.get("components") or []
        sources = []
        for c in comps:
            topic = c.get("topic") or ""
            if "/sensing/detection3d/" in topic:
                op = topic.split("/")[1]
                sources.append(op)
        sources_str = ", ".join(sorted(set(sources))) or "(none)"
        entity_id = payload.get("entity_id", "unknown")
        label = f"{entity_id} ← {sources_str}"
        rr.log(
            f"world/fused/bindings/{entity_id}",
            rr.Points3D(np.array([center]),
                         colors=[BINDING_COLOR + [120]],
                         radii=0.5,
                         labels=[label]),
            static=True,
        )
        # Verbose attribution in the events log (TRACE so it doesn't
        # drown the conflict warnings out).
        rr.log("events/log", rr.TextLog(
            f"binding {entity_id} ({payload.get('entity_class', '?')}): "
            f"{len(comps)} components from [{sources_str}]",
            level=rr.TextLogLevel.TRACE,
        ))

    def _handle_announce(self, operator: str, payload: Dict[str, Any],
                            frame_num: int) -> None:
        if self.debug:
            print(f"RENDER announce: {operator} frame={frame_num}",
                  file=sys.stderr)
        """Coverage geometry as a flat circle on z=0. Logged ``static``
        the first time we see it per operator so a 5 s republish doesn't
        keep stamping the same circle into every frame.

        The rendered radius is clamped to ``COVERAGE_RENDER_MAX_M`` so
        Rerun's Spatial3DView auto-fit doesn't zoom out to the 200 m
        infrastructure radius and shrink the actual ±30 m crossing
        action to a dot. The label still carries the true radius."""
        # Announce arrives on the well-known discovery topic, so the service
        # names itself rather than the topic naming it.
        operator = payload.get("name") or operator
        if operator in self._coverage_logged:
            return

        # Announce.coverage is a sequence of CoverageElement. There is no
        # circle in CoverageElement, so a circular footprint is published as
        # its bounding aabb in local metres and read back as centre +
        # half-width — the same convention circle_coverage() writes.
        elements = payload.get("coverage") or []
        aabb = next((e.get("aabb") for e in elements
                     if isinstance(e, dict) and e.get("has_aabb")), None)
        if not aabb:
            return
        lo = _xyz(aabb.get("min_xyz"))
        hi = _xyz(aabb.get("max_xyz"))
        cx, cy, cz = ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2])
        true_radius = max((hi[0] - lo[0]) / 2.0, 1.0)
        self._coverage_logged.add(operator)
        render_radius = min(true_radius, COVERAGE_RENDER_MAX_M)
        n = 64
        ring = [[cx + render_radius * math.cos(2.0 * math.pi * i / n),
                  cy + render_radius * math.sin(2.0 * math.pi * i / n),
                  cz] for i in range(n + 1)]
        rendered_str = (f" — rendered at {render_radius:.0f}m"
                        if render_radius < true_radius else "")
        rr.log(
            f"world/{operator}/coverage",
            rr.LineStrips3D(
                [np.array(ring)],
                colors=[_operator_color(operator, alpha=60)],
                radii=0.6,
                labels=[f"{operator} coverage ({true_radius:.0f}m{rendered_str})"],
            ),
            static=True,
        )


def handle_sample(sub: "RerunMultiOpSubscriber", type_name: str,
                  topic: str, operator: str,
                  payload: Dict[str, Any],
                  frame_num: int) -> None:
    """
    Route one typed sample to its handler, by its announced 3.3.2 type.

    Kept module-level so the smoke test can drive each handler without
    instantiating DDS. ``frame_num`` is threaded into every handler so each
    sets its own timeline before any rr.log call (no shared mutable counter).

    Routing on the announced type is the substantive change from the
    envelope: ``msg_type`` was a demo-private label, so every consumer kept
    its own table of aliases — this dispatcher carried three spellings of
    "a detection set". The type name is the publisher's declared contract,
    and it is the same string the announce advertises.
    """
    handler = _HANDLERS.get(type_name)
    if handler is None:
        return
    handler(sub, operator, payload, frame_num)


_HANDLERS = {
    "oarc.framed_pose": lambda s, op, p, n: s._handle_ego(op, p, n),
    "geopose": lambda s, op, p, n: s._handle_ego(op, p, n),
    "oarc.video_frame_meta": lambda s, op, p, n: s._handle_vision_meta(op, p, n),
    "video_frame": lambda s, op, p, n: s._handle_vision_frame(op, p, n),
    "oarc.lidar_frame": lambda s, op, p, n: s._handle_lidar(op, p, n),
    "radar_detection": lambda s, op, p, n: s._handle_radar(op, p, n),
    "oarc.detection3d_velocity": lambda s, op, p, n: s._handle_det3d(op, p, n),
    "oarc.fused_track": lambda s, op, p, n: s._handle_fused_tracks(p, n),
    "oarc.fusion_coverage": lambda s, op, p, n: s._handle_coverage(p, n),
    "planned_trajectory": lambda s, op, p, n: s._handle_planned_trajectory(op, p, n),
    "spatial_event": lambda s, op, p, n: s._handle_spatial_event(p, n),
    "entity_binding": lambda s, op, p, n: s._handle_entity_binding(p, n),
    ANNOUNCE_TYPE: lambda s, op, p, n: s._handle_announce(op, p, n),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-operator SpatialDDS Rerun subscriber")
    p.add_argument("--dataroot", default="/tmp",
                   help="nuScenes dataroot (only read for vision/lidar blobs; "
                         "the synthetic publisher doesn't emit these).")
    p.add_argument("--domain", type=int, default=0)
    p.add_argument("--app-id", default="spatialdds_multi_op_fusion")
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--spawn-viewer", action="store_true",
                   help="Spawn a fresh native Rerun viewer window.")
    p.add_argument("--connect-grpc", default="",
                   help="Connect to a running Rerun gRPC endpoint "
                        "(e.g. rerun+http://127.0.0.1:9876/proxy)")
    p.add_argument("--serve-web", action="store_true",
                   help="Host an integrated Rerun web viewer in this process. "
                        "Browse to http://localhost:9090.")
    p.add_argument("--web-viewer-port", type=int, default=9090)
    p.add_argument("--grpc-port", type=int, default=9876)
    p.add_argument("--debug", action="store_true",
                   help="Print per-msg-type arrival counts every 5 s plus a "
                        "GOT line for each such envelope.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    rr.init(args.app_id, spawn=args.spawn_viewer)
    if args.connect_grpc:
        rr.connect_grpc(args.connect_grpc)
    if args.serve_web:
        rr.serve_web(
            grpc_port=args.grpc_port,
            web_port=args.web_viewer_port,
            open_browser=False,
        )
        print(f"[rerun] web viewer at http://localhost:{args.web_viewer_port}",
              file=sys.stderr)

    # Z-up coordinate hint puts the bird's-eye view in the natural
    # orientation when the user picks the ``Top-down`` camera preset.
    rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)

    # No blueprint — let Rerun auto-discover view layout based on the
    # archetypes that arrive. With a manually-set blueprint, a single
    # misconfigured panel can hide every entity behind it; auto-layout
    # always produces a Spatial3DView for /world contents.

    rr.log("description", rr.TextDocument(
        "# SpatialDDS 1.6 — Multi-Operator Fusion\n\n"
        "Three vehicle operators (a/b/c) plus a roadside radar "
        "(infrastructure) sharing Detection3D on a SpatialDDS bus.\n\n"
        "**wire types added in v1.6, rendered here:**\n"
        "* `PlannedTrajectory` — semi-transparent path ahead of each ego\n"
        "* trajectory-conflict `SpatialEvent` — red marker + warn log\n"
        "* `EntityBinding` — grey markers with source provenance label\n"
        "* `Announce` — coverage circles per operator (static, faint)\n",
        media_type=rr.MediaType.MARKDOWN,
    ), static=True)

    sub = RerunMultiOpSubscriber(Path(args.dataroot), args.domain,
                                    debug=args.debug)
    sub.start()
    try:
        sub.spin(max_frames=args.max_frames)
    except KeyboardInterrupt:
        pass
    finally:
        sub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
