"""Map SpatialDDS envelope msg_types to JSON-schema descriptions for MCAP.

The repo's envelope transport (`nuscenes/dds_envelope_transport.py`) already
ships every dataclass as JSON in the envelope's `payload_json` field. The MCAP
bridge therefore writes JSON-encoded messages and registers JSON schemas keyed
by the envelope's `msg_type`.

We don't try to describe every dataclass field-by-field — Foxglove and `mcap
cat` only need a permissive object schema to render the JSON. Callers that
want stricter schemas can pass a `schema_overrides` dict to the recorder.
"""

from __future__ import annotations

from typing import Dict, Iterable

# Every msg_type currently emitted by the demos. Permissive object schemas —
# enough for MCAP readers to display, not enough to enforce field shape.
KNOWN_MSG_TYPES: Iterable[str] = (
    # nuScenes / multi-operator fusion
    "NUSC_EGO_POSE",
    "NUSC_VISION_META",
    "NUSC_VISION_FRAME",
    "NUSC_LIDAR_META",
    "NUSC_LIDAR_FRAME",
    "NUSC_RAD_DET_SET",
    "NUSC_DET3D_SET",
    "NUSC_FUSED_TRACK_SET",
    "NUSC_FUSION_COVERAGE",
    # DeepSense
    "DEEPSENSE_UNIT1_GEOPOSE",
    "DEEPSENSE_UNIT2_GEOPOSE",
    "DEEPSENSE_RF_BEAM_META",
    "DEEPSENSE_RF_BEAM_FRAME",
    "DEEPSENSE_RAD_TENSOR_META",
    "DEEPSENSE_RAD_TENSOR_FRAME",
    "DEEPSENSE_VISION_META",
    "DEEPSENSE_VISION_FRAME",
    "DEEPSENSE_LIDAR2D_FRAME",
    "DEEPSENSE_DET2D_SET",
    # Generic / discovery
    "ANNOUNCE",
    "OPERATOR_DATA",
)


def default_schema(msg_type: str) -> dict:
    """Return a permissive JSON schema for a SpatialDDS msg_type."""
    return {
        "title": msg_type,
        "type": "object",
        "additionalProperties": True,
    }


def build_schema_table(overrides: Dict[str, dict] | None = None) -> Dict[str, dict]:
    """Return {msg_type: jsonschema-dict} for every known type, plus overrides.

    Overrides may include msg_types not in KNOWN_MSG_TYPES — they will be
    registered too. This is how a caller can extend the bridge for custom
    SpatialDDS message types without modifying this file.
    """
    table = {name: default_schema(name) for name in KNOWN_MSG_TYPES}
    if overrides:
        table.update(overrides)
    return table
