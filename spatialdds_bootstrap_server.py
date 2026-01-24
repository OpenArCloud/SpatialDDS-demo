#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from spatialdds_demo.dds_transport import DDSTransport, require_dds_env
from spatialdds_demo.topics import (
    TOPIC_BOOTSTRAP_QUERY_V1,
    TOPIC_BOOTSTRAP_RESPONSE_V1,
    TOPIC_SOURCE_REQUEST,
    TOPIC_SOURCE_SPEC,
)
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
    require_dds_env()
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed

    mapping = _mapping_for_site(site, dds_domain, manifests)
    ttl_sec = int(os.getenv("SPATIALDDS_BOOTSTRAP_TTL", "300"))

    print("🧭 Bootstrap Server (v1) starting...")
    print(f"- subscribe: {TOPIC_BOOTSTRAP_QUERY_V1}")
    print(f"- respond:   {TOPIC_BOOTSTRAP_RESPONSE_V1}")
    print(f"- site: {mapping['site']} domain: {mapping['dds_domain']} ttl_sec: {ttl_sec}")
    if mapping["manifest_uris"]:
        print(f"- manifest_uris: {', '.join(mapping['manifest_uris'])}")
    print("")

    def on_message(envelope: object) -> None:
        if envelope.msg_type != "BOOTSTRAP_QUERY":
            return
        logical_topic = envelope.logical_topic
        data = json.loads(envelope.payload_json)

        logger.log_message(
            "BOOTSTRAP_QUERY",
            "RECV",
            data.get("client_id", "Client"),
            "BootstrapService",
            data,
            logical_topic,
            TOPIC_SOURCE_SPEC,
            show_message_content,
        )

        response = {
            "client_id": data.get("client_id", ""),
            "dds_domain": mapping["dds_domain"],
            "cyclonedds_profile": os.getenv("SPATIALDDS_BOOTSTRAP_PROFILE", ""),
            "manifest_uris": mapping["manifest_uris"],
            "ttl_sec": ttl_sec,
            "stamp": SpatialDDSValidator.now_time(),
        }

        transport.publish(TOPIC_BOOTSTRAP_RESPONSE_V1, "BOOTSTRAP_RESPONSE", json.dumps(response))
        logger.log_message(
            "BOOTSTRAP_RESPONSE",
            "SEND",
            "BootstrapService",
            data.get("client_id", "Client"),
            response,
            TOPIC_BOOTSTRAP_RESPONSE_V1,
            TOPIC_SOURCE_REQUEST,
            show_message_content,
        )

    transport = DDSTransport(
        on_message_callback=on_message,
        domain_id=0,
        local_sender_id="BootstrapService",
    )
    transport.start()

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    transport.stop()
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
            "spatialdds://vps.example.com/zone:sf-downtown/manifest:vps",
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
