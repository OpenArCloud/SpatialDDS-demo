#!/usr/bin/env python3
"""Rerun subscriber for the multi-operator fusion demo.

Routes each operator's streams under ``world/{operator}/…`` and the
platform fuser's output under ``world/fused/…``, giving a top-down
intersection view of who-sees-what versus the unified fusion output.

Subscribed topics (matched by ``logical_topic`` prefix / ``msg_type``):
  * spatialdds/{operator}/ego/pose/v1                 NUSC_EGO_POSE
  * spatialdds/{operator}/vision/{cam}/frame/v1       NUSC_VISION_{META,FRAME}
  * spatialdds/{operator}/lidar/{ch}/frame/v1         NUSC_LIDAR_{META,FRAME}
  * spatialdds/{operator}/rad/{ch}/frame/v1           NUSC_RAD_DET_SET
  * spatialdds/{operator}/sensing/detection3d/v1      NUSC_DET3D_SET
  * spatialdds/infrastructure/sensing/detection3d/v1  INFRA_DET3D_SET
  * spatialdds/platform/fusion/track/v1               NUSC_FUSED_TRACK_SET
  * spatialdds/platform/fusion/coverage/v1            NUSC_FUSION_COVERAGE

  v1.6 additions:
  * spatialdds/{operator}/plan/{agent}/trajectory/v1  PlannedTrajectory
  * spatialdds/platform/events/trajectory_conflict/v1 SpatialEvent
  * spatialdds/platform/entity/binding/v1             EntityBinding
  * spatialdds/{operator}/discovery/announce/v1       Announce
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

import numpy as np
import rerun as rr
import rerun.blueprint as rrb

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

# Lossless RELIABLE+KEEP_ALL reader from bridges/envelope_io. The legacy
# EnvelopeTransport's BEST_EFFORT+KEEP_LAST(1) reader collapsed bursts —
# with one publisher writing 9+ topics back-to-back per tick, the
# subscriber would only see one or two of them.
from bridges.envelope_io import EnvelopeSubscriber  # noqa: E402
from routing import operator_from_topic  # noqa: E402


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


def _set_time(stamp: Dict[str, Any]) -> None:
    """Set the Rerun timeline. ``rr.set_time_nanos`` was deprecated in
    0.23; use the new ``rr.set_time(timestamp=…)`` form so the warning
    doesn't spam every frame."""
    ns = _stamp_ns(stamp)
    rr.set_time("timestamp", duration=ns / 1e9)


def _q_xyzw(q: Dict[str, float]) -> List[float]:
    return [float(q.get("x", 0.0)), float(q.get("y", 0.0)),
            float(q.get("z", 0.0)), float(q.get("w", 1.0))]


def _create_blueprint() -> rrb.Blueprint:
    """Programmatic blueprint so the viewer opens with the demo layout
    every time. A wide 3D view (the intersection) plus a narrow column
    of fusion metrics; events log along the bottom.

    Camera/eye positioning isn't directly settable on Spatial3DView
    across all Rerun versions, so we lean on ``ViewCoordinates`` +
    Z-up to give the viewer a sensible default frame and let the user
    rotate / zoom from there.
    """
    return rrb.Blueprint(
        rrb.Vertical(
            rrb.Horizontal(
                rrb.Spatial3DView(
                    name="Intersection",
                    origin="/world",
                ),
                rrb.TextLogView(name="Fusion Metrics", origin="/fusion"),
                column_shares=[3, 1],
            ),
            rrb.TextLogView(name="Events", origin="/events"),
            row_shares=[3, 1],
        ),
    )


