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
from typing import Iterable, List, Tuple


# ── msg_type inference table ────────────────────────────────────────────────
# Order matters: the FIRST pattern that matches wins. More-specific
# patterns should come before more-general ones.
TOPIC_TYPE_MAP: List[Tuple[str, str]] = [
    ("*/sensing/detection3d/*",   "oarc.detection3d_velocity"),
    ("*/ego/pose/*",              "oarc.framed_pose"),
    ("*/geo/*/pose/*",            "geopose"),
    ("*/vision/*/frame/*",        "video_frame"),
    ("*/vision/*/meta/*",         "oarc.video_frame_meta"),
    ("*/lidar/*/frame/*",         "oarc.lidar_frame"),
    ("*/lidar/*/meta/*",          "oarc.lidar_meta"),
    ("*/rad/*/frame/*",           "radar_detection"),
    ("*/rad/*/tensor/*",          "radar_tensor"),
    ("*/rad/*/meta/*",            "oarc.radar_tensor_meta"),
    ("*/rf_beam/*/frame/*",       "rf_beam"),
    ("*/rf_beam/*/meta/*",        "oarc.rf_beam_meta"),
    ("*/fusion/track/*",          "oarc.fused_track"),
    ("*/fusion/coverage/*",       "oarc.fusion_coverage"),
    ("*/plan/*/trajectory/*",     "planned_trajectory"),
    ("*/entity/binding/*",        "entity_binding"),
    ("*/events/*",                "spatial_event"),
    # Discovery is not a TopicMeta lane, so it has no registry name; the
    # bridge handles it as its own case.
    ("*/discovery/announce/*",    "spatialdds/discovery/announce"),
]


def infer_msg_type(topic: str) -> str:
    """
    Best-effort type inference for ``topic``. ``"Unknown"`` if nothing
    matches.

    Note what dropped out of this table in the typed migration: 2D
    detections, IMU samples, radio scans. Each had an invented name here and
    no §3.3.2 registration, so a consumer could not have turned any of them
    into a reader; the entries were labels this bridge told itself. Where a
    real type exists but the registry does not name it, the demo's own
    ``oarc.*`` extension name is used and the gap is on the findings list.
    """
    for pattern, msg_type in TOPIC_TYPE_MAP:
        if fnmatch.fnmatchcase(topic, pattern):
            return msg_type
    return "Unknown"


# ── QoS / retain mapping ─────────────────────────────────────────────────────
# Returned as ``(mqtt_qos, retain)``:
#   QoS 0 = at-most-once (BEST_EFFORT-ish; high-rate sensor streams)
#   QoS 1 = at-least-once (RELIABLE-ish; decisions, plans, fused output)
#   retain=True for latched-meta semantics (TRANSIENT_LOCAL on DDS) so
#   late-joining MQTT subscribers immediately receive the latest snapshot.
QOS_MAP: List[Tuple[str, Tuple[int, bool]]] = [
    ("*/meta/*",          (1, True)),
    ("*/binding/*",       (1, True)),
    ("*/discovery/*",     (1, True)),
    ("*/detection3d/*",   (1, False)),
    ("*/detection2d/*",   (1, False)),
    ("*/track/*",         (1, False)),
    ("*/trajectory/*",    (1, False)),
    ("*/events/*",        (1, False)),
    ("*/pose/*",          (0, False)),
    ("*/frame/*",         (0, False)),
    ("*/tensor/*",        (0, False)),
    ("*/scan/*",          (0, False)),
    ("*/sample/*",        (0, False)),
    ("*/coverage/*",      (0, False)),
]
DEFAULT_QOS: Tuple[int, bool] = (0, False)


def get_qos(topic: str) -> Tuple[int, bool]:
    for pattern, qos_retain in QOS_MAP:
        if fnmatch.fnmatchcase(topic, pattern):
            return qos_retain
    return DEFAULT_QOS


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
