#!/usr/bin/env python3
import argparse
import json
import sys
import time
from typing import Dict

from spatialdds_demo.dds_transport import DDSTransport, require_dds_env
from spatialdds_demo.topics import (
    TOPIC_DISCOVERY_ANNOUNCE_V1,
    TOPIC_SOURCE_ANNOUNCE_PREVIEW,
    TOPIC_SOURCE_FALLBACK,
    TOPIC_SOURCE_MANIFEST,
    TOPIC_SOURCE_REQUEST,
    TOPIC_SOURCE_SPEC,
    TOPIC_VPS_LOCALIZE_REQUEST_V1,
    TOPIC_VPS_LOCALIZE_RESPONSE_V1,
)
from spatialdds_test import (
    SpatialDDSLogger,
    VPSServiceV14,
    _index_manifest_topics,
    _load_manifest,
    _select_topic,
)


def _topic_source_for(manifest_topics: Dict[str, str], role: str, logical_topic: str) -> str:
    if manifest_topics.get(role) == logical_topic:
        return TOPIC_SOURCE_MANIFEST
    if logical_topic in (TOPIC_VPS_LOCALIZE_REQUEST_V1, TOPIC_VPS_LOCALIZE_RESPONSE_V1):
        return TOPIC_SOURCE_FALLBACK
    return TOPIC_SOURCE_SPEC


def run_server(show_message_content: bool, detailed_content: bool) -> int:
    domain_id = require_dds_env()
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content

    service = VPSServiceV14(logger)
    announce = service.create_announce()
    manifest, _ = _load_manifest(announce)
    manifest_topics = _index_manifest_topics(manifest) if manifest else {}

    def on_message(envelope: object) -> None:
        msg_type = envelope.msg_type
        logical_topic = envelope.logical_topic
        data = json.loads(envelope.payload_json)
        request_id = envelope.request_id

        if msg_type == "COVERAGE_QUERY":
            logger.log_message(
                "COVERAGE_QUERY",
                "RECV",
                "Client",
                f"VPS:{service.service_name}",
                data,
                logical_topic,
                TOPIC_SOURCE_SPEC,
                show_message_content,
            )
            response = service.handle_coverage_query(data)
            transport.publish(
                data.get("reply_topic", ""),
                "COVERAGE_RESPONSE",
                json.dumps(response),
                request_id,
            )
            logger.log_message(
                "COVERAGE_RESPONSE",
                "SEND",
                f"VPS:{service.service_name}",
                "Client",
                response,
                data.get("reply_topic"),
                TOPIC_SOURCE_REQUEST,
                show_message_content,
            )

        if msg_type == "LOCALIZE_REQUEST":
            topic_source = _topic_source_for(manifest_topics, "localize_request", logical_topic)
            logger.log_message(
                "LOCALIZE_REQUEST",
                "RECV",
                "Client",
                f"VPS:{service.service_name}",
                data,
                logical_topic,
                topic_source,
                show_message_content,
            )
            response = service.process_localize_request(data)
            response_topic, response_source = _select_topic(
                manifest_topics, "localize_response", TOPIC_VPS_LOCALIZE_RESPONSE_V1
            )
            transport.publish(
                response_topic,
                "LOCALIZE_RESPONSE",
                json.dumps(response),
                request_id,
            )
            logger.log_message(
                "LOCALIZE_RESPONSE",
                "SEND",
                f"VPS:{service.service_name}",
                "Client",
                response,
                response_topic,
                response_source,
                show_message_content,
            )

    transport = DDSTransport(
        on_message_callback=on_message,
        domain_id=domain_id,
        local_sender_id=service.service_id,
    )
    transport.start()

    ttl_sec = int(announce.get("ttl_sec", 300) or 300)
    announce_writer = transport.create_announce_writer(ttl_sec)
    print(f"announce topic: {TOPIC_DISCOVERY_ANNOUNCE_V1}")
    print(f"announce qos: {transport.announce_qos_summary(ttl_sec)}")
    transport.publish_on(
        announce_writer,
        TOPIC_DISCOVERY_ANNOUNCE_V1,
        "ANNOUNCE",
        json.dumps(announce),
        "",
    )
    logger.log_message(
        "ANNOUNCE",
        "SEND",
        f"VPS:{service.service_name}",
        "DDS_NETWORK",
        announce,
        TOPIC_DISCOVERY_ANNOUNCE_V1,
        TOPIC_SOURCE_ANNOUNCE_PREVIEW,
        show_message_content,
    )

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    transport.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SpatialDDS DDS Server Demo")
    parser.add_argument("--summary-only", action="store_true", help="Show only headers")
    parser.add_argument("--detailed", action="store_true", help="Show detailed content")
    args = parser.parse_args()

    show_content = not args.summary_only
    detailed = args.detailed and not args.summary_only
    return run_server(show_content, detailed)


if __name__ == "__main__":
    sys.exit(main())
