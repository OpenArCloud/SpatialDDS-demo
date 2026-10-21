from typing import List, Optional, Tuple

TOPIC_DISCOVERY_ANNOUNCE_V1 = "spatialdds/discovery/announce/v1"
TOPIC_DISCOVERY_DEPART_V1 = "spatialdds/discovery/depart/v1"
TOPIC_DISCOVERY_QUERY_V1 = "spatialdds/discovery/query/v1"
TOPIC_VPS_QUERY_V1 = "spatialdds/vps/query/v1"
TOPIC_VPS_RESULT_V1 = "spatialdds/vps/result/v1"
TOPIC_CATALOG_QUERY_V1 = "spatialdds/catalog/query/v1"
TOPIC_BOOTSTRAP_QUERY_V1 = "spatialdds/bootstrap/query/v1"
TOPIC_BOOTSTRAP_RESPONSE_V1 = "spatialdds/bootstrap/response/v1"
TOPIC_DDS_ENVELOPE_V1 = "spatialdds/envelope/v1"

# --- world model (`spatial.owm/0.1`, provisional, SpatialDDS 1.8) --------
# One topic per record kind. Per-layer splitting waits for a FAST tier:
# Entity.layer is carried but informational today.
#
# The names follow §3.3.1's five-segment grammar,
# `spatialdds/<domain>/<stream>/<type>/<version>`, with the scene as the
# domain so two venues on one DDS domain do not share an entity namespace.
# The scene name is the demo's and it keeps it.
SCENE = "ut-austin-littlefield"

TOPIC_MODEL_ENTITY_V1 = f"spatialdds/{SCENE}/world/entity/v1"
TOPIC_MODEL_POSE_V1 = f"spatialdds/{SCENE}/world/model_pose/v1"
TOPIC_MODEL_COMMAND_V1 = f"spatialdds/{SCENE}/world/model_command/v1"

# The scene's own georeference: where its frame sits on the earth, published by
# the party that declares the frame. `frames` is the stream segment the web
# binding already uses for this kind of resource, so the N.3 path falls out of
# the topic name with no mapping. The catalogue server announces a transform
# for the same frame and keeps doing so --- a different claim, about where its
# map sits. See docs/venue-georeference-census.md.
TOPIC_FRAME_TRANSFORM_V1 = f"spatialdds/{SCENE}/frames/frame_transform/v1"

# The pre-1.8 names and the retired relationship topic are gone, with the
# last readers of them: the web bridge's `_ModelPump` and `/v1/model` surface
# retired on 2026-10-18, and the robot tier had migrated a batch earlier. The
# names are recorded in docs/owm-1.8-census.md and docs/bridge-1.8-census.md
# rather than kept here as constants nothing reads. `Relationship` itself has
# no 1.8 home and is open design --- docs/relationship-candidate-1.9.md.


def TOPIC_ANCHORS_DELTA(zone: str) -> str:
    return f"spatialdds/anchors/{zone}/delta/v1"


def TOPIC_CATALOG_REPLIES(client_id: str) -> str:
    return f"spatialdds/catalog/replies/{client_id}/v1"


def TOPIC_DISCOVERY_RESPONSE(query_id: str) -> str:
    return f"spatialdds/discovery/response/{query_id}"


# --- 1.7 registries (spec 3.3.2 typed topics, 3.3.3 QoS profiles) ---------
# Every Announce.topics[] entry and manifest topic reference MUST carry a
# type + version + qos_profile drawn from these tables, or a documented
# deployment-specific extension following the `myorg.depth_frame` /
# `DEPTH_LIVE` naming pattern.
REGISTERED_TOPIC_TYPES = {
    "geometry_tile",
    "video_frame",
    "radar_detection",
    "radar_tensor",
    "rf_beam",
    "radio_scan",
    "seg_mask",
    "desc_array",
    "map_meta",
    "map_alignment",
    "map_event",
    "spatial_zone",
    "spatial_event",
    "zone_state",
    # Informative example registrations (Appendix E): types defined only in the
    # example IDL, outside the normative conformance surface (spec batch-3
    # follow-up Q.3). The demo does not use them; kept here as valid names.
    "agent_status",
    "task_offer",
    "task_assignment",
    "navsat_status",
    "planned_trajectory",
    "entity_binding",
    "geopose",
    "vps_query",
    # Added in 1.7's findings-batch-2 revision, closing the gap between the
    # registry and the IDL. Every one of these is a stable type the demo had
    # to invent an `oarc.*` name for because discovery could not describe it.
    "framed_pose",
    "detection3d",
    "detection2d",
    "lidar_frame",
    "lidar_meta",
    "radar_tensor_meta",
    "video_meta",
    "rf_beam_meta",
    "imu_sample",
    "anchor_delta",
    "rad_sensor_meta",
    "radio_sensor_meta",
    # Added in 1.7 batch 3: the VPS request/response pair now has spec types
    # (argeo) and cross-source fused tracks have one (semantics). `tile_meta`
    # was dropped in the batch-3 follow-up — `geometry_tile` is the canonical
    # registry name for TileMeta.
    "vps_response",
    "fused_track",
    # Blob transfer is its own mechanism rather than a topic type, but a
    # lane carrying chunks still has to name something.
    "blob_chunk",
    # Added in 1.8. Found by comparing this set against the 1.8 spec's
    # §3.3.2 table during the migration, not by anything failing: until then
    # the validator would have refused a conformant 1.8 announce of either.
    "crossing_line",    # events::CrossingLine, ZONE_META
    "model_entity",     # owm::Entity (provisional spatial.owm/0.1)
}

