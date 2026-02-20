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


def TOPIC_ANCHORS_DELTA(zone: str) -> str:
    return f"spatialdds/anchors/{zone}/delta/v1"


def TOPIC_CATALOG_REPLIES(client_id: str) -> str:
    return f"spatialdds/catalog/replies/{client_id}/v1"


def TOPIC_DISCOVERY_RESPONSE(query_id: str) -> str:
    return f"spatialdds/discovery/response/{query_id}"


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
