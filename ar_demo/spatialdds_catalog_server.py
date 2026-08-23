#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from cyclonedds.domain import DomainParticipant

from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.json_mapping import from_json, to_json
from spatialdds_demo.service_bus import CatalogService
from spatialdds_idl.oarc_demo import CatalogResponse
from spatialdds_demo.topics import (
    TOPIC_CATALOG_QUERY_V1,
    TOPIC_SOURCE_REQUEST,
    TOPIC_SOURCE_SPEC,
)
from spatialdds_test import SpatialDDSLogger
from spatialdds_validation import SpatialDDSValidator


def _load_seed(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("catalog_seed.json must be a list")
    return payload


def _parse_page_token(token: str) -> int:
    if not token:
        return 0
    if token.startswith("o="):
        try:
            return max(0, int(token.split("=", 1)[1]))
        except ValueError:
            return 0
    return 0


def _matches_filter(entry: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """
    Demo-local catalog filter — NOT spec CoverageQuery.filter (which is a
    CoverageFilter of type_in/qos_profile_in/module_id_in). The catalog is a
    demo-specific protocol; it carries a structured filter in the same
    has_filter + `*_in` style so both query surfaces share one vocabulary.
    An empty kind_in means "match all".
    """
    if not query.get("has_filter"):
        return True
    kinds = (query.get("filter") or {}).get("kind_in") or []
    if not kinds:
        return True
    return entry.get("kind") in kinds


def _ttl_ok(stamp: Dict[str, Any], ttl_sec: int) -> bool:
    if not stamp or ttl_sec <= 0:
        return True
    now = time.time()
    sec = stamp.get("sec")
    nanosec = stamp.get("nanosec", 0)
    if sec is None:
        return True
    stamp_time = float(sec) + float(nanosec) / 1_000_000_000.0
    return (now - stamp_time) <= float(ttl_sec)


def run_server(seed_path: str, show_message_content: bool, detailed_content: bool) -> int:
    domain_id = require_dds_env()
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content

    try:
        dataset = _load_seed(seed_path)
    except Exception as exc:
        print(f"Failed to load catalog seed: {exc}")
        return 1

    print("📚 MockCatalog Server (v1) starting...")
    print(f"- subscribe: {TOPIC_CATALOG_QUERY_V1}")
    print(f"- dataset: {seed_path} ({len(dataset)} entries)\n")

    def serve(catalog: CatalogService) -> None:
        for query in catalog.take_queries():
            data = to_json(query)
            if not _ttl_ok(data.get("stamp", {}), data.get("ttl_sec", 0)):
                continue
            reply_topic = data.get("reply_topic", "")
            if not reply_topic:
                continue

            logger.log_message(
                "CATALOG_QUERY", "RECV", "Client", "Catalog:MockCatalog-v1",
                data, TOPIC_CATALOG_QUERY_V1, TOPIC_SOURCE_SPEC, show_message_content,
            )

            query_coverage = data.get("coverage", [])
            results = []
            for entry in dataset:
                if not _matches_filter(entry, data):
                    continue
                entry_coverage = entry.get("coverage", [])
                if query_coverage and entry_coverage:
                    if not SpatialDDSValidator.check_coverage_intersection(
                        query_coverage, entry_coverage
                    ):
                        continue
                results.append(entry)

            results.sort(
                key=lambda item: (
                    -(item.get("updated_sec") or 0),
                    item.get("content_id") or "",
                )
            )

            limit = int(data.get("limit", 20) or 20)
            offset = _parse_page_token(data.get("page_token", ""))
            page = results[offset: offset + limit]
            next_token = ""
            if offset + limit < len(results):
                next_token = f"o={offset + limit}"

            response = {
                "query_id": data.get("query_id", ""),
                "results": page,
                "next_page_token": next_token,
                "stamp": SpatialDDSValidator.now_time(),
            }
            catalog.reply(reply_topic, from_json(CatalogResponse, response))
            logger.log_message(
                "CATALOG_RESPONSE", "SEND", "Catalog:MockCatalog-v1", "Client",
                response, reply_topic, TOPIC_SOURCE_REQUEST, show_message_content,
            )
            print(f"catalog: results={len(page)} next_page_token={next_token or 'none'}")

    participant = DomainParticipant(domain_id)
    catalog = CatalogService(participant)

    try:
        while True:
            serve(catalog)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SpatialDDS Catalog Server Demo")
    parser.add_argument(
        "--seed",
        default=os.getenv("SPATIALDDS_CATALOG_SEED", "catalog_seed.json"),
        help="Path to catalog seed JSON",
    )
    parser.add_argument("--summary-only", action="store_true", help="Show only headers")
    parser.add_argument("--detailed", action="store_true", help="Show detailed content")
    args = parser.parse_args()

    show_content = not args.summary_only
    detailed = args.detailed and not args.summary_only
    return run_server(args.seed, show_content, detailed)


if __name__ == "__main__":
    sys.exit(main())
