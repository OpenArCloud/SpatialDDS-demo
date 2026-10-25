#!/usr/bin/env python3
"""
Benchmark 4: content discovery scaling with catalogue size.

What a client waits for to learn what content is near it, as the catalogue
grows. Since 1.8 that is not a query round trip: the inventory is a latched
`ContentAnnounce` topic, so a client opens a reader, is handed every live
item, and filters to its area. Each iteration is one late-joining reader:
open, receive all N items, filter -- the whole cost of "what is here?".

Until the 1.8 migration this timed the demo's own 1.7 `CatalogQuery` ->
`CatalogResponse` round trip, against a mock server of its own, over the
envelope transport. That protocol retired; the figure's subject moved with it,
and results from before and after are not comparable.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import content_catalogue as cc
from spatialdds_demo.discovery_bus import ContentAnnouncePublisher, ContentInventory
from spatialdds.dds import from_json
from spatialdds_idl18.spatial.disco import ContentAnnounce
from spatialdds_validation import create_coverage_bbox_earth_fixed

from common import DDS_DOMAIN_MAIN, Stats, WARMUP_ITERATIONS, log, parse_csv_ints, write_csv

CITY_CENTER_LAT = 30.2700
CITY_CENTER_LON = -97.7400
CITY_HALF_SPAN_KM = 5.0
# A reader that has not received the whole inventory by then is a failure,
# not a slow sample.
DELIVERY_TIMEOUT_S = 10.0


def _km_to_deg_lat(km: float) -> float:
    return km / 111.0


def _km_to_deg_lon(km: float, lat_deg: float) -> float:
    return km / (111.320 * max(0.1, abs(math.cos(math.radians(lat_deg)))))


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
                "name": f"Overlay {i}",
                "coverage": [coverage_elem],
                "coverage_frame_ref": frame_ref,
                "updated_sec": int(time.time()),
                "has_pose": False,
                "has_asset": False,
            }
        )

    return entries


class Catalogue:
    """N items on the latched inventory topic, as the catalogue server publishes them."""

    def __init__(self, domain_id: int, entries: List[Dict[str, Any]]) -> None:
        self._participant = DomainParticipant(domain_id)
        self._publisher = ContentAnnouncePublisher(self._participant)
        for entry in entries:
            self._publisher.publish(from_json(ContentAnnounce, cc.content_announce(
                entry, provider_id="svc:content:bench/catalog")))

    def close(self) -> None:
        self._publisher.close()


def run_once(domain_id: int, num_entries: int, geopose: Dict[str, float]) -> int:
    """One late joiner: open a reader, receive all N, filter. Elapsed ns."""
    participant = DomainParticipant(domain_id)
    start = time.perf_counter_ns()
    inventory = ContentInventory(participant)
    deadline = time.monotonic() + DELIVERY_TIMEOUT_S
    items = inventory.items()
    while len(items) < num_entries:
        if time.monotonic() > deadline:
            raise TimeoutError(f"received {len(items)} of {num_entries} items")
        time.sleep(0.0005)
        items = inventory.items()
    cc.filter_inventory(items, geopose=geopose)
    return time.perf_counter_ns() - start


def benchmark(args: argparse.Namespace) -> None:
    entry_counts = parse_csv_ints(args.entries)
    rows: List[List[object]] = []
    here = {"lat_deg": CITY_CENTER_LAT, "lon_deg": CITY_CENTER_LON}

    for num_entries in entry_counts:
        catalogue = Catalogue(args.domain, _generate_entries(num_entries))
        try:
            warmups = min(args.warmup, max(1, args.iterations // 2))
            log(f"[content] warmup entries={num_entries} iterations={warmups}")
            for _ in range(warmups):
                run_once(args.domain, num_entries, here)

            samples: List[int] = []
            log(f"[content] running entries={num_entries} iterations={args.iterations}")
            for i in range(1, args.iterations + 1):
                elapsed_ns = run_once(args.domain, num_entries, here)
                rows.append([num_entries, i, elapsed_ns])
                samples.append(elapsed_ns)

            stats = Stats.from_values(samples)
            log(
                "[content] summary "
                f"entries={num_entries} median_ms={stats.median / 1_000_000:.3f} "
                f"p95_ms={stats.p95 / 1_000_000:.3f}"
            )
        finally:
            catalogue.close()

    write_csv(args.output, ["num_entries", "iteration", "inventory_time_ns"], rows)
    log(f"[content] wrote {len(rows)} rows to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark content discovery scaling")
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
