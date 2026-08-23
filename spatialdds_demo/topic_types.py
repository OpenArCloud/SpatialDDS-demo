"""
Registered topic type name -> generated Python class.

`TopicMeta.type` names a type from the §3.3.2 registry (or a documented
deployment-specific extension). This table is what lets a consumer turn an
announced topic into a typed reader without a hardcoded topic list: read the
announce, look the type up here, subscribe.

That is the replacement for the envelope's `msg_type` string. The difference is
that `msg_type` was demo-private and resolvable only through a table each
consumer kept for itself, whereas these names are the spec's and the mapping
lives once.
"""

from __future__ import annotations

from typing import Dict, Optional, Type

from spatialdds_idl.oarc_demo import (
    CatalogQuery,
    CatalogResponse,
    FusedTrackSet,
    FusionCoverage,
    OperatorDetectionSet,
    VpsRequest,
    VpsResponse,
)
from spatialdds_idl.spatial.core import (
    EntityBinding,
    FramedPose,
    GeoPose,
    NavSatStatus,
    PlannedTrajectory,
)
from spatialdds_idl.spatial.disco import Announce, CoverageQuery, CoverageResponse, Depart
from spatialdds_idl.spatial.events import SpatialEvent
from spatialdds_idl.spatial.semantics import Detection3DSet
from spatialdds_idl.spatial.sensing.lidar import LidarFrame
from spatialdds_idl.spatial.sensing.rad import RadTensorFrame
from spatialdds_idl.spatial.sensing.vision import VisionFrame

# --- §3.3.2 registered types -----------------------------------------------
REGISTERED: Dict[str, Type] = {
    "geopose": GeoPose,
    "navsat_status": NavSatStatus,
    "planned_trajectory": PlannedTrajectory,
    "entity_binding": EntityBinding,
    "spatial_event": SpatialEvent,
    "video_frame": VisionFrame,
    "radar_tensor": RadTensorFrame,
    "radar_detection": Detection3DSet,
    "vps_query": VpsRequest,
}

# --- deployment-specific extensions, named per §3.3.2 guidance --------------
# Each of these exists because the spec has no type for what the demo means;
# they are catalogued in ar_demo/SPEC_COMPLIANCE.md.
EXTENSIONS: Dict[str, Type] = {
    # semantics::Detection3D carries no velocity, and the fuser gates on it.
    # Composes the spec type rather than replacing it.
    "oarc.detection3d_velocity": OperatorDetectionSet,
    # No fused-track type in 1.7: Tracklet is feature-level, Track2D per-image.
    "oarc.fused_track": FusedTrackSet,
    "oarc.fusion_coverage": FusionCoverage,
    # A local ego pose is a FramedPose; `geopose` is the geographic one.
    "oarc.framed_pose": FramedPose,
    # No catalogue query/response pair in 1.7.
    "oarc.catalog_query": CatalogQuery,
    "oarc.catalog_response": CatalogResponse,
    # vps_query is registered but has no struct; the response has no type name.
    "oarc.vps_response": VpsResponse,
    # Anchor deltas have neither a registered type nor a QoS profile.
}

ALL: Dict[str, Type] = {**REGISTERED, **EXTENSIONS}

# Types that are their own topic rather than being announced in TopicMeta.
WELL_KNOWN: Dict[str, Type] = {
    "spatialdds/discovery/announce/v1": Announce,
    "spatialdds/discovery/depart/v1": Depart,
    "spatialdds/discovery/query/v1": CoverageQuery,
}


class UnknownTopicType(KeyError):
    """A TopicMeta.type this build cannot map to a class."""


def resolve(type_name: str) -> Type:
    try:
        return ALL[type_name]
    except KeyError:
        raise UnknownTopicType(
            f"{type_name!r} is not a registered SpatialDDS type or a documented "
            f"extension. Known: {', '.join(sorted(ALL))}"
        ) from None


def try_resolve(type_name: str) -> Optional[Type]:
    """As :func:`resolve`, but None for an unknown type.

    A consumer should skip topics it cannot type rather than refusing to run:
    the bus may legitimately carry types this build has never heard of, and
    §3.3.2 says unknown values are extension points, not errors.
    """
    return ALL.get(type_name)
