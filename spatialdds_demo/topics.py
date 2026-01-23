from typing import List, Optional, Tuple

TOPIC_DISCOVERY_ANNOUNCE_V1 = "spatialdds/discovery/announce/v1"
TOPIC_VPS_COVERAGE_QUERY_V1 = "spatialdds/vps/coverage/query/v1"
TOPIC_VPS_COVERAGE_REPLIES_V1 = "spatialdds/vps/coverage/replies/v1"
TOPIC_VPS_LOCALIZE_REQUEST_V1 = "spatialdds/vps/localize/request/v1"
TOPIC_VPS_LOCALIZE_RESPONSE_V1 = "spatialdds/vps/localize/response/v1"
TOPIC_DDS_ENVELOPE_V1 = "spatialdds/envelope/v1"


def TOPIC_ANCHORS_DELTA(zone: str) -> str:
    return f"spatialdds/anchors/{zone}/delta/v1"


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
        if TOPIC_VPS_LOCALIZE_REQUEST_V1 not in topics:
            errors.append("Missing localize request topic for VPS service")
        if TOPIC_VPS_LOCALIZE_RESPONSE_V1 not in topics:
            errors.append("Missing localize response topic for VPS service")

    return len(errors) == 0, errors
