"""Bidirectional mapping between ROS 2 tf2 frame_ids and SpatialDDS FrameRefs.

ROS 2 uses string ``frame_id`` (e.g. ``"base_link"``) inside a tf2 tree; that
namespace is robot-local. SpatialDDS uses ``FrameRef = {uuid, fqn}`` over a
shared bus, so collisions across robots ("base_link" on robot A vs robot B)
must be resolved.

Convention: the SpatialDDS FQN is always ``{operator}/{frame_id}``, and the
UUID is a deterministic UUIDv5 derived from ``(operator, frame_id)`` so the
same frame always gets the same UUID (across processes, across runs).

Pure-Python; no ROS 2 or DDS imports.
"""

from __future__ import annotations

import uuid
from typing import Dict


# Stable namespace for SpatialDDS FrameRef UUIDs derived from this bridge.
# Picking a fixed UUID here means the mapping is reproducible — the same
# (operator, frame_id) pair always yields the same UUID, on any host, in
# any process.
_FRAMEREF_NAMESPACE = uuid.UUID("3f0c0a17-1f6f-5b0a-8e5d-9d3a8c1f2e4a")


def deterministic_uuid(operator: str, frame_id: str) -> str:
    """Return a deterministic UUID string for ``(operator, frame_id)``."""
    name = f"{operator}/{frame_id}"
    return str(uuid.uuid5(_FRAMEREF_NAMESPACE, name))


def frame_ref_dict(uuid_str: str, fqn: str) -> Dict[str, str]:
    """Build a SpatialDDS FrameRef as a JSON-serializable dict."""
    return {"uuid": uuid_str, "fqn": fqn}


class FrameMapper:
    """Stateful bidirectional mapping for one operator.

    Caches lookups so repeated conversions on the same frame_id are O(1).
    Reverse lookup (FrameRef → frame_id) prefers cache hits and falls back to
    parsing the FQN, so it works for FrameRefs the bridge has never seen
    (e.g. arriving from another publisher on the bus).
    """

    def __init__(self, operator: str):
        self.operator = operator
        self._fwd: Dict[str, Dict[str, str]] = {}  # frame_id → {uuid, fqn}
        self._rev: Dict[str, str] = {}             # uuid → frame_id

    def frame_id_to_frame_ref(self, frame_id: str) -> Dict[str, str]:
        cached = self._fwd.get(frame_id)
        if cached is not None:
            return cached
        u = deterministic_uuid(self.operator, frame_id)
        ref = frame_ref_dict(u, f"{self.operator}/{frame_id}")
        self._fwd[frame_id] = ref
        self._rev[u] = frame_id
        return ref

    def frame_ref_to_frame_id(self, ref: Dict[str, str]) -> str:
        if not isinstance(ref, dict):
            return ""
        u = ref.get("uuid", "")
        cached = self._rev.get(u)
        if cached is not None:
            return cached
        # Fallback: split the FQN. Strip the leading "{operator}/" if present;
        # otherwise return the whole FQN (foreign frames keep their full name).
        fqn = ref.get("fqn", "") or ""
        prefix = f"{self.operator}/"
        if fqn.startswith(prefix):
            return fqn[len(prefix):]
        return fqn
