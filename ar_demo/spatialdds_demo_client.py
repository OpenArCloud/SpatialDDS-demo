#!/usr/bin/env python3
import argparse
import json
import os
import queue
import sys
import time
import uuid
from typing import Any, Dict, Optional

from cyclonedds.domain import DomainParticipant

from spatialdds_demo.dds_transport import DDSTransport, require_dds_env
from spatialdds_demo.discovery_bus import AnnounceSubscriber
from spatialdds_demo.json_mapping import from_json, to_json
from spatialdds_demo.service_bus import CatalogClient, VpsClient
from spatialdds_idl.oarc_demo import CatalogQuery as TypedCatalogQuery
from spatialdds_idl.oarc_demo import VpsRequest as TypedVpsRequest
from spatialdds_demo.topics import (
    TOPIC_ANCHORS_DELTA,
    TOPIC_BOOTSTRAP_QUERY_V1,
    TOPIC_BOOTSTRAP_RESPONSE_V1,
    TOPIC_CATALOG_QUERY_V1,
    TOPIC_CATALOG_REPLIES,
    TOPIC_DISCOVERY_ANNOUNCE_V1,
    TOPIC_DISCOVERY_QUERY_V1,
    TOPIC_SOURCE_ANNOUNCE_PREVIEW,
    TOPIC_SOURCE_FALLBACK,
    TOPIC_SOURCE_MANIFEST,
    TOPIC_SOURCE_REQUEST,
    TOPIC_SOURCE_SPEC,
    TOPIC_VPS_QUERY_V1,
    TOPIC_VPS_RESULT_V1,
)
from spatialdds_test import (
    SpatialDDSClientV15,
    SpatialDDSLogger,
    _index_manifest_topics,
    _load_manifest,
    _select_topic,
)
from spatialdds_validation import SpatialDDSValidator


def _topic_source_for(manifest_topics: Dict[str, str], role: str, logical_topic: str) -> str:
    if manifest_topics.get(role) == logical_topic:
        return TOPIC_SOURCE_MANIFEST
    if logical_topic in (TOPIC_VPS_QUERY_V1, TOPIC_VPS_RESULT_V1):
        return TOPIC_SOURCE_FALLBACK
    return TOPIC_SOURCE_SPEC


