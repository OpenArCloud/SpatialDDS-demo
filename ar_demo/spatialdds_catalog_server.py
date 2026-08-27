#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from cyclonedds.domain import DomainParticipant

from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.discovery_bus import AnnouncePublisher
from spatialdds_demo.json_mapping import from_json, to_json
from spatialdds_demo.service_bus import CatalogService, CoverageService
from spatialdds_idl.oarc_demo import CatalogResponse
from spatialdds_idl.spatial.disco import (
    Announce as TypedAnnounce,
    CoverageResponse as TypedCoverageResponse,
)
from spatialdds_demo.topics import (
    TOPIC_CATALOG_QUERY_V1,
    TOPIC_DISCOVERY_ANNOUNCE_V1,
    TOPIC_DISCOVERY_QUERY_V1,
    TOPIC_SOURCE_ANNOUNCE_PREVIEW,
    TOPIC_SOURCE_REQUEST,
    TOPIC_SOURCE_SPEC,
)
from spatialdds_test import SpatialDDSLogger
from spatialdds_validation import (
    SpatialDDSValidator,
    complete_coverage_element,
    create_coverage_bbox_earth_fixed,
)


def _load_seed(path: str) -> List[Dict[str, Any]]:
    """
    Load the authored catalogue and complete its coverage elements.

    `catalog_seed.json` is hand-authored: it carries the fields a human cares
    about and omits the presence-flagged ones that are always on the wire.
    That is the right shape for authored data — it should not have to track
    every field the IDL gains. `complete_coverage_element` fills the rest in,
    so the seed does not silently stop building when the spec adds one, which
    is exactly what happened when 1.7 added `has_circle`/`circle_center`/
    `circle_radius_m` and this file was three revisions older than the IDL.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("catalog_seed.json must be a list")
    for entry in payload:
        entry["coverage"] = [
            complete_coverage_element(**element)
            for element in (entry.get("coverage") or [])
        ]
    return payload


def _seed_coverage(dataset: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    One bbox covering everything in the catalogue.

    Derived from the seed rather than configured separately, so the announce
    cannot claim an area the data does not cover — the failure the VPS's
    `geopose`-for-`vps_response` announce was: an advertisement that stopped
    matching what the service actually does, with nothing to catch it.
    """
    west = south = float("inf")
    east = north = float("-inf")
    for entry in dataset:
        for element in entry.get("coverage") or []:
            if not element.get("has_bbox"):
                continue
            w, s_, e, n = (float(v) for v in element["bbox"][:4])
            west, south = min(west, w), min(south, s_)
            east, north = max(east, e), max(north, n)
    if west > east or south > north:
        # Nothing in the seed carries a bbox; announce global coverage rather
        # than a nonsense extent, and let consumers filter on content.
        return [complete_coverage_element(**{"global": True})]
    frame_ref, element = create_coverage_bbox_earth_fixed(west, south, east, north)
    return [element]