class RerunMultiOpSubscriber:
    def __init__(self, dataroot: Path, domain: int,
                  debug: bool = False) -> None:
        self.dataroot = dataroot
        self.debug = debug
        # The lossless EnvelopeSubscriber decodes JSON and hands us
        # ``(msg_type, topic, payload, stamp_ns)`` directly; we route
        # those through an inbox queue so the rendering thread can
        # process them serially under Rerun's main thread.
        self.inbox: "queue.Queue[tuple]" = queue.Queue()
        self.subscriber = EnvelopeSubscriber(
            domain_id=domain, callback=self._on_message,
        )
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

    def _on_message(self, msg_type: str, topic: str,
                    payload: Dict[str, Any], stamp_ns: int) -> None:
        if not topic.startswith("spatialdds/"):
            return
        self.inbox.put((msg_type, topic, payload))

    def start(self) -> None:
        self.subscriber.start()

    def stop(self) -> None:
        self.subscriber.stop()

    def spin(self, max_frames: int = 0) -> None:
        import time as _time
        seen = 0
        while True:
            msg_type, topic, payload = self.inbox.get()
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
                if msg_type in ("PlannedTrajectory", "SpatialEvent",
                                  "EntityBinding", "Announce"):
                    suffix = ""
                    if msg_type == "SpatialEvent":
                        suffix = f" event_type={payload.get('event_type', '?')}"
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
            handle_envelope(self, msg_type, topic, operator, payload)
            if msg_type in {"NUSC_EGO_POSE", "NUSC_DET3D_SET",
                              "NUSC_FUSED_TRACK_SET"}:
                seen += 1
            if max_frames > 0 and seen >= max_frames:
                return

    # ── Existing handlers ────────────────────────────────────────────

    def _handle_ego(self, operator: str, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        # nuScenes publisher uses ``pose_se3``; the synthetic multi-op
        # publisher uses ``pose``. Accept either so the same Rerun
        # subscriber works against both.
        pose = payload.get("pose_se3") or payload.get("pose") or {}
        t = pose.get("t", {})
        q = pose.get("q", {})
        x = float(t.get("x", 0.0))
        y = float(t.get("y", 0.0))
        z = float(t.get("z", 0.0))
        rr.log(
            f"world/{operator}/ego_vehicle",
            rr.Transform3D(
                translation=[x, y, z],
                rotation=rr.Quaternion(xyzw=_q_xyzw(q)),
                relation=rr.TransformRelation.ParentFromChild,
            ),
        )
        # Labelled ego marker so each operator is identifiable from the
        # top-down view.
        rr.log(
            f"world/{operator}/ego",
            rr.Points3D([[x, y, z]],
                          radii=[4.0],
                          colors=[_operator_color(operator)],
                          labels=[operator]),
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
            )
        geo = payload.get("geopose")
        if geo:
            rr.log(
                f"world/{operator}/ego_vehicle",
                rr.GeoPoints(lat_lon=[(float(geo["lat_deg"]), float(geo["lon_deg"]))]),
            )

    def _handle_vision_meta(self, operator: str, payload: Dict[str, Any]) -> None:
        cam = payload.get("cam", {})
        stream = payload.get("stream_id", "camera")
        rr.log(
            f"world/{operator}/ego_vehicle/{stream}",
            rr.Pinhole(
                resolution=[int(cam.get("width", 1600)), int(cam.get("height", 900))],
                focal_length=[float(cam.get("fx", 1.0)), float(cam.get("fy", 1.0))],
                principal_point=[float(cam.get("cx", 0.0)), float(cam.get("cy", 0.0))],
            ),
        )

    def _handle_vision_frame(self, operator: str, payload: Dict[str, Any]) -> None:
        hdr = payload.get("hdr", {})
        _set_time(hdr.get("t_start", {}))
        stream = payload.get("stream_id", "camera")
        blobs = hdr.get("blobs", [])
        if not blobs:
            return
        path = self.dataroot / blobs[0].get("blob_id", "")
        if path.exists():
            rr.log(f"world/{operator}/ego_vehicle/{stream}", rr.EncodedImage(path=str(path)))

    def _handle_lidar(self, operator: str, payload: Dict[str, Any]) -> None:
        hdr = payload.get("hdr", {})
        _set_time(hdr.get("t_start", {}))
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
               rr.Points3D(points, colors=_operator_color(operator)))

    def _handle_radar(self, operator: str, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        dets = payload.get("detections", [])
        pts = [[float(d.get("xyz_m", {}).get("x", 0.0)),
                float(d.get("xyz_m", {}).get("y", 0.0)),
                float(d.get("xyz_m", {}).get("z", 0.0))] for d in dets]
        if pts:
            stream = payload.get("stream_id", "RADAR")
            rr.log(f"world/{operator}/ego_vehicle/{stream}",
                   rr.Points3D(np.array(pts), colors=_operator_color(operator)))

    def _handle_det3d(self, operator: str, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        dets = payload.get("detections", [])
        if not dets:
            return
        centers, sizes, quats, labels, colors = [], [], [], [], []
        base = _operator_color(operator)
        for d in dets:
            c = d.get("center", {})
            s = d.get("size") or {"x": 2.0, "y": 2.0, "z": 1.5}
            q = d.get("q", {})
            centers.append([float(c.get("x", 0.0)),
                              float(c.get("y", 0.0)),
                              float(c.get("z", 0.0))])
            # Rerun expects Boxes3D sizes as half-extents-ish (x, y, z)
            # — the prior code was permuting axes to match nuScenes
            # convention. Keep the same permutation for compatibility
            # with the existing per-operator detection look.
            sizes.append([float(s.get("x", 0.0)),
                            float(s.get("z", 0.0)),
                            float(s.get("y", 0.0))])
            quats.append(_q_xyzw(q))
            cls = str(d.get("class_id", "unknown"))
            score = float(d.get("score", 0.0))
            labels.append(f"{cls} {score:.0%}")
            alpha = int(60 + 180 * max(0.0, min(1.0, score)))
            colors.append(base + [alpha])
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
        )

    def _handle_fused_tracks(self, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        tracks = payload.get("tracks", [])
        if not tracks:
            rr.log("world/fused/tracks", rr.Clear(recursive=False))
            return
        centers, sizes, labels, colors = [], [], [], []
        for t in tracks:
            p = t.get("position", {})
            centers.append([float(p.get("x", 0.0)),
                              float(p.get("y", 0.0)),
                              float(p.get("z", 0.0))])
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
        rr.log(
            "world/fused/tracks",
            rr.Boxes3D(
                centers=np.array(centers),
                sizes=np.array(sizes),
                labels=labels,
                colors=colors,
                fill_mode=rr.components.FillMode.MajorWireframe,
            ),
        )

    def _handle_coverage(self, payload: Dict[str, Any]) -> None:
        _set_time(payload.get("stamp", {}))
        m = payload.get("metrics", {})
        per_op = m.get("per_operator_track_count", {})
        per_op_str = ", ".join(f"{op}:{n}" for op, n in sorted(per_op.items())) or "—"
        rr.log("fusion/metrics", rr.TextLog(
            f"tracks={m.get('track_count', 0)} "
            f"multi_source={m.get('multi_source_count', 0)} "
            f"({m.get('multi_source_pct', 0.0) * 100:.0f}%) "
            f"improvement={m.get('coverage_improvement', 0.0):.2f}x "
            f"per_op=[{per_op_str}]"
        ))

    # ── v1.6 handlers ────────────────────────────────────────────────

    def _handle_planned_trajectory(self, operator: str,
                                     payload: Dict[str, Any]) -> None:
        """Render the upcoming waypoints as a semi-transparent line strip
        ahead of the ego, plus uncertainty circles (radius from
        ``position_uncertainty_m``) and a brighter goal marker."""
        _set_time(payload.get("stamp", {}))
        waypoints = payload.get("waypoints") or []
        if not waypoints:
            return
        positions: List[List[float]] = []
        radii: List[float] = []
        for wp in waypoints:
            pose_t = (wp.get("pose") or {}).get("t") or {}
            positions.append([float(pose_t.get("x", 0.0)),
                              float(pose_t.get("y", 0.0)),
                              float(pose_t.get("z", 0.0))])
            if wp.get("has_uncertainty") and "position_uncertainty_m" in wp:
                radii.append(float(wp["position_uncertainty_m"]))
            else:
                radii.append(1.0)

        agent = payload.get("agent_id", f"{operator}_ego")
        path_color = _operator_color(operator, alpha=140)
        uncertainty_color = _operator_color(operator, alpha=50)
        goal_color = _operator_color(operator, alpha=220)
        rr.log(
            f"world/{operator}/plan/{agent}",
            rr.LineStrips3D(
                [np.array(positions)],
                colors=[path_color],
                radii=1.0,
                labels=[f"{agent} plan"],
            ),
        )
        rr.log(
            f"world/{operator}/plan/{agent}/uncertainty",
            rr.Points3D(np.array(positions),
                          radii=np.array(radii, dtype=np.float32) * 2.0,
                          colors=[uncertainty_color]),
        )
        # Goal marker at the last waypoint.
        rr.log(
            f"world/{operator}/plan/{agent}/goal",
            rr.Points3D(np.array([positions[-1]]),
                          radii=[3.0],
                          colors=[goal_color],
                          labels=["goal"]),
        )

    def _handle_spatial_event(self, payload: Dict[str, Any]) -> None:
        """trajectory_conflict events get a bright red marker plus a
        WARN-level entry in the events log so the timeline highlights
        them."""
        _set_time(payload.get("stamp", {}))
        if payload.get("event_type") != "trajectory_conflict":
            rr.log("events/log", rr.TextLog(
                f"{payload.get('event_type', 'event')}: "
                f"{json.dumps(payload, default=str)[:200]}",
                level=rr.TextLogLevel.INFO))
            return
        pos = payload.get("conflict_position") or {}
        agents = payload.get("agents") or []
        ttc = payload.get("time_to_conflict")
        dist = float(payload.get("min_distance_m", 0.0))
        center = [float(pos.get("x", 0.0)),
                  float(pos.get("y", 0.0)),
                  float(pos.get("z", 0.0)) + 1.0]
        agents_str = " × ".join(agents) if agents else "?"
        ttc_str = f"{float(ttc):.1f}s" if isinstance(ttc, (int, float)) else "?"
        marker_label = f"⚠ CONFLICT {agents_str} in {ttc_str}"
        rr.log(
            "world/fused/conflicts",
            rr.Points3D(np.array([center]),
                         radii=8.0,
                         colors=[CONFLICT_COLOR + [200]],
                         labels=[marker_label]),
        )
        rr.log("events/log", rr.TextLog(
            f"CONFLICT: {agents_str} | dist={dist:.1f}m | ETA={ttc_str} | "
            f"pos=({float(pos.get('x', 0.0)):.0f}, "
            f"{float(pos.get('y', 0.0)):.0f})",
            level=rr.TextLogLevel.WARN,
        ))

    def _handle_entity_binding(self, payload: Dict[str, Any]) -> None:
        """Each EntityBinding pins together one fused track and its
        contributing detections. We don't have a position cache for the
        component refs, so we just place a small grey marker at the
        binding's pose and label it with the source operators it pulled
        from. Click → see (topic, key) provenance in the inspector."""
        _set_time(payload.get("stamp", {}))
        if not payload.get("has_pose", False):
            return
        pose_t = (payload.get("pose") or {}).get("t") or {}
        center = [float(pose_t.get("x", 0.0)),
                  float(pose_t.get("y", 0.0)),
                  float(pose_t.get("z", 0.0)) + 0.2]
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
        )
        # Verbose attribution in the events log (TRACE so it doesn't
        # drown the conflict warnings out).
        rr.log("events/log", rr.TextLog(
            f"binding {entity_id} ({payload.get('entity_class', '?')}): "
            f"{len(comps)} components from [{sources_str}]",
            level=rr.TextLogLevel.TRACE,
        ))

    def _handle_announce(self, operator: str, payload: Dict[str, Any]) -> None:
        """Coverage geometry as a flat circle on z=0. Logged ``static``
        the first time we see it per operator so a 5 s republish doesn't
        keep stamping the same circle into every frame.

        The rendered radius is clamped to ``COVERAGE_RENDER_MAX_M`` so
        Rerun's Spatial3DView auto-fit doesn't zoom out to the 200 m
        infrastructure radius and shrink the actual ±30 m crossing
        action to a dot. The label still carries the true radius."""
        cov = payload.get("coverage") or {}
        if cov.get("type") != "circle":
            return
        if operator in self._coverage_logged:
            return
        self._coverage_logged.add(operator)
        c = cov.get("center") or {}
        cx = float(c.get("x", 0.0))
        cy = float(c.get("y", 0.0))
        cz = float(c.get("z", 0.0))
        true_radius = float(cov.get("radius_m", 1.0))
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


def handle_envelope(sub: "RerunMultiOpSubscriber", msg_type: str,
                     topic: str, operator: str,
                     payload: Dict[str, Any]) -> None:
    """Module-level dispatcher — kept separate so the smoke test can
    drive each handler without instantiating DDS.

    Accepts both the legacy ``NUSC_*`` / ``INFRA_*`` msg_types the
    in-tree synthetic publisher emits AND the SpatialDDS standard names
    (``Detection3DSet``, ``FramedPose``, ``FusedTrackSet``,
    ``CoverageMetrics``) so a subscriber written against the standard
    schema also routes correctly."""
    if msg_type in ("FramedPose", "NUSC_EGO_POSE"):
        sub._handle_ego(operator, payload)
    elif msg_type == "NUSC_VISION_META":
        sub._handle_vision_meta(operator, payload)
    elif msg_type == "NUSC_VISION_FRAME":
        sub._handle_vision_frame(operator, payload)
    elif msg_type == "NUSC_LIDAR_FRAME":
        sub._handle_lidar(operator, payload)
    elif msg_type == "NUSC_RAD_DET_SET":
        sub._handle_radar(operator, payload)
    elif msg_type in ("Detection3DSet", "NUSC_DET3D_SET", "INFRA_DET3D_SET"):
        sub._handle_det3d(operator, payload)
    elif msg_type in ("FusedTrackSet", "NUSC_FUSED_TRACK_SET"):
        sub._handle_fused_tracks(payload)
    elif msg_type in ("CoverageMetrics", "NUSC_FUSION_COVERAGE"):
        sub._handle_coverage(payload)
    elif msg_type == "PlannedTrajectory":
        sub._handle_planned_trajectory(operator, payload)
    elif msg_type == "SpatialEvent":
        sub._handle_spatial_event(payload)
    elif msg_type == "EntityBinding":
        sub._handle_entity_binding(payload)
    elif msg_type == "Announce":
        sub._handle_announce(operator, payload)


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
                        "GOT line for each v1.6 envelope.")
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

    # Push the layout to the viewer so the operator doesn't have to
    # build it manually each session.
    rr.send_blueprint(_create_blueprint())

    # Z-up coordinate hint puts the bird's-eye view in the natural
    # orientation when the user picks the ``Top-down`` camera preset.
    rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)

    # Camera framing — pin a viewpoint above the centroid of the three
    # operator start positions (must match synthetic_publisher.EGO_PATHS:
    # A=(0,-30), B=(30,0), C=(30,-10)). 120 m altitude is high enough to
    # see all three converging on the (0,0) crossing without losing the
    # detail at meter scale.
    centroid_x = (0.0 + 30.0 + 30.0) / 3.0     # = 20.0
    centroid_y = (-30.0 + 0.0 + -10.0) / 3.0   # ≈ -13.33
    rr.log("world/viewpoint", rr.Transform3D(
        translation=[centroid_x, centroid_y, 120.0],
    ), static=True)

    rr.log("description", rr.TextDocument(
        "# SpatialDDS 1.6 — Multi-Operator Fusion\n\n"
        "Three vehicle operators (a/b/c) plus a roadside radar "
        "(infrastructure) sharing Detection3D on a SpatialDDS bus.\n\n"
        "**v1.6 wire types rendered here:**\n"
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
