#!/usr/bin/env python3
import argparse
import json
import os
import signal
import sys
import threading
import time
from typing import Any, Dict, List

import spatialdds as sd
from spatialdds.dds import from_json, to_json

from spatialdds_demo.lanes import (
    SCENE, TOPIC_BOOTSTRAP_QUERY, TOPIC_BOOTSTRAP_RESPONSE,
    TOPIC_SOURCE_REQUEST, TOPIC_SOURCE_SPEC,
)
from spatialdds_demo.service_bus import BootstrapService
from spatialdds_demo.discovery_http import BOOTSTRAP_SCHEMA
from spatialdds_demo_idl18.oarc_demo import BootstrapResponse as TypedBootstrapResponse
from spatialdds_test import SpatialDDSLogger
from spatialdds_validation import SpatialDDSValidator


def _manifest_list(raw: str) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _mapping_for_site(site: str, default_domain: int, manifests: List[str]) -> Dict[str, Any]:
    return {
        "site": site or "default",
        "dds_domain": default_domain,
        "manifest_uris": manifests,
    }


def run_server(
    site: str, dds_domain: int, manifests: List[str], show_message_content: bool, detailed: bool
) -> int:
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed

    mapping = _mapping_for_site(site, dds_domain, manifests)
    ttl_sec = int(os.getenv("SPATIALDDS_BOOTSTRAP_TTL", "300"))

    print("🧭 Bootstrap Server (v1) starting...")
    print(f"- subscribe: {TOPIC_BOOTSTRAP_QUERY}")
    print(f"- respond:   {TOPIC_BOOTSTRAP_RESPONSE}")
    print(f"- site: {mapping['site']} domain: {mapping['dds_domain']} ttl_sec: {ttl_sec}")
    if mapping["manifest_uris"]:
        print(f"- manifest_uris: {', '.join(mapping['manifest_uris'])}")
    print("")

    def serve(bootstrap: BootstrapService) -> None:
        for query in bootstrap.take_queries():
            data = to_json(query)
            logger.log_message(
                "BOOTSTRAP_QUERY",
                "RECV",
                data.get("client_id", "Client"),
                "BootstrapService",
                data,
                TOPIC_BOOTSTRAP_QUERY,
                TOPIC_SOURCE_SPEC,
                show_message_content,
            )

            response = {
                # Demo-local bootstrap topic pair, but tagged with the 1.7
                # bootstrap schema version so it matches the manifest served
                # at /.well-known/spatialdds/bootstrap.
                "spatialdds_bootstrap": BOOTSTRAP_SCHEMA,
                "client_id": query.client_id,
                "dds_domain": mapping["dds_domain"],
                "cyclonedds_profile": os.getenv("SPATIALDDS_BOOTSTRAP_PROFILE", ""),
                "manifest_uris": mapping["manifest_uris"],
                "ttl_sec": ttl_sec,
                "stamp": SpatialDDSValidator.now_time(),
            }

            bootstrap.reply(from_json(TypedBootstrapResponse, response))
            logger.log_message(
                "BOOTSTRAP_RESPONSE",
                "SEND",
                "BootstrapService",
                query.client_id or "Client",
                response,
                TOPIC_BOOTSTRAP_RESPONSE,
                TOPIC_SOURCE_REQUEST,
                show_message_content,
            )

    # Domain 0, explicitly: it is the bootstrap domain, the one every client
    # asks on before it knows the deployment's own (`--domain` is the answer
    # it is given, not where this server listens).
    participant = sd.participant(SCENE, domain=0,
                                 service_id="svc:bootstrap:demo")
    bootstrap = BootstrapService(participant)

    # SIGTERM as well as SIGINT: `docker stop` and most supervisors send
    # SIGTERM, and a background process from a non-interactive shell ignores
    # SIGINT entirely.
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    while not stopping.is_set():
        serve(bootstrap)
        time.sleep(0.05)
    participant.close(reason="the bootstrap server stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SpatialDDS DDS Bootstrap Server")
    parser.add_argument(
        "--site",
        default=os.getenv("SPATIALDDS_BOOTSTRAP_SITE", "sf-downtown"),
        help="Site/city key for bootstrap mapping",
    )
    parser.add_argument(
        "--domain",
        type=int,
        default=int(os.getenv("SPATIALDDS_BOOTSTRAP_DOMAIN", "1")),
        help="DDS domain to return for the site",
    )
    parser.add_argument(
        "--manifest-uris",
        default=os.getenv(
            "SPATIALDDS_BOOTSTRAP_MANIFESTS",
            "spatialdds://vps.example.com/sf-downtown/service/vps",
        ),
        help="Comma-separated manifest URIs",
    )
    parser.add_argument("--summary-only", action="store_true", help="Show only headers")
    parser.add_argument("--detailed", action="store_true", help="Show detailed content")
    args = parser.parse_args()

    show_content = not args.summary_only
    manifests = _manifest_list(args.manifest_uris)
    detailed = args.detailed and not args.summary_only
    return run_server(args.site, args.domain, manifests, show_content, detailed)


if __name__ == "__main__":
    sys.exit(main())