def _catalog_announce(dataset: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    The catalogue's own `spatial::disco::Announce`.

    Without this the service was on the bus but invisible: nothing could
    discover it, the web bridge never opened a reader on its query topic, and
    a client had to already know `spatialdds/catalog/query/v1` to use it. The
    VPS has always announced; this is the same thing for content.

    `kind` is CONTENT, so `/.well-known/spatialdds/search?kind=CONTENT` finds
    it and the VPS search does not.
    """
    frame_ref, _ = create_coverage_bbox_earth_fixed(0.0, 0.0, 0.0, 0.0)
    service_id = os.getenv("SPATIALDDS_CATALOG_SERVICE_ID", "svc:content:demo/catalog")
    return {
        "service_id": service_id,
        "name": os.getenv("SPATIALDDS_CATALOG_SERVICE_NAME", "MockCatalog-v1"),
        "kind": "CONTENT",
        "version": "1.7",
        "org": os.getenv("SPATIALDDS_CATALOG_ORG", "ExampleOrg"),
        "hints": [],
        "caps": {
            "supported_profiles": [
                {"name": "spatial.core", "major": 1, "min_minor": 7, "max_minor": 7},
                {"name": "spatial.discovery", "major": 1, "min_minor": 7, "max_minor": 7},
            ],
            "preferred_profiles": [],
            "features": [],
        },
        # The query lane only. Responses go to the `reply_topic` each query
        # names, which is chosen by the client and so cannot be advertised.
        "topics": [
            {
                "name": TOPIC_CATALOG_QUERY_V1,
                "type": "oarc.catalog_query",
                "version": "v1",
                "qos_profile": "VPS_REQ",
                # Advisory hints; a request/reply lane has no steady rate and
                # no chunking, so both are zero (= unspecified).
                "target_rate_hz": 0.0,
                "max_chunk_bytes": 0,
            },
        ],
        "coverage": _seed_coverage(dataset),
        "coverage_frame_ref": frame_ref,
        "has_coverage_eval_time": False,
        "coverage_eval_time": SpatialDDSValidator.now_time(),
        "transforms": [],
        "manifest_uri": os.getenv(
            "SPATIALDDS_CATALOG_MANIFEST_URI",
            "spatialdds://catalog.example.com/zone:demo/manifest:catalog",
        ),
        "auth_hint": "",
        "stamp": SpatialDDSValidator.now_time(),
        "ttl_sec": 300,
        "coverage_source_ids": [],
    }


def _service_summary(announce: Dict[str, Any]) -> Dict[str, Any]:
    """
    The compact row a `CoverageResponse` carries (§3.3, 1.7).

    1.7 made `CoverageResponse` return `ServiceSummary` rows rather than whole
    announcements: enough to decide whether you want a service, plus the
    `manifest_uri` to resolve for the rest. Carrying topics or caps here is
    explicitly refused by the validator.
    """
    return {
        "service_id": announce["service_id"],
        "name": announce["name"],
        "kind": announce["kind"],
        "org": announce["org"],
        "manifest_uri": announce["manifest_uri"],
        "coverage": announce["coverage"],
        "coverage_frame_ref": announce["coverage_frame_ref"],
        "stamp": announce["stamp"],
        "ttl_sec": announce["ttl_sec"],
    }


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

    def serve_coverage(coverage: CoverageService, announce: Dict[str, Any]) -> None:
        """
        Typed CoverageQuery -> CoverageResponse, replying where the query asks.

        The DDS half of discovery. The catalogue answered nothing here until
        now, so an on-bus CoverageQuery found the VPS and never the content
        service — the same invisibility as having no announce, one layer up.
        """
        for query in coverage.take_queries():
            data = to_json(query)
            logger.log_message(
                "COVERAGE_QUERY", "RECV", "Client", f"Catalog:{announce['name']}",
                data, TOPIC_DISCOVERY_QUERY_V1, TOPIC_SOURCE_SPEC,
                show_message_content,
            )
            intersects = SpatialDDSValidator.check_coverage_intersection(
                data.get("coverage") or [],
                announce["coverage"],
                data.get("coverage_frame_ref"),
                announce["coverage_frame_ref"],
            )
            response = {
                "query_id": data.get("query_id", ""),
                "results": [_service_summary(announce)] if intersects else [],
                "next_page_token": "",
            }
            coverage.reply(query.reply_topic,
                           from_json(TypedCoverageResponse, response))
            logger.log_message(
                "COVERAGE_RESPONSE", "SEND", f"Catalog:{announce['name']}", "Client",
                response, query.reply_topic, TOPIC_SOURCE_REQUEST,
                show_message_content,
            )

    participant = DomainParticipant(domain_id)
    catalog = CatalogService(participant)
    coverage = CoverageService(participant)

    # Keyed, latched Announce, disposed on the way out — so a consumer learns
    # this catalogue left rather than waiting for its TTL to lapse.
    announcer = AnnouncePublisher(participant)
    announce = _catalog_announce(dataset)
    announcer.publish(from_json(TypedAnnounce, announce))
    print(f"announce topic: {TOPIC_DISCOVERY_ANNOUNCE_V1}")
    print(f"announce service_id: {announce['service_id']} (kind=CONTENT)")
    logger.log_message(
        "ANNOUNCE", "SEND", f"Catalog:{announce['name']}", "DDS_NETWORK",
        announce, TOPIC_DISCOVERY_ANNOUNCE_V1, TOPIC_SOURCE_ANNOUNCE_PREVIEW,
        show_message_content,
    )

    # An Announce is a lease, not a birth certificate: `ttl_sec` says how long
    # it stays good, and a consumer honouring it drops the service when that
    # lapses. Publishing once at startup and never again means a service that
    # is running perfectly disappears from every cache after its TTL — which
    # is what happened here, silently, once the demo had been up ten minutes.
    # Re-publish well inside the window.
    refresh_every = max(10.0, float(announce.get("ttl_sec", 300)) / 3.0)
    next_refresh = time.time() + refresh_every

    try:
        while True:
            if time.time() >= next_refresh:
                announce["stamp"] = SpatialDDSValidator.now_time()
                announcer.publish(from_json(TypedAnnounce, announce))
                next_refresh = time.time() + refresh_every
            serve(catalog)
            serve_coverage(coverage, announce)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        announcer.close()

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
