#!/usr/bin/env python3
"""
The types the DeepSense converters build.

These used to be hand-written dataclasses mirroring the spec's provisional
`rf_beam` and `rad` profiles. They are the generated types now — the same
drift that made the nuScenes types not-quite-SpatialDDS applies here, and
there is no reason for a second definition of a type the IDL already has.

`rf_beam`'s IDL ships under `idl/v1.7/examples/` rather than beside the
stable modules, so the generator names it explicitly; see the findings list.

`Detection2DSet` is demo-owned, and has to be: 1.7 has `Detection3D` for 3D
boxes and nothing at all for 2D ones. `vision::VisionDetections` carries
keypoints and 2D *tracks*, not labelled boxes, and `seg_mask` is masks — so
the most common camera perception output there is has no spec type. Defined
in `idl/demo/oarc_demo.idl`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from spatialdds_idl.oarc_demo import (  # noqa: E402
    BBox2D,
    Detection2D,
    Detection2DSet,
)
from spatialdds_idl.spatial.sensing.common import (  # noqa: E402
    AxisSpec,
    FrameHeader,
    FrameQuality,
    StreamMeta,
)
from spatialdds_idl.spatial.sensing.rad import (  # noqa: E402
    RadTensorFrame,
    RadTensorMeta,
)
from spatialdds_idl.spatial.sensing.rf_beam import (  # noqa: E402
    RfBeamFrame,
    RfBeamMeta,
)
from spatialdds_idl.builtin import Time  # noqa: E402

__all__ = [
    "AxisSpec", "BBox2D", "Detection2D", "Detection2DSet", "FrameHeader",
    "FrameQuality", "RadTensorFrame", "RadTensorMeta", "RfBeamFrame",
    "RfBeamMeta", "StreamMeta", "Time",
]
