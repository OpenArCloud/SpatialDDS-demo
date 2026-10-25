"""Topic-name conventions for the MQTT ↔ SpatialDDS bridge.

Three jobs:

  * ``infer_msg_type(topic)`` — turn an MQTT topic string into a §3.3.2
    registered type name when the publisher didn't supply one. On the DDS
    side the type is announced, so this is only needed for the inbound
    direction, where the message comes from MQTT and has no announce
    behind it.
  * ``get_qos(topic)`` — pick an MQTT QoS level + retain flag based on
    the topic's role (meta = retained, frames = best-effort, decisions
    = at-least-once, etc.).
  * ``matches_any(topic, patterns)`` — check whether a topic matches a
    list of MQTT-style patterns (``+`` = one segment, ``#`` = any
    number of trailing segments). Used for the bridge's inbound /
    outbound filters.

Pure-Python, no MQTT or DDS dependencies — easy to unit-test.
"""

from __future__ import annotations

import fnmatch
from typing import Dict, Iterable, List, Tuple


# ── msg_type inference ──────────────────────────────────────────────────────
# §3.3.1 names a lane `spatialdds/<scene>/<stream>/<type>/<version>`, and this
# deployment puts the lane's registered type in the type segment, so the type
# is read off the name rather than guessed from it. Until the SDK adoption the
# names did not say what they carried (`…/vision/<cam>/frame/v1`,
# `…/fusion/track/v1`), and this module kept a table of glob patterns that
# guessed; a name the table did not know was "Unknown".
#
# Two kinds of name are not read that way:
#
# * the lane templates §2 gives two profiles, which §3.3.1 makes normative
#   where they are defined (and allows an extra segment): a PlannedTrajectory
#   "SHOULD be advertised on the topic spatialdds/<scene>/plan/<agent_id>/
#   trajectory/v1", an EntityBinding on spatialdds/<scene>/entity/binding/v1;
# * announces: on MQTT each service's announce has a topic of its own
#   (`spatialdds/<name>/discovery/announce/v1`), which is the bridges' edge
#   convention, not a lane, and the bridge handles them as their own case.
ANNOUNCE_MSG_TYPE = "spatialdds/discovery/announce"
SPEC_TEMPLATES: List[Tuple[str, str]] = [
    ("spatialdds/*/plan/*/trajectory/v*", "planned_trajectory"),
    ("spatialdds/*/entity/binding/v*", "entity_binding"),
]


def _fits(topic: str, template: str) -> bool:
    """Segment by segment, so a `*` never spans a `/`."""
    have, want = topic.split("/"), template.split("/")
    return len(have) == len(want) and all(
        h and fnmatch.fnmatchcase(h, w) for h, w in zip(have, want))


def _segments(topic: str) -> List[str]:
    parts = topic.split("/")
    if len(parts) == 5 and parts[0] == "spatialdds" and all(parts):
        return parts
    return []


def infer_msg_type(topic: str) -> str:
    """
    The type a topic's name says it carries. ``"Unknown"`` if the name is not
    a five-segment §3.3.1 lane.

    The type is not checked here (this module stays free of the registry);
    the writer refuses a name that resolves to nothing, with the reason.
    """
    if fnmatch.fnmatchcase(topic, "*/discovery/announce/*"):
        return ANNOUNCE_MSG_TYPE
    for template, type_name in SPEC_TEMPLATES:
        if _fits(topic, template):
            return type_name
    parts = _segments(topic)
    return parts[3] if parts else "Unknown"


# ── QoS / retain mapping ─────────────────────────────────────────────────────
# Returned as ``(mqtt_qos, retain)``:
#   QoS 0 = at-most-once (BEST_EFFORT-ish; high-rate sensor streams)
#   QoS 1 = at-least-once (RELIABLE-ish; decisions, plans, fused output)
#   retain=True for latched-meta semantics (TRANSIENT_LOCAL on DDS) so
#   late-joining MQTT subscribers immediately receive the latest snapshot.
# By the type a name carries, for the same reason as above.
QOS_BY_TYPE: Dict[str, Tuple[int, bool]] = {
    "video_meta": (1, True),
    "lidar_meta": (1, True),
    "radar_tensor_meta": (1, True),
    "rf_beam_meta": (1, True),
    "entity_binding": (1, True),
    ANNOUNCE_MSG_TYPE: (1, True),
    "detection3d": (1, False),
    "detection2d": (1, False),
    "fused_track": (1, False),
    "planned_trajectory": (1, False),
    "spatial_event": (1, False),
}
DEFAULT_QOS: Tuple[int, bool] = (0, False)


def get_qos(topic: str) -> Tuple[int, bool]:
    return QOS_BY_TYPE.get(infer_msg_type(topic), DEFAULT_QOS)


# ── MQTT-style topic-pattern matching ───────────────────────────────────────
# MQTT defines two wildcards: ``+`` (single level) and ``#`` (multi-level,
# trailing only). We don't try to enforce strict MQTT semantics for
# matching — the bridge's filter list is hand-curated and the few-percent
# over-match a permissive ``*`` substitution can produce is preferable to
# rolling a bespoke matcher and getting it subtly wrong.

def _mqtt_to_glob(pattern: str) -> str:
    """Convert an MQTT-style topic pattern to an fnmatch-compatible glob."""
    return pattern.replace("+", "*").replace("#", "*")


def matches_any(topic: str, patterns: Iterable[str]) -> bool:
    if not topic:
        return False
    for pattern in patterns:
        if fnmatch.fnmatchcase(topic, _mqtt_to_glob(pattern)):
            return True
    return False


def to_broker_filter(pattern: str) -> str:
    """Coarsen a topic pattern to a *valid* MQTT subscription filter.

    Per MQTT spec, the ``+`` wildcard must be a whole topic segment by
    itself — ``operator_+`` is invalid and the broker rejects the
    SUBSCRIBE. We accept richer patterns in the bridge's filter list
    (so users can write ``spatialdds/operator_+/sensing/#``) and
    coarsen them here to ``spatialdds/+/sensing/#`` for the broker
    subscription. Per-message filtering still uses the original
    pattern via ``matches_any``, so the over-subscription doesn't leak
    unwanted messages onto the DDS side.

    ``#`` is only valid as the last segment; if a caller puts it
    elsewhere we leave it alone — the broker will reject it and the
    bridge logs the error, surfacing the misconfiguration rather than
    silently rewriting it.
    """
    segments = pattern.split("/")
    coarsened = []
    for seg in segments:
        if "+" in seg and seg != "+":
            coarsened.append("+")
        else:
            coarsened.append(seg)
    return "/".join(coarsened)
