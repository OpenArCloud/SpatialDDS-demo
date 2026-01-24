#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from spatialdds_demo.dds_transport import DDSTransport, require_dds_env
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


def _matches_expr(entry: Dict[str, Any], expr: str) -> bool:
    if not expr:
        return True
    kinds = []
    for part in expr.split("kind=="):
        if '"' in part:
            value = part.split('"', 2)[1]
            kinds.append(value)
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

    def on_message(envelope: object) -> None:
        if envelope.msg_type != "CATALOG_QUERY":
            return
        logical_topic = envelope.logical_topic
        data = json.loads(envelope.payload_json)

        if not _ttl_ok(data.get("stamp", {}), data.get("ttl_sec", 0)):
            return

        reply_topic = data.get("reply_topic", "")
        if not reply_topic:
            return

        logger.log_message(
            "CATALOG_QUERY",
            "RECV",
            "Client",
            "Catalog:MockCatalog-v1",
            data,
            logical_topic,
            TOPIC_SOURCE_SPEC,
            show_message_content,
        )

        query_coverage = data.get("coverage", [])
        results = []
        for entry in dataset:
            if not _matches_expr(entry, data.get("expr", "")):
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
        page = results[offset : offset + limit]
        next_token = ""
        if offset + limit < len(results):
            next_token = f"o={offset + limit}"

        response = {
            "query_id": data.get("query_id", ""),
            "results": page,
            "next_page_token": next_token,
            "stamp": SpatialDDSValidator.now_time(),
        }

        transport.publish(reply_topic, "CATALOG_RESPONSE", json.dumps(response), "")
        logger.log_message(
            "CATALOG_RESPONSE",
            "SEND",
            "Catalog:MockCatalog-v1",
            "Client",
            response,
            reply_topic,
            TOPIC_SOURCE_REQUEST,
            show_message_content,
        )

        print(
            f"catalog: results={len(page)} next_page_token={next_token or 'none'}"
        )

    transport = DDSTransport(
        on_message_callback=on_message,
        domain_id=domain_id,
        local_sender_id="Catalog:MockCatalog-v1",
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
