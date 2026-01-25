#!/usr/bin/env python3
import argparse
import json
import os
import queue
import sys
import time
import uuid
from typing import Any, Dict, Optional

from spatialdds_demo.dds_transport import DDSTransport, require_dds_env
from spatialdds_demo.topics import (
    TOPIC_ANCHORS_DELTA,
    TOPIC_BOOTSTRAP_QUERY_V1,
    TOPIC_BOOTSTRAP_RESPONSE_V1,
    TOPIC_CATALOG_QUERY_V1,
    TOPIC_CATALOG_REPLIES,
    TOPIC_DISCOVERY_ANNOUNCE_V1,
    TOPIC_SOURCE_ANNOUNCE_PREVIEW,
    TOPIC_SOURCE_FALLBACK,
    TOPIC_SOURCE_MANIFEST,
    TOPIC_SOURCE_REQUEST,
    TOPIC_SOURCE_SPEC,
    TOPIC_VPS_COVERAGE_QUERY_V1,
    TOPIC_VPS_LOCALIZE_REQUEST_V1,
    TOPIC_VPS_LOCALIZE_RESPONSE_V1,
)
from spatialdds_test import (
    SpatialDDSClientV14,
    SpatialDDSLogger,
    _index_manifest_topics,
    _load_manifest,
    _select_topic,
)
from spatialdds_validation import SpatialDDSValidator


def _topic_source_for(manifest_topics: Dict[str, str], role: str, logical_topic: str) -> str:
    if manifest_topics.get(role) == logical_topic:
        return TOPIC_SOURCE_MANIFEST
    if logical_topic in (TOPIC_VPS_LOCALIZE_REQUEST_V1, TOPIC_VPS_LOCALIZE_RESPONSE_V1):
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
    return (time.time() - stamp_time) <= float(ttl_sec)


def run_client(show_message_content: bool, detailed_content: bool) -> int:
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content

    require_dds_env()
    print("🧭 Bootstrap phase: querying DDS domain on bootstrap domain 0")
    domain_id = _bootstrap_domain(logger, show_message_content)
    if domain_id is None:
        return 1

    client = SpatialDDSClientV14(logger)
    inbox: queue.Queue = queue.Queue()

    def on_message(envelope: object) -> None:
        inbox.put(envelope)

    transport = DDSTransport(
        on_message_callback=on_message,
        domain_id=domain_id,
        local_sender_id=client.client_ref["fqn"],
    )
    transport.start()
    announce_reader = transport.create_announce_reader(300)
    print(f"announce topic: {TOPIC_DISCOVERY_ANNOUNCE_V1}")
    print(f"announce qos: {transport.announce_qos_summary(300)}")

    announce_env = None
    announce = None
    deadline = time.time() + 10
    while time.time() < deadline:
        samples = announce_reader.take()
        if samples:
            for sample in samples:
                if not sample or sample.msg_type != "ANNOUNCE":
                    continue
                candidate = json.loads(sample.payload_json)
                if _announce_fresh(candidate):
                    announce = candidate
                    announce_env = sample
                    break
        if announce:
            break
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
        announce_env.logical_topic,
        TOPIC_SOURCE_ANNOUNCE_PREVIEW,
        show_message_content,
    )

    manifest, _ = _load_manifest(announce)
    manifest_topics = _index_manifest_topics(manifest) if manifest else {}

    coverage_query = client.create_coverage_query()
    transport.publish(
        TOPIC_VPS_COVERAGE_QUERY_V1,
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
        TOPIC_VPS_COVERAGE_QUERY_V1,
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

    loc_request = client.create_localize_request(announce.get("service_id", ""))
    loc_request_topic, loc_request_source = _select_topic(
        manifest_topics, "localize_request", TOPIC_VPS_LOCALIZE_REQUEST_V1
    )
    transport.publish(
        loc_request_topic,
        "LOCALIZE_REQUEST",
        json.dumps(loc_request),
        loc_request.get("request_id", ""),
    )
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

    loc_env = _wait_for(inbox, "LOCALIZE_RESPONSE", timeout=10)
    if not loc_env:
        print("Client timed out waiting for LOCALIZE_RESPONSE.")
        transport.stop()
        return 1

    loc_response = json.loads(loc_env.payload_json)
    loc_response_source = _topic_source_for(
        manifest_topics, "localize_response", loc_env.logical_topic
    )
    logger.log_message(
        "LOCALIZE_RESPONSE",
        "RECV",
        f"VPS:{announce.get('name', 'unknown')}",
        "Client",
        loc_response,
        loc_env.logical_topic,
        loc_response_source,
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
            expr='kind=="mesh" OR kind=="poi"',
        )
        transport.publish(
            TOPIC_CATALOG_QUERY_V1,
            "CATALOG_QUERY",
            json.dumps(catalog_query),
            catalog_query.get("query_id", ""),
        )
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

        catalog_env = _wait_for(inbox, "CATALOG_RESPONSE", timeout=3)
        if not catalog_env:
            print("⚠️  catalog timeout (no CATALOG_RESPONSE)")
        else:
            catalog_response = json.loads(catalog_env.payload_json)
            logger.log_message(
                "CATALOG_RESPONSE",
                "RECV",
                "Catalog:MockCatalog-v1",
                "Client",
                catalog_response,
                catalog_env.logical_topic,
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