def _wait_for(queue_obj: queue.Queue, msg_type: str, timeout: float) -> Optional[object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        try:
            envelope = queue_obj.get(timeout=remaining)
        except queue.Empty:
            continue
        if envelope.msg_type == msg_type:
            return envelope
    return None


def _bootstrap_domain(logger: SpatialDDSLogger, show_message_content: bool) -> Optional[int]:
    client_id = f"client-{uuid.uuid4().hex[:6]}"
    client_kind = os.getenv("SPATIALDDS_BOOTSTRAP_KIND", "robot")
    capabilities = [
        item.strip()
        for item in os.getenv("SPATIALDDS_BOOTSTRAP_CAPS", "localize,catalog").split(",")
        if item.strip()
    ]
    site = os.getenv("SPATIALDDS_BOOTSTRAP_SITE", "sf-downtown")
    query = {
        "client_id": client_id,
        "client_kind": client_kind,
        "capabilities": capabilities,
        "location_hint": site,
        "stamp": SpatialDDSValidator.now_time(),
    }

    inbox: queue.Queue = queue.Queue()

    def on_message(envelope: object) -> None:
        inbox.put(envelope)

    transport = DDSTransport(
        on_message_callback=on_message, domain_id=0, local_sender_id=client_id
    )
    transport.start()

    deadline = time.time() + 5
    response_env = None
    while time.time() < deadline and not response_env:
        transport.publish(
            TOPIC_BOOTSTRAP_QUERY_V1, "BOOTSTRAP_QUERY", json.dumps(query), client_id
        )
        logger.log_message(
            "BOOTSTRAP_QUERY",
            "SEND",
            client_id,
            "BootstrapService",
            query,
            TOPIC_BOOTSTRAP_QUERY_V1,
            TOPIC_SOURCE_SPEC,
            show_message_content,
        )
        response_env = _wait_for(inbox, "BOOTSTRAP_RESPONSE", timeout=1)

    transport.stop()
    if not response_env:
        print("Client timed out waiting for BOOTSTRAP_RESPONSE.")
        return None

    response = json.loads(response_env.payload_json)
    logger.log_message(
        "BOOTSTRAP_RESPONSE",
        "RECV",
        "BootstrapService",
        client_id,
        response,
        response_env.logical_topic,
        TOPIC_SOURCE_REQUEST,
        show_message_content,
    )

    domain = response.get("dds_domain")
    manifests = response.get("manifest_uris", [])
    if manifests:
        print(f"bootstrap: manifest_uris={', '.join(manifests)}")
    try:
        return int(domain)
    except (TypeError, ValueError):
        print(f"Invalid dds_domain in bootstrap response: {domain}")
        return None


def _announce_fresh(announce: Dict[str, Any]) -> bool:
    ttl_sec = announce.get("ttl_sec")
    stamp = announce.get("stamp")
    if not ttl_sec or not stamp:
        return True
    try:
        stamp_time = float(stamp.get("sec", 0)) + float(stamp.get("nanosec", 0)) / 1_000_000_000.0
    except (TypeError, ValueError):
        return True
    return (time.time() - stamp_time) <= float(ttl_sec) * 2.0


def run_client(show_message_content: bool, detailed_content: bool) -> int:
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content

    require_dds_env()
    print("🧭 Bootstrap phase: querying DDS domain on bootstrap domain 0")
    domain_id = _bootstrap_domain(logger, show_message_content)
    if domain_id is None:
        return 1

    client = SpatialDDSClientV15(logger)
    inbox: queue.Queue = queue.Queue()

    def on_message(envelope: object) -> None:
        inbox.put(envelope)

    transport = DDSTransport(
        on_message_callback=on_message,
        domain_id=domain_id,
        local_sender_id=client.client_ref["fqn"],
    )
    transport.start()
    # Typed announces on their own keyed topic. TRANSIENT_LOCAL means this
    # client sees services that announced before it started.
    announce_participant = DomainParticipant(domain_id)
    announce_sub = AnnounceSubscriber(announce_participant)
    print(f"announce topic: {TOPIC_DISCOVERY_ANNOUNCE_V1}")
    print("announce qos: DISCOVERY_ANNOUNCE "
          "(RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1), keyed on service_id)")

    announce = None
    deadline = time.time() + 10
    while time.time() < deadline and announce is None:
        for event in announce_sub.poll():
            if event.alive and event.announce is not None:
                candidate = to_json(event.announce)
                if _announce_fresh(candidate):
                    announce = candidate
                    break
        if announce is None:
            time.sleep(0.05)

    if not announce:
        print("Client timed out waiting for ANNOUNCE.")
        transport.stop()
        return 1
    logger.log_message(
        "ANNOUNCE",
        "RECV",
        f"VPS:{announce.get('name', 'unknown')}",
        "Client",
        announce,
        TOPIC_DISCOVERY_ANNOUNCE_V1,
        TOPIC_SOURCE_ANNOUNCE_PREVIEW,
        show_message_content,
    )

    manifest, _ = _load_manifest(announce)
    manifest_topics = _index_manifest_topics(manifest) if manifest else {}

    coverage_query = client.create_coverage_query()
    transport.publish(
        TOPIC_DISCOVERY_QUERY_V1,
        "COVERAGE_QUERY",
        json.dumps(coverage_query),
        coverage_query.get("query_id", ""),
    )
    logger.log_message(
        "COVERAGE_QUERY",
        "SEND",
        "Client",
        "DDS_NETWORK",
        coverage_query,
        TOPIC_DISCOVERY_QUERY_V1,
        TOPIC_SOURCE_SPEC,
        show_message_content,
    )

    response_env = _wait_for(inbox, "COVERAGE_RESPONSE", timeout=10)
    if not response_env:
        print("Client timed out waiting for COVERAGE_RESPONSE.")
        transport.stop()
        return 1

    coverage_response = json.loads(response_env.payload_json)
    logger.log_message(
        "COVERAGE_RESPONSE",
        "RECV",
        f"VPS:{announce.get('name', 'unknown')}",
        "Client",
        coverage_response,
        response_env.logical_topic,
        TOPIC_SOURCE_REQUEST,
        show_message_content,
    )

    # 1.7: results are compact ServiceSummary rows — no caps/topics inline.
    # Select on the summary, then take detail from the retained Announce we
    # already hold (matched by service_id); resolving manifest_uri is the
    # other route when the Announce isn't on this bus.
    summaries = coverage_response.get("results", []) or []
    service_id = announce.get("service_id", "")
    for summary in summaries:
        try:
            SpatialDDSValidator.validate_service_summary(summary)
        except Exception as exc:
            print(f"⚠️  discarding malformed ServiceSummary: {exc}")
            continue
        if summary.get("service_id") == service_id:
            print(f"discovery: selected {service_id} from ServiceSummary row")
            break
    else:
        if summaries:
            summary = summaries[0]
            service_id = summary.get("service_id", service_id)
            print(
                f"discovery: selected {service_id}; detail via "
                f"{summary.get('manifest_uri', '(no manifest_uri)')}"
            )

    loc_request = client.create_localize_request(service_id)
    loc_request_topic, loc_request_source = _select_topic(
        manifest_topics, "vps_query", TOPIC_VPS_QUERY_V1
    )
    vps = VpsClient(announce_participant)
    logger.log_message(
        "LOCALIZE_REQUEST",
        "SEND",
        "Client",
        f"VPS:{announce.get('name', 'unknown')}",
        loc_request,
        loc_request_topic,
        loc_request_source,
        show_message_content,
    )
    # Correlated by request_id on the typed reply, not by an envelope field.
    typed_response = vps.request(from_json(TypedVpsRequest, loc_request), timeout=10)
    if typed_response is None:
        print("Client timed out waiting for LOCALIZE_RESPONSE.")
        transport.stop()
        return 1

    loc_response = to_json(typed_response)
    logger.log_message(
        "LOCALIZE_RESPONSE",
        "RECV",
        f"VPS:{announce.get('name', 'unknown')}",
        "Client",
        loc_response,
        TOPIC_VPS_RESULT_V1,
        _topic_source_for(manifest_topics, "geopose", TOPIC_VPS_RESULT_V1),
        show_message_content,
    )

    if loc_response.get("quality", {}).get("success"):
        print("🔎 Phase 5: Content Discovery (catalog.CatalogQuery → CatalogResponse)")
        print("-" * 40)
        client_id = f"client-{uuid.uuid4().hex[:6]}"
        reply_topic = TOPIC_CATALOG_REPLIES(client_id)
        geopose = loc_response.get("node_geo", {}).get("geopose", {})
        catalog_query = client.create_catalog_query(
            geopose.get("lat_deg", 37.7749),
            geopose.get("lon_deg", -122.4194),
            reply_topic,
            limit=20,
            kind_in=["mesh", "poi"],
        )
        catalog_client = CatalogClient(announce_participant, reply_topic)
        logger.log_message(
            "CATALOG_QUERY",
            "SEND",
            "Client",
            "DDS_NETWORK",
            catalog_query,
            TOPIC_CATALOG_QUERY_V1,
            TOPIC_SOURCE_SPEC,
            show_message_content,
        )
        typed_catalog = catalog_client.query(
            from_json(TypedCatalogQuery, catalog_query), timeout=5
        )
        if typed_catalog is None:
            print("⚠️  catalog timeout (no CATALOG_RESPONSE)")
        else:
            catalog_response = to_json(typed_catalog)
            logger.log_message(
                "CATALOG_RESPONSE",
                "RECV",
                "Catalog:MockCatalog-v1",
                "Client",
                catalog_response,
                reply_topic,
                TOPIC_SOURCE_REQUEST,
                show_message_content,
            )
            count = len(catalog_response.get("results", []))
            next_token = catalog_response.get("next_page_token", "")
            print(
                f"✅ Content discovery: {count} results"
                f"{' (next_page_token=' + next_token + ')' if next_token else ''}"
            )

    anchor_delta = client.create_anchor_delta(
        loc_response["node_geo"], loc_response["quality"]["confidence"]
    )
    anchor_topic = (
        TOPIC_ANCHORS_DELTA(anchor_delta.get("set_id"))
        if anchor_delta.get("set_id")
        else TOPIC_ANCHORS_DELTA("unknown")
    )
    transport.publish(
        anchor_topic,
        "ANCHOR_DELTA",
        json.dumps(anchor_delta),
        "",
    )
    logger.log_message(
        "ANCHOR_DELTA",
        "SEND",
        "Client",
        "DDS_NETWORK",
        anchor_delta,
        anchor_topic,
        TOPIC_SOURCE_SPEC,
        show_message_content,
    )

    time.sleep(0.2)
    transport.stop()
    logger.print_summary()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SpatialDDS DDS Client Demo")
    parser.add_argument("--summary-only", action="store_true", help="Show only headers")
    parser.add_argument("--detailed", action="store_true", help="Show detailed content")
    args = parser.parse_args()

    show_content = not args.summary_only
    detailed = args.detailed and not args.summary_only
    return run_client(show_content, detailed)


if __name__ == "__main__":
    sys.exit(main())
