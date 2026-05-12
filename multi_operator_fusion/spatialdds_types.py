"""SpatialDDS 1.6 dict-builder helpers.

The repo's wire format is JSON-on-the-envelope — every demo emits dicts,
not typed dataclasses (see ``nuscenes/dds_envelope_transport.py``). When
v1.6 added new core types (``PlannedTrajectory``, ``PlannedWaypoint``,
``EntityBinding``, ``ComponentRef``) we don't have native dataclasses
for them; we have these helpers, which return well-formed JSON-
serialisable dicts that match the IDL shape.

Lives under ``multi_operator_fusion/`` because that's the only consumer
in v0 (synthetic publisher + fusion service). If a second demo grows a
need for typed access, promote to a top-level ``spatialdds_types``.

Pure-Python — no DDS, no FastAPI, easy to unit-test.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence


SCHEMA_CORE = "spatial.core/1.6"


def _stamp(timestamp_s: float) -> Dict[str, int]:
    """Convert a float epoch seconds to a SpatialDDS Time dict."""
    sec = int(timestamp_s)
    nsec = int(round((timestamp_s - sec) * 1_000_000_000))
    if nsec >= 1_000_000_000:
        sec += 1
        nsec -= 1_000_000_000
    elif nsec < 0:
        sec -= 1
        nsec += 1_000_000_000
    return {"sec": sec, "nanosec": nsec}


def make_planned_waypoint(
    x: float, y: float, z: float,
    timestamp_s: float,
    *,
    vx: Optional[float] = None,
    vy: Optional[float] = None,
    vz: Optional[float] = None,
    qx: float = 0.0, qy: float = 0.0, qz: float = 0.0, qw: float = 1.0,
    uncertainty_m: Optional[float] = None,
    confidence: Optional[float] = None,
) -> Dict:
    """Build one ``PlannedWaypoint`` dict.

    Velocity, uncertainty, and confidence are optional — the
    ``has_velocity`` / ``has_uncertainty`` / ``has_confidence`` flags
    follow the SpatialDDS presence-flag convention. If a velocity
    component is omitted the others default to 0.
    """
    wp: Dict[str, object] = {
        "pose": {
            "t": {"x": float(x), "y": float(y), "z": float(z)},
            "q": {"x": float(qx), "y": float(qy), "z": float(qz), "w": float(qw)},
        },
        "stamp": _stamp(timestamp_s),
        "has_velocity": vx is not None,
        "has_uncertainty": uncertainty_m is not None,
        "has_confidence": confidence is not None,
    }
    if vx is not None:
        wp["velocity"] = {
            "x": float(vx),
            "y": float(vy or 0.0),
            "z": float(vz or 0.0),
        }
    if uncertainty_m is not None:
        wp["position_uncertainty_m"] = float(uncertainty_m)
    if confidence is not None:
        wp["confidence"] = float(confidence)
    return wp


def make_planned_trajectory(
    agent_id: str,
    plan_id: str,
    plan_revision: int,
    frame_ref_fqn: str,
    waypoints: Sequence[Dict],
    *,
    goal_pose: Optional[Dict] = None,
    horizon_sec: Optional[float] = None,
    replan_rate_hz: Optional[float] = None,
    timestamp_s: float = 0.0,
    frame_ref_uuid: str = "",
) -> Dict:
    """Build a ``PlannedTrajectory`` dict from a list of waypoints.

    ``frame_ref_fqn`` is required; ``frame_ref_uuid`` is optional and
    defaults to "" since the demo doesn't use UUIDs end-to-end yet.
    """
    traj: Dict[str, object] = {
        "schema_version": SCHEMA_CORE,
        "agent_id": str(agent_id),
        "plan_id": str(plan_id),
        "plan_revision": int(plan_revision),
        # v1.6 §2.12 — FrameRef carries an axis convention; all demo
        # code is ENU-anchored.
        "frame_ref": {
            "uuid": frame_ref_uuid,
            "fqn": frame_ref_fqn,
            "has_coord_convention": True,
            "coord_convention": "ENU",
        },
        "waypoints": list(waypoints),
        "has_goal_pose": goal_pose is not None,
        "has_horizon_sec": horizon_sec is not None,
        "has_replan_rate_hz": replan_rate_hz is not None,
        "stamp": _stamp(timestamp_s),
    }
    if goal_pose is not None:
        traj["goal_pose"] = goal_pose
    if horizon_sec is not None:
        traj["horizon_sec"] = float(horizon_sec)
    if replan_rate_hz is not None:
        traj["replan_rate_hz"] = float(replan_rate_hz)
    return traj


def make_component_ref(topic: str, key: str) -> Dict[str, str]:
    """Build a ``ComponentRef`` dict pointing at one (topic, key) cell."""
    return {"topic": str(topic), "key": str(key)}


def make_entity_binding(
    entity_id: str,
    entity_class: str,
    components: Sequence[Dict],
    *,
    pose: Optional[Dict] = None,
    source_id: str = "",
    timestamp_s: float = 0.0,
) -> Dict:
    """Build an ``EntityBinding`` dict that links one logical entity to
    every (topic, key) that contributes to it."""
    binding: Dict[str, object] = {
        "schema_version": SCHEMA_CORE,
        "entity_id": str(entity_id),
        "entity_class": str(entity_class),
        "components": list(components),
        "has_pose": pose is not None,
        "stamp": _stamp(timestamp_s),
        "source_id": str(source_id),
    }
    if pose is not None:
        binding["pose"] = pose
    return binding


SCHEMA_DISCOVERY = "spatial.discovery/1.6"


def make_announce(
    operator: str,
    *,
    service_kind: str,
    topics: Sequence[Dict],
    coverage: Optional[Dict] = None,
    timestamp_s: float = 0.0,
) -> Dict:
    """Build an ``Announce`` dict for the discovery topic.

    ``coverage`` is a free-form dict (typically ``{"type": "circle",
    "center": {"x", "y"}, "radius_m": …}`` or a polygon) — the dashboard
    just renders whatever shape it gets. ``topics`` is a list of
    ``{"topic": …, "msg_type": …}`` dicts the operator owns.
    """
    return {
        "schema_version": SCHEMA_DISCOVERY,
        "operator": str(operator),
        "service_kind": str(service_kind),
        "topics": list(topics),
        "has_coverage": coverage is not None,
        **({"coverage": coverage} if coverage is not None else {}),
        "stamp": _stamp(timestamp_s),
    }
