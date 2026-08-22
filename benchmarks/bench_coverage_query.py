#!/usr/bin/env python3
"""Benchmark 4: Coverage query response scaling with catalog size."""

from __future__ import annotations

import argparse
import json
import math
import queue
import random
import time
import uuid
from pathlib import Path
import sys
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from spatialdds_demo.dds_transport import DDSTransport
from spatialdds_demo.topics import TOPIC_CATALOG_QUERY_V1
from spatialdds_validation import SpatialDDSValidator, create_coverage_bbox_earth_fixed

from common import DDS_DOMAIN_MAIN, Stats, WARMUP_ITERATIONS, log, parse_csv_ints, write_csv

CITY_CENTER_LAT = 30.2700
CITY_CENTER_LON = -97.7400
CITY_HALF_SPAN_KM = 5.0


def _km_to_deg_lat(km: float) -> float:
    return km / 111.0


def _km_to_deg_lon(km: float, lat_deg: float) -> float:
    return km / (111.320 * max(0.1, abs(math.cos(math.radians(lat_deg)))))


def _bbox_intersects(a: List[float], b: List[float]) -> bool:
    west1, south1, east1, north1 = a
    west2, south2, east2, north2 = b
    if east1 < west2 or east2 < west1:
        return False
    if north1 < south2 or north2 < south1:
        return False
    return True


class CatalogService:
    def __init__(self, domain_id: int, entries: List[Dict[str, Any]]) -> None:
        self._entries = entries
        self._transport = DDSTransport(
            on_message_callback=self._on_message,
            domain_id=domain_id,
            local_sender_id="bench-catalog-server",
        )
        self._transport.start()

    def close(self) -> None:
        self._transport.stop()

    def _on_message(self, envelope: object) -> None:
        if envelope.msg_type != "CATALOG_QUERY":
            return
        query = json.loads(envelope.payload_json)
        query_id = query.get("query_id", "")
        reply_topic = query.get("reply_topic", "")
        if not reply_topic:
            return

        query_bbox = None
        coverage = query.get("coverage", [])
        for elem in coverage:
            if elem.get("has_bbox") and isinstance(elem.get("bbox"), list):
                query_bbox = elem.get("bbox")
                break

        results = []
        if query_bbox is not None:
            for entry in self._entries:
                for elem in entry.get("coverage", []):
                    bbox = elem.get("bbox") if elem.get("has_bbox") else None
                    if bbox and _bbox_intersects(query_bbox, bbox):
                        results.append(entry)
                        break

        response = {
            "query_id": query_id,
            "results": results,
            "next_page_token": "",
            "stamp": SpatialDDSValidator.now_time(),
        }
        self._transport.publish(reply_topic, "CATALOG_RESPONSE", json.dumps(response), query_id)


class QueryClient:
    def __init__(self, domain_id: int) -> None:
        self._inbox: "queue.Queue[object]" = queue.Queue()
        self._transport = DDSTransport(
            on_message_callback=self._on_message,
            domain_id=domain_id,
            local_sender_id="bench-catalog-client",
        )
        self._transport.start()

    def close(self) -> None:
        self._transport.stop()

    def run_once(self, bbox_elem: Dict[str, Any], frame_ref: Dict[str, Any], iteration: int) -> int:
        query_id = f"catalog-{iteration}-{uuid.uuid4().hex[:8]}"
        reply_topic = f"spatialdds/catalog/replies/bench-{uuid.uuid4().hex[:6]}/v1"
        query = {
            "query_id": query_id,
            "reply_topic": reply_topic,
            "coverage": [bbox_elem],
            "coverage_frame_ref": frame_ref,
            "has_coverage_eval_time": False,
            # Demo-local catalog filter; CoverageQuery.expr is gone in 1.7.
            "has_filter": False,
            "filter": {"kind_in": []},
            "limit": 10000,
            "page_token": "",
            "stamp": SpatialDDSValidator.now_time(),
            "ttl_sec": 30,
        }

        start_ns = time.perf_counter_ns()
        self._transport.publish(TOPIC_CATALOG_QUERY_V1, "CATALOG_QUERY", json.dumps(query), query_id)

        deadline_ns = start_ns + 5_000_000_000
        while time.perf_counter_ns() < deadline_ns:
            try:
                envelope = self._inbox.get(timeout=0.1)
            except queue.Empty:
                continue
            if envelope.msg_type == "CATALOG_RESPONSE" and envelope.request_id == query_id:
                return time.perf_counter_ns() - start_ns

        raise TimeoutError("Timeout waiting for CATALOG_RESPONSE")

    def _on_message(self, envelope: object) -> None:
        if envelope.msg_type == "CATALOG_RESPONSE":
            self._inbox.put(envelope)


