#!/usr/bin/env python3
import argparse
import json
import signal
import sys
import threading
import time
from typing import Dict

from cyclonedds.domain import DomainParticipant

from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.discovery_bus import AnnouncePublisher
from spatialdds_demo.json_mapping import from_json, to_json
from spatialdds_demo.service_bus import CoverageService, VpsService
from spatialdds_idl.spatial.argeo import VpsRequest, VpsResponse
from spatialdds_idl.spatial.disco import (
    Announce as TypedAnnounce,
    CoverageResponse as TypedCoverageResponse,
)
from spatialdds_demo.topics import (
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
    SpatialDDSLogger,
    VPSServiceV15,
    _index_manifest_topics,
    _load_manifest,
    _select_topic,
)


def _topic_source_for(manifest_topics: Dict[str, str], role: str, logical_topic: str) -> str:
    if manifest_topics.get(role) == logical_topic:
        return TOPIC_SOURCE_MANIFEST
    if logical_topic in (TOPIC_VPS_QUERY_V1, TOPIC_VPS_RESULT_V1):
        return TOPIC_SOURCE_FALLBACK
    return TOPIC_SOURCE_SPEC


def run_server(show_message_content: bool, detailed_content: bool) -> int:
    domain_id = require_dds_env()
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content

    service = VPSServiceV15(logger)
    announce = service.create_announce()
    manifest, _ = _load_manifest(announce)
    manifest_topics = _index_manifest_topics(manifest) if manifest else {}

    def serve_coverage(coverage: CoverageService) -> None:
        """Typed CoverageQuery -> CoverageResponse, replying where asked."""
        for query in coverage.take_queries():
            data = to_json(query)
            logger.log_message(
                "COVERAGE_QUERY", "RECV", "Client",
                f"VPS:{service.service_name}", data,
                TOPIC_DISCOVERY_QUERY_V1, TOPIC_SOURCE_SPEC,
                show_message_content,
            )
            response = service.handle_coverage_query(data)
            coverage.reply(query.reply_topic,
                           from_json(TypedCoverageResponse, response))
            logger.log_message(
                "COVERAGE_RESPONSE", "SEND", f"VPS:{service.service_name}",
                "Client", response, query.reply_topic,
                TOPIC_SOURCE_REQUEST, show_message_content,
            )

    def serve_localize(vps: VpsService) -> None:
        """Typed VpsRequest -> VpsResponse on the registered vps topics."""
        for request in vps.take_requests():
            data = to_json(request)
            logger.log_message(
                "LOCALIZE_REQUEST", "RECV", "Client", f"VPS:{service.service_name}",
                data, TOPIC_VPS_QUERY_V1,
                _topic_source_for(manifest_topics, "vps_query", TOPIC_VPS_QUERY_V1),
                show_message_content,
            )
            response = service.process_localize_request(data)
            vps.reply(from_json(VpsResponse, response))
            logger.log_message(
                "LOCALIZE_RESPONSE", "SEND", f"VPS:{service.service_name}", "Client",
                response, TOPIC_VPS_RESULT_V1,
                _topic_source_for(manifest_topics, "geopose", TOPIC_VPS_RESULT_V1),
                show_message_content,
            )

    # Typed, keyed Announce on its own topic. The instance is disposed on the
    # way out, so a consumer learns this service left rather than waiting for
    # its TTL to lapse.
    participant = DomainParticipant(domain_id)
    announcer = AnnouncePublisher(participant)
    vps = VpsService(participant)
    coverage = CoverageService(participant)
    announcer.publish(from_json(TypedAnnounce, announce))
    print(f"announce topic: {TOPIC_DISCOVERY_ANNOUNCE_V1}")
    print("announce qos: DISCOVERY_ANNOUNCE "
          "(RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1), keyed on service_id)")
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

    # SIGTERM matters as much as SIGINT here: `docker stop` and most process
    # supervisors send SIGTERM, and a background process started from a
    # non-interactive shell ignores SIGINT entirely. Without this the service
    # would vanish without disposing, and consumers would wait out its TTL.
    stopping = threading.Event()

    def _stop(signum, _frame):
        print(f"signal {signum}: departing {service.service_id}")
        stopping.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        while not stopping.is_set():
            serve_localize(vps)
            serve_coverage(coverage)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    # Graceful shutdown: dispose the instance (spec MUST) and publish Depart.
    announcer.close()
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
