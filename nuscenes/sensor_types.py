#!/usr/bin/env python3
"""
The SpatialDDS types the nuScenes and DeepSense converters build.

These used to be hand-written dataclasses mirroring the IDL. They had drifted
badly — `StreamMeta` was missing `schema_version`, `FrameHeader` was missing
`sensor_pose` entirely, `VisionFrame` was missing eight fields, `LidarFrame`
nine — so every payload built from them was something that resembled a
SpatialDDS message and was not one. Nothing caught it, because nothing
compared them to the IDL: they *were* the demo's definition of the types.

So they are the generated types now. A field that exists in the spec exists
here, with the spec's name and the spec's semantics, because there is only
one definition. Constructing one requires every field — which is the point:
the spec has no absent fields, only presence-flagged values, and a
constructor that lets you skip one is how the drift happened.

`to_dict` is the JSON mapping (§2.8 enums as identifiers, IDL field names),
so what a converter produces is what goes on the wire.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from spatialdds_demo.json_mapping import to_json as _to_json  # noqa: E402
from spatialdds_idl.spatial.common import (  # noqa: E402
    FrameRef,
    QuaternionXYZW,
    Vec3,
)
from spatialdds_idl.spatial.core import BlobRef, GeoPose, PoseSE3  # noqa: E402
from spatialdds_idl.builtin import Time  # noqa: E402
from spatialdds_idl.spatial.semantics import (  # noqa: E402
    Detection3D,
    Detection3DSet,
)
from spatialdds_idl.spatial.sensing.common import (  # noqa: E402
    FrameHeader,
    FrameQuality,
    StreamMeta,
)
from spatialdds_idl.spatial.sensing.lidar import LidarFrame, LidarMeta  # noqa: E402
from spatialdds_idl.spatial.sensing.rad import (  # noqa: E402
    RadDetection,
    RadDetectionSet,
)
from spatialdds_idl.spatial.sensing.vision import (  # noqa: E402
    CamIntrinsics,
    VisionFrame,
    VisionMeta,
)

__all__ = [
    "BlobRef", "CamIntrinsics", "Detection3D", "Detection3DSet",
    "FrameHeader", "FrameQuality", "FrameRef", "GeoPose", "LidarFrame",
    "LidarMeta", "PoseSE3", "QuaternionXYZW", "RadDetection",
    "RadDetectionSet", "StreamMeta", "Time", "Vec3", "VisionFrame",
    "VisionMeta", "to_dict",
]


def to_dict(obj: object) -> Dict:
    """One typed sample as the JSON a consumer sees."""
    return _to_json(obj)
