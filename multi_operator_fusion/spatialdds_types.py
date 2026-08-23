"""SpatialDDS 1.7 dict builders for the multi-operator demo.

Each function returns a dict that ``spatialdds_demo.json_mapping.from_json``
can build into its IDL type — so these are payload *constructors* for typed
topics, not a JSON wire format. The dicts exist because the demo's publishers
have always assembled payloads as dicts and the dashboards still want them
that way; the conversion to a real sample happens once, at the writer, and a
dict that is not a complete well-formed sample fails there.

Two conventions the IDL enforces and these helpers follow:

* **Vectors are arrays.** ``Vec3`` is ``double[3]``, so ``t`` is
  ``[x, y, z]``, not ``{"x":…}``.
* **Optionals are presence-flagged.** The spec pairs ``has_x`` with a value
  that is always present, so every field is emitted whatever the flag says
  and consumers read the flag rather than testing for null.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from spatialdds_demo import payloads


SCHEMA_CORE = "spatial.core/1.7"
SCHEMA_EVENTS = "spatial.events/1.7"


# The primitives and the spec-type builders live in spatialdds_demo.payloads
# so this module and the ROS 2 bridge cannot grow different ideas of what a
# Detection3D looks like. They did, and both were wrong.
_stamp = payloads.stamp


_vec = payloads.vec


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
        # Vec3 and QuaternionXYZW are IDL arrays, not {x,y,z} objects.
        "pose": {
            "t": [float(x), float(y), float(z)],
            "q": [float(qx), float(qy), float(qz), float(qw)],
        },
        "stamp": _stamp(timestamp_s),
        # Presence flags gate meaning, not presence: every member still
        # carries a value, so the struct can always be built.
        "has_velocity": vx is not None,
        "velocity": [float(vx or 0.0), float(vy or 0.0), float(vz or 0.0)],
        "has_uncertainty": uncertainty_m is not None,
        "position_uncertainty_m": float(uncertainty_m or 0.0),
        "has_confidence": confidence is not None,
        "confidence": float(confidence if confidence is not None else 0.0),
    }
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
    identity_pose = {"t": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]}
    traj: Dict[str, object] = {
        "schema_version": SCHEMA_CORE,
        "agent_id": str(agent_id),
        "plan_id": str(plan_id),
        "plan_revision": int(plan_revision),
        "frame_ref": _frame_ref(frame_ref_fqn, uuid=frame_ref_uuid),
        "waypoints": list(waypoints),
        "has_goal_pose": goal_pose is not None,
        "goal_pose": goal_pose or identity_pose,
        "has_horizon_sec": horizon_sec is not None,
        "horizon_sec": float(horizon_sec or 0.0),
        "has_replan_rate_hz": replan_rate_hz is not None,
        "replan_rate_hz": float(replan_rate_hz or 0.0),
        "stamp": _stamp(timestamp_s),
    }
    return traj


def make_component_ref(topic: str, key: str) -> Dict[str, str]:
    """Build a ``ComponentRef`` dict pointing at one (topic, key) cell."""
    return {"topic": str(topic), "key": str(key)}


def make_entity_binding(
    entity_id: str,
    entity_class: str,
    components: Sequence[Dict],
    *,
    position: Optional[Sequence[float]] = None,
    frame_ref_fqn: str = "",
    source_id: str = "",
    timestamp_s: float = 0.0,
) -> Dict:
    """
    Build an ``EntityBinding`` dict that links one logical entity to every
    (topic, key) that contributes to it.

    ``EntityBinding.pose`` is a ``FramedPose``, not a bare ``PoseSE3``: a
    pose without the frame it is expressed in is not interpretable, so the
    caller gives a position and the frame it lives in and gets both.
    """
    return {
        "schema_version": SCHEMA_CORE,
        "entity_id": str(entity_id),
        "entity_class": str(entity_class),
        "components": list(components),
        # Presence flag, not omission: FramedPose is a value type, so the
        # field is always on the wire and `has_pose` says whether to read it.
        "has_pose": position is not None,
        "pose": make_framed_pose(
            *(position if position is not None else (0.0, 0.0, 0.0)),
            q=(0.0, 0.0, 0.0, 1.0),
            frame_ref_fqn=frame_ref_fqn,
            timestamp_s=timestamp_s,
        ),
        "stamp": _stamp(timestamp_s),
        "source_id": str(source_id),
    }



_frame_ref = payloads.frame_ref


SCHEMA_DISCOVERY = "spatial.discovery/1.7"

# ServiceKind has no member for a sensor fleet, a roadside unit or a fusion
# service, so all of them map to OTHER and the real role travels in `hints`.
# That works, but it means the enum cannot discriminate the three service
# classes this demo is actually built out of — a consumer filtering "show me
# the fusion services" has to know the demo's hint convention. Worth WG
# input; on the findings list.
_SERVICE_KIND = {
    "SENSING": "OTHER",
    "INFRASTRUCTURE": "OTHER",
    "AV_FLEET": "OTHER",
    "FUSION": "OTHER",
}


def topic_meta(name: str, type_: str, qos_profile: str) -> Dict:
    """One TopicMeta row. type and qos_profile must be registered (3.3.2/3.3.3)."""
    return {
        "name": name,
        "type": type_,
        "version": "v1",
        "qos_profile": qos_profile,
        "target_rate_hz": 0.0,
        "max_chunk_bytes": 0,
    }


def circle_coverage(center_x: float, center_y: float, radius_m: float,
                    frame_ref: Optional[Dict] = None) -> Dict:
    """
    A circular coverage area as a spec `CoverageElement`.

    `CoverageElement` offers bbox (geographic degrees) and aabb (local metres);
    there is no circle. The aabb is the circle's bounding box in the local
    frame, so a consumer that wants the circle back takes the centre and
    half-width — which is what the canvas dashboard does.
    """
    return {
        "has_crs": False,
        "crs": "",
        "has_bbox": False,
        "bbox": [0.0, 0.0, 0.0, 0.0],
        "has_aabb": True,
        "aabb": {
            "min_xyz": [center_x - radius_m, center_y - radius_m, 0.0],
            "max_xyz": [center_x + radius_m, center_y + radius_m, 0.0],
        },
        "global": False,
        "has_frame_ref": frame_ref is not None,
        "frame_ref": frame_ref or _frame_ref("scene/intersection"),
        "has_coverage_window": False,
        "coverage_window_start": _stamp(0.0),
        "coverage_window_end": _stamp(0.0),
    }


def make_announce(
    operator: str,
    *,
    service_kind: str,
    topics: Sequence[Dict],
    coverage: Optional[Dict] = None,
    timestamp_s: float = 0.0,
    manifest_uri: Optional[str] = None,
) -> Dict:
    """
    Build a real `spatial::disco::Announce`.

    This used to emit a demo-private shape — `operator`, `service_kind`,
    `has_coverage`, and topics as `{topic, msg_type}` — which the repo's own
    `validate_topic_meta` rejected and which `AnnounceCache` dropped for having
    no `service_id`, so the flagship demo was quietly absent from discovery
    (findings 5.1, 5.2).

    `topics` rows are TopicMeta: build them with :func:`topic_meta` so the
    type and QoS profile come from the 3.3.2 / 3.3.3 registries.
    """
    return {
        "service_id": f"svc:{operator}",
        "name": operator,
        "kind": _SERVICE_KIND.get(service_kind, "OTHER"),
        "version": "1.7",
        "org": "OARC demo",
        "hints": [{"key": "role", "value": str(service_kind)}],
        "caps": {
            "supported_profiles": [
                {"name": "spatial.core", "major": 1, "min_minor": 7, "max_minor": 7},
                {"name": "spatial.discovery", "major": 1, "min_minor": 7, "max_minor": 7},
            ],
            "preferred_profiles": ["spatial.discovery/1.7", "spatial.core/1.7"],
            "features": [],
        },
        "topics": list(topics),
        "coverage": [coverage] if coverage is not None else [],
        "coverage_frame_ref": _frame_ref("scene/intersection"),
        "has_coverage_eval_time": False,
        "coverage_eval_time": _stamp(timestamp_s),
        "transforms": [],
        "manifest_uri": (
            manifest_uri
            or f"spatialdds://oarc.demo/zone:intersection/service:{operator}"
        ),
        "auth_hint": "",
        "stamp": _stamp(timestamp_s),
        "ttl_sec": 300,
    }


def make_framed_pose(x: float, y: float, z: float, q: Sequence[float],
                     frame_ref_fqn: str, timestamp_s: float) -> Dict:
    """
    A `spatial::core::FramedPose` — the spec type for a local metric pose.

    Note what it does NOT carry: velocity, or a source_operator field. The
    demo's old ego payload had both. Velocity was never read by any consumer
    (dashboard and Rerun both ignore it), and the operator is already in the
    topic name, which is where DDS expects that kind of identity to live.
    """
    return {
        "pose": {"t": [float(x), float(y), float(z)], "q": _vec(q, ("x", "y", "z", "w"))},
        "frame_ref": _frame_ref(frame_ref_fqn),
        "cov": {"discriminator": "COV_NONE", "none": 0},
        "stamp": _stamp(timestamp_s),
    }


def make_detection(det_id: str, class_id: str, score: float,
                   center: Sequence[float], size: Sequence[float],
                   q: Sequence[float], frame_ref_fqn: str,
                   timestamp_s: float, source_id: str) -> Dict:
    """One `spatial::semantics::Detection3D`, complete."""
    return payloads.detection3d(
        det_id, class_id, score, center, size, q, frame_ref_fqn,
        timestamp_s, source_id)


def make_detection_set(set_id: str, source_operator: str, frame_ref_fqn: str,
                       dets: Sequence[Dict], frame_seq: int,
                       timestamp_s: float) -> Dict:
    """
    An `oarc_demo::OperatorDetectionSet`.

    Each row composes the spec `Detection3D` verbatim and adds the velocity
    the fuser gates on, which `Detection3D` has no member for. See
    idl/demo/oarc_demo.idl for why that is a composed struct rather than a
    MetaKV attribute.
    """
    return {
        "set_id": str(set_id),
        "source_operator": str(source_operator),
        "frame_ref": _frame_ref(frame_ref_fqn),
        "dets": list(dets),
        "frame_seq": int(frame_seq),
        "stamp": _stamp(timestamp_s),
    }


def make_detection_with_velocity(detection: Dict, velocity: Optional[Sequence[float]],
                                 source_modality: str = "det3d") -> Dict:
    return {
        "detection": detection,
        "has_velocity": velocity is not None,
        "velocity": _vec(velocity if velocity is not None else (0.0, 0.0, 0.0)),
        "source_modality": str(source_modality),
    }


# ── Platform fusion outputs ───────────────────────────────────────────
# The fusion service's three published streams. Each is a real struct on
# its own typed topic; under the envelope all three were JSON blobs whose
# shape lived only in the subscriber's parser.


def make_fused_track(track, timestamp_s: float) -> Dict:
    """One :class:`fusion.FusedTrack` as an ``oarc_demo::FusedTrack`` dict."""
    return {
        "track_id": str(track.track_id),
        "position": _vec((track.position.x, track.position.y, track.position.z)),
        "velocity": _vec((track.velocity.vx, track.velocity.vy, track.velocity.vz)),
        "position_uncertainty": float(track.position_uncertainty),
        "object_class": str(track.object_class),
        "confidence": float(track.confidence),
        "source_operators": [str(o) for o in track.source_operators],
        "source_modalities": [str(m) for m in track.source_modalities],
        "source_count": int(track.source_count),
        "track_age": float(track.track_age),
        "stamp": _stamp(getattr(track, "timestamp", timestamp_s)),
    }


def make_fused_track_set(tracks, *, source_operator: str = "platform",
                         timestamp_s: float = 0.0) -> Dict:
    return {
        "source_operator": str(source_operator),
        "tracks": [make_fused_track(t, timestamp_s) for t in tracks],
        "stamp": _stamp(timestamp_s),
    }


def make_fusion_coverage(metrics: Dict, *, source_operator: str = "platform",
                         timestamp_s: float = 0.0) -> Dict:
    """
    Coverage metrics as a struct.

    ``per_operator_track_count`` was a free-form JSON object keyed by
    operator id — not expressible as an IDL struct field, so it becomes a
    sequence of ``OperatorTrackCount`` rows. Sorted so the sequence is
    stable across ticks and a diff of two samples means something.
    """
    per_op = metrics.get("per_operator_track_count") or {}
    return {
        "source_operator": str(source_operator),
        "track_count": int(metrics.get("track_count", 0)),
        "multi_source_count": int(metrics.get("multi_source_count", 0)),
        "multi_source_pct": float(metrics.get("multi_source_pct", 0.0)),
        "best_single_operator_count": int(
            metrics.get("best_single_operator_count", 0)),
        "coverage_improvement": float(metrics.get("coverage_improvement", 0.0)),
        "best_av_operator_count": int(metrics.get("best_av_operator_count", 0)),
        "coverage_improvement_excl_infra": float(
            metrics.get("coverage_improvement_excl_infra", 0.0)),
        "per_operator_track_count": [
            {"operator_id": str(op), "track_count": int(n)}
            for op, n in sorted(per_op.items())
        ],
        "stamp": _stamp(timestamp_s),
    }


def make_trajectory_conflict_event(conflict: Dict, *, timestamp_s: float,
                                   frame_ref_fqn: str,
                                   source_id: str = "platform-fusion") -> Dict:
    """
    A predicted trajectory conflict as a ``spatial::events::SpatialEvent``.

    ``PROXIMITY_ALERT`` is the closest registered EventType, but it is not
    an exact fit: 1.7's event types all describe something that has already
    happened or is happening now, and this is a *predicted* conflict some
    seconds ahead.

    The lead time needs no extension, though. ``event_start`` is when the
    event begins and ``stamp`` is when this sample was produced; for a
    prediction those are different instants, and their difference *is* the
    lead time. So event_start is the predicted conflict time and stamp is
    now — which also makes an already-started event and a predicted one
    distinguishable by comparing the two, without a new field.

    The conflicting pair has nowhere typed to go. SpatialEvent models one
    trigger and one secondary, and both slots are det/track ids rather than
    agent ids; the spec's generic hatch, ``attributes``, is
    ``MetaKV{namespace, json}`` — a JSON string, which is exactly what this
    migration exists to get off the bus. So the pair is carried in the
    event_id and the description, ``attributes`` is left empty, and the
    missing typed slot is on the findings list.
    """
    agents = [str(a) for a in conflict.get("agents", [])]
    pos = conflict.get("conflict_position") or {}
    # The detector reports None when the trajectories carry no usable stamp,
    # i.e. "they conflict, but not at a knowable time".
    lead = conflict.get("time_to_conflict")
    lead_s = float(lead) if lead is not None else None
    return {
        "schema_version": SCHEMA_EVENTS,
        "event_id": "conflict:" + "|".join(sorted(agents)),
        "type": "PROXIMITY_ALERT",
        "severity": "ALERT",
        "state": "ACTIVE",
        "has_zone_id": False,
        "zone_id": "",
        "has_position": bool(pos),
        "position": _vec((pos.get("x", 0.0), pos.get("y", 0.0), pos.get("z", 0.0))),
        "frame_ref": _frame_ref(frame_ref_fqn),
        "has_trigger_det_id": False,
        "trigger_det_id": "",
        "has_trigger_track_id": False,
        "trigger_track_id": "",
        "trigger_class_id": "",
        "has_secondary_det_id": False,
        "secondary_det_id": "",
        "has_measured_speed_mps": False,
        "measured_speed_mps": 0.0,
        "has_measured_dwell_sec": False,
        "measured_dwell_sec": 0.0,
        "has_measured_distance_m": True,
        "measured_distance_m": float(conflict.get("min_distance_m", 0.0)),
        "has_zone_occupancy": False,
        "zone_occupancy": 0,
        "confidence": 1.0,
        "has_evidence": False,
        "evidence": {"blob_id": "", "role": "", "checksum": ""},
        "has_description": True,
        "description": (
            f"predicted conflict between {' and '.join(sorted(agents))}"
            + (f" in {lead_s:.1f}s" if lead_s is not None else "")
        ),
        # When the conflict is predicted to occur; `stamp` below is when the
        # prediction was made, so event_start - stamp is the lead time.
        "event_start": _stamp(timestamp_s + (lead_s or 0.0)),
        "stamp": _stamp(timestamp_s),
        "source_id": str(source_id),
        "attributes": [],
    }