REGISTERED_QOS_PROFILES = {
    "GEOM_TILE",
    "VIDEO_LIVE",
    "VIDEO_ARCHIVE",
    "RADAR_RT",
    "RF_BEAM_RT",
    "RADIO_SCAN_RT",
    "SEG_MASK_RT",
    "DESC_BATCH",
    "MAP_META",
    "ZONE_META",
    "EVENT_RT",
    "POSE_RT",
    "VPS_REQ",
    "VPS_RESP",
    # Added alongside the registry rows they serve, so every registered type
    # now names a registered profile. The demo used to invent ANCHOR_DELTA
    # and borrow MAP_META for sensor metadata.
    "DET_RT",
    "LIDAR_RT",
    "IMU_RT",
    "SENSOR_META",
    "ANCHOR_DELTA",
}

def _deployment_topic_types() -> set:
    """
    Deployment-specific type names this demo documents and uses.

    Sourced from spatialdds_demo.topic_types.EXTENSIONS so the validator and
    the type registry cannot disagree — they did once, and the publish-path
    validator caught it, which is the point of having it there.

    Imported lazily: topic_types imports the generated bindings, and this
    module is imported by code that has no need of them.
    """
    names = {"anchor_delta"}   # anchors have no registered type or profile
    try:
        from spatialdds_demo.topic_types import EXTENSIONS
        names |= set(EXTENSIONS)
    except Exception:
        pass
    return names


# Every profile the demo uses is registered now; ANCHOR_DELTA was the last
# invented one and 1.7 adopted it.
DEPLOYMENT_QOS_PROFILES: set = set()


TOPIC_SOURCE_SPEC = "spec"
TOPIC_SOURCE_ANNOUNCE = "announce"
TOPIC_SOURCE_MANIFEST = "manifest"
TOPIC_SOURCE_REQUEST = "request"
TOPIC_SOURCE_ANNOUNCE_PREVIEW = "announce_preview"
TOPIC_SOURCE_FALLBACK = "fallback"
TOPIC_SOURCE_RUNTIME_CUSTOM = "runtime_custom"


def validate_topics_are_canonical(
    topics: List[str], service_kind: Optional[str] = None
) -> Tuple[bool, List[str]]:
    errors: List[str] = []

    for topic in topics:
        if not topic.startswith("spatialdds/"):
            errors.append(f"Topic missing spatialdds/ prefix: {topic}")
        if "//" in topic:
            errors.append(f"Topic contains double slash: {topic}")
        if not topic.endswith("/v1"):
            errors.append(f"Topic missing /v1 suffix: {topic}")

    if service_kind == "VPS":
        if TOPIC_VPS_QUERY_V1 not in topics:
            errors.append("Missing VPS query topic for VPS service")
        if TOPIC_VPS_RESULT_V1 not in topics:
            errors.append("Missing VPS result topic for VPS service")

    return len(errors) == 0, errors


def topic_meta(name: str, type_: str, qos_profile: str, *,
               target_rate_hz: float = 0.0, max_chunk_bytes: int = 0) -> dict:
    """
    One complete 1.8 ``TopicMeta`` row, as plain data.

    ``type`` and ``qos_profile`` must be registered (3.3.2 / 3.3.3) or a
    documented extension -- ``validate_topic_meta`` below says which.
    ``durability`` is derived from the profile (``qos_profiles.durability_for``)
    and never written by hand: 1.8 appended it so a bridged gateway can fill a
    manifest's N.7-required durability from the bus, and a guessed value is a
    claim nobody made.

    The one builder for this shape. There were two, in ``spatialdds_test`` and
    in the fusion demo, and the 1.8 migration updated only one: the mock VPS
    then could not build its own announce, never answered a CoverageQuery,
    and the web-bridge HTTP lane timed out with nothing pointing at why.
    """
    from spatialdds_demo import qos_profiles   # imported lazily, see module doc
    return {
        "name": name,
        "type": type_,
        "version": "v1",
        "qos_profile": qos_profile,
        "target_rate_hz": float(target_rate_hz),
        "max_chunk_bytes": int(max_chunk_bytes),
        "durability": qos_profiles.durability_for(qos_profile),
    }


def validate_topic_meta(entries: List[dict]) -> Tuple[bool, List[str]]:
    """
    Validate TopicMeta rows against the 1.7 registries (spec 3.3.2/3.3.3).

    ``type``, ``version`` and ``qos_profile`` are all mandatory in 1.7. A
    value passes if it is registered, or if it is a documented
    deployment-specific extension.
    """
    errors: List[str] = []

    for entry in entries:
        name = entry.get("name") or "<unnamed>"

        topic_type = entry.get("type")
        if not topic_type:
            errors.append(f"{name}: TopicMeta.type is required")
        elif (
            topic_type not in REGISTERED_TOPIC_TYPES
            and topic_type not in _deployment_topic_types()
        ):
            errors.append(
                f"{name}: unregistered topic type '{topic_type}' "
                "(not in the 3.3.2 registry or the documented extensions)"
            )

        if not entry.get("version"):
            errors.append(f"{name}: TopicMeta.version is required")

        qos_profile = entry.get("qos_profile")
        if not qos_profile:
            errors.append(f"{name}: TopicMeta.qos_profile is required")
        elif (
            qos_profile not in REGISTERED_QOS_PROFILES
            and qos_profile not in DEPLOYMENT_QOS_PROFILES
        ):
            errors.append(
                f"{name}: unregistered QoS profile '{qos_profile}' "
                "(not in the 3.3.3 registry or the documented extensions)"
            )

    return len(errors) == 0, errors