def _generate_entries(num_entries: int, seed: int = 1337) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    lat_span = _km_to_deg_lat(CITY_HALF_SPAN_KM)
    lon_span = _km_to_deg_lon(CITY_HALF_SPAN_KM, CITY_CENTER_LAT)
    entries: List[Dict[str, Any]] = []

    for i in range(num_entries):
        center_lat = CITY_CENTER_LAT + rng.uniform(-lat_span, lat_span)
        center_lon = CITY_CENTER_LON + rng.uniform(-lon_span, lon_span)
        box_h_km = rng.uniform(0.05, 0.25)
        half_lat = _km_to_deg_lat(box_h_km)
        half_lon = _km_to_deg_lon(box_h_km, center_lat)

        west = center_lon - half_lon
        south = center_lat - half_lat
        east = center_lon + half_lon
        north = center_lat + half_lat
        frame_ref, coverage_elem = create_coverage_bbox_earth_fixed(west, south, east, north)

        entries.append(
            {
                "content_id": f"content-{i:05d}",
                "kind": "overlay",
                "coverage": [coverage_elem],
                "coverage_frame_ref": frame_ref,
                "updated_sec": int(time.time()),
            }
        )

    return entries


def benchmark(args: argparse.Namespace) -> None:
    entry_counts = parse_csv_ints(args.entries)
    rows: List[List[object]] = []

    query_frame_ref, query_bbox = create_coverage_bbox_earth_fixed(
        CITY_CENTER_LON - 0.01,
        CITY_CENTER_LAT - 0.01,
        CITY_CENTER_LON + 0.01,
        CITY_CENTER_LAT + 0.01,
    )

    for num_entries in entry_counts:
        entries = _generate_entries(num_entries)
        service = CatalogService(args.domain, entries)
        client = QueryClient(args.domain)

        try:
            warmups = min(args.warmup, max(1, args.iterations // 2))
            log(f"[coverage] warmup entries={num_entries} iterations={warmups}")
            for _ in range(warmups):
                client.run_once(query_bbox, query_frame_ref, -1)

            samples: List[int] = []
            log(f"[coverage] running entries={num_entries} iterations={args.iterations}")
            for i in range(1, args.iterations + 1):
                elapsed_ns = client.run_once(query_bbox, query_frame_ref, i)
                rows.append([num_entries, i, elapsed_ns])
                samples.append(elapsed_ns)

            stats = Stats.from_values(samples)
            log(
                "[coverage] summary "
                f"entries={num_entries} median_ms={stats.median / 1_000_000:.3f} "
                f"p95_ms={stats.p95 / 1_000_000:.3f}"
            )
        finally:
            client.close()
            service.close()

    write_csv(args.output, ["num_entries", "iteration", "query_time_ns"], rows)
    log(f"[coverage] wrote {len(rows)} rows to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark coverage query scaling")
    parser.add_argument("--entries", default="10,50,100,500,1000")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=WARMUP_ITERATIONS)
    parser.add_argument("--domain", type=int, default=DDS_DOMAIN_MAIN)
    parser.add_argument("--output", default="results/coverage_query.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
