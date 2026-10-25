#!/usr/bin/env python3
import argparse
import json
import math
import os
import uuid
import signal
import sys
import time
from typing import Any, Dict, List

import spatialdds as sd
from spatialdds.dds import from_json, to_json
from spatialdds.frames import enu_to_ecef_pose
from spatialdds.dds.types.spatial.disco import (
    CoverageResponse as TypedCoverageResponse,
)

from spatialdds_demo import content_catalogue as cc
from spatialdds_demo.lanes import (
    SCENE, TOPIC_SOURCE_ANNOUNCE_PREVIEW, TOPIC_SOURCE_REQUEST,
    TOPIC_SOURCE_SPEC,
)
from spatialdds_demo.service_bus import CoverageService
from spatialdds_demo.stream import announce_document
from spatialdds_test import SpatialDDSLogger
from spatialdds_validation import (
    SpatialDDSValidator,
    complete_coverage_element,
    create_coverage_bbox_earth_fixed,
)
from spatialdds_demo.payloads import ABSENT_SCALE


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


def _enu_to_ecef_transform(fqn: str, anchor: Dict[str, Any]) -> Dict[str, Any]:
    """
    A `spatial::disco::Transform` taking a local ENU frame into earth-fixed.

    A catalogue row can now say where its content sits inside a frame, which
    is only worth anything if a consumer can find that frame. Announcing the
    transform is what turns "the duck is at (11.7, -14.3, -1.4) in
    map/ut-littlefield-fountain" from folklore into something a client that
    has never seen this site can resolve. `Announce.transforms` is the slot the
    spec already provides, so no new topic and no new reader are involved.

    Per the struct's comment the pose maps FROM the local frame INTO
    earth-fixed, i.e. ECEF metres: translation is the frame origin's ECEF
    position, rotation is the ENU basis at that origin.

    The projection itself moved to `spatialdds_demo.plaza` on 2026-10-18, when
    the venue began publishing its own georeference for the same frame. The two
    are different claims and may differ --- but only through their anchors,
    which are separate on purpose. Two copies of this arithmetic drifting apart
    would be a bug dressed as a disagreement, and one of its failure modes is
    silent: an inverted direction still yields a plausible pose.
    """
    t, q = enu_to_ecef_pose(float(anchor["latitude"]),
                            float(anchor["longitude"]),
                            float(anchor.get("height", 0.0)))

    now = SpatialDDSValidator.now_time()
    return {
        "from": SpatialDDSValidator.create_frame_ref(fqn),
        # The flag is false because ECEF is not an axis convention this enum
        # can name. The member still carries its zero value, which happens to
        # be ENU -- see the note in `complete_coverage_element`.
        "to": {"uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "earth-fixed")),
               "fqn": "earth-fixed", "has_coord_convention": False,
               "coord_convention": "ENU", **ABSENT_SCALE},
        "pose": {"t": t, "q": q},
        "stamp": now,
        # The site is not going anywhere; an unbounded transform is the honest
        # statement, not a window that quietly expires.
        "has_validity": False,
        "validity": {"from": {"sec": 0, "nanosec": 0}, "seconds": 0},
    }


def _frame_transforms(dataset: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Announce a transform for every local frame the catalogue places content in."""
    path = os.getenv("SPATIALDDS_FRAME_ANCHORS", "")
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        anchors = json.load(handle)
    used = {
        (entry.get("frame_ref") or {}).get("fqn", "")
        for entry in dataset
        if entry.get("has_pose")
    }
    return [_enu_to_ecef_transform(fqn, anchors[fqn])
            for fqn in sorted(used) if fqn in anchors]


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
        "version": "1.8",
        "org": os.getenv("SPATIALDDS_CATALOG_ORG", "ExampleOrg"),
        "hints": [],
        "caps": {
            "supported_profiles": [
                {"name": "spatial.core", "major": 1, "min_minor": 8, "max_minor": 8},
                {"name": "spatial.discovery", "major": 1, "min_minor": 8, "max_minor": 8},
            ],
            "preferred_profiles": [],
            "features": [],
        },
        # The query lane only. Responses go to the `reply_topic` each query
        # names, which is chosen by the client and so cannot be advertised.
        # None. The inventory rides `spatialdds/discovery/content/v1`, a
        # well-known topic, so -- like the announce topic itself -- it is not
        # listed; and there is no query lane any more. Until the 1.8 migration
        # this advertised the demo's own `oarc.catalog_query`.
        "topics": [],
        "coverage": _seed_coverage(dataset),
        "coverage_frame_ref": frame_ref,
        "has_coverage_eval_time": False,
        "coverage_eval_time": SpatialDDSValidator.now_time(),
        "transforms": _frame_transforms(dataset),
        "manifest_uri": os.getenv(
            "SPATIALDDS_CATALOG_MANIFEST_URI",
            "spatialdds://catalog.example.com/demo/service/catalog",
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


def _start_resolver(dataset: List[Dict[str, Any]]):
    """
    The authority's resolver (§7.5.2), serving this catalogue's manifests.

    Here rather than in the bridge because this process holds the content:
    a second reader of the seed could serve a manifest that disagrees with
    the announce it is meant to back. Bound to loopback by default -- the
    bridge fronts it for browsers and rewrites ``https_base`` to its own
    public path, so nothing outside the host or task needs to reach it.

    Plain HTTP on loopback. §7.5.5 requires HTTPS for resolution; the
    public face of a deployment is the bridge, behind TLS where the
    deployment has it. Recorded in ar_demo/SPEC_COMPLIANCE.md.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    host = os.getenv("SPATIALDDS_CONTENT_RESOLVER_HOST", "127.0.0.1")
    port = int(os.getenv("SPATIALDDS_CONTENT_RESOLVER_PORT", "8097"))

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, body: Any, etag: str = "") -> None:
            raw = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            if etag:
                self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/.well-known/spatialdds/resolver":
                base = f"http://{host}:{self.server.server_address[1]}/resolve"
                return self._json(200, cc.resolver_metadata(base))
            if url.path == "/resolve":
                uri = (parse_qs(url.query).get("uri") or [""])[0]
                status, manifest, etag = cc.resolve(uri, dataset)
                if status == 200 and self.headers.get("If-None-Match") == etag:
                    return self._json(304, None, etag)
                return self._json(status, manifest if status == 200
                                  else {"error": "not found" if status == 404
                                        else "invalid SpatialDDS URI"}, etag)
            return self._json(404, {"error": "not found"})

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_server(seed_path: str, show_message_content: bool, detailed_content: bool) -> int:
    # SIGTERM as well as SIGINT, from the first line: `docker stop` sends
    # SIGTERM, and a process that only caught KeyboardInterrupt left without
    # withdrawing anything. Installed before anything is published, so a stop
    # that arrives during startup is still a clean one.
    def _terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _terminate)

    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content

    try:
        dataset = cc.load_seed(seed_path)
    except Exception as exc:
        print(f"Failed to load catalog seed: {exc}")
        return 1

    print("📚 MockCatalog Server (v1) starting...")
    print(f"- inventory: {sd.dds.registry.well_known('ContentAnnounce').topic} "
          f"(latched, keyed on content_id)")
    print(f"- manifests: spatialdds://{cc.authority()}/{cc.zone()}/content/<rid>, "
          "served by the resolver")
    print(f"- dataset: {seed_path} ({len(dataset)} entries)\n")

    def serve_coverage(coverage: CoverageService, announce: Dict[str, Any]) -> None:
        """
        Typed CoverageQuery -> CoverageResponse, replying where the query asks.

        The DDS half of discovery. The catalogue answered nothing here until
        it learned to, so an on-bus CoverageQuery found the VPS and never the
        content service -- the same invisibility as having no announce.
        """
        for query in coverage.take_queries():
            data = to_json(query)
            logger.log_message(
                "COVERAGE_QUERY", "RECV", "Client", f"Catalog:{announce['name']}",
                data, coverage.topic, TOPIC_SOURCE_SPEC,
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

    announce = _catalog_announce(dataset)
    participant = sd.participant(SCENE, service_id=announce["service_id"],
                                 manifest_uri=announce["manifest_uri"])
    # Everything after the participant exists is inside the try, so a stop
    # that arrives while the inventory is still being written withdraws
    # what was written. It used to begin at the loop, and a SIGTERM during
    # startup left without withdrawing anything (exit 130).
    resolver = None
    try:
        coverage = CoverageService(participant)

        # The inventory: one ContentAnnounce per item, latched and keyed on
        # content_id. A clean stop withdraws each item (its terminal sample, then
        # its dispose, with the reason), so a consumer learns the catalogue left
        # rather than waiting for the TTL to lapse. The floor re-publishes each one
        # with a fresh stamp inside its TTL window: an announce is a lease, not a
        # birth certificate, and this service once vanished from every cache ten
        # minutes in because it published once and never again.
        ttl_sec = int(announce.get("ttl_sec", 300))
        content = participant.writer("ContentAnnounce", on_exit="withdraw",
                                     floor_hz=3.0 / ttl_sec)

        # The participant's own announce, with the catalogue's venue frame as its
        # coverage frame and its placement transforms (spatialdds 0.7.3, S11 and
        # S14). It lists no lanes: the inventory rides the well-known content
        # topic, which an announce does not list, and replies go where each query
        # asks. tick() refreshes it inside its TTL; close() departs it.
        announce_document(participant, announce, [])
        print(f"announce topic: {sd.dds.registry.well_known('Announce').topic}")
        print(f"announce service_id: {announce['service_id']} (kind=CONTENT)")
        logger.log_message(
            "ANNOUNCE", "SEND", f"Catalog:{announce['name']}", "DDS_NETWORK",
            announce, sd.dds.registry.well_known("Announce").topic,
            TOPIC_SOURCE_ANNOUNCE_PREVIEW, show_message_content,
        )

        # A placed item's frame anchor rides with it, so a client holding only
        # this sample can place it.
        anchors = {t["from"]["fqn"]: t for t in _frame_transforms(dataset)}
        for row in dataset:
            frame = (row.get("frame_ref") or {}).get("fqn", "")
            content.write(cc.content_announce(
                row, provider_id=announce["service_id"],
                frame_anchor=anchors.get(frame) if row.get("has_pose") else None,
                ttl_sec=ttl_sec))
        print(f"content: {len(dataset)} item(s) announced on {content.topic}")
        resolver = _start_resolver(dataset)
        print("resolver: http://%s:%d/.well-known/spatialdds/resolver" % resolver.server_address)

        # An Announce is a lease, not a birth certificate: tick() re-publishes it
        # every ttl/3, as the inventory's floor re-publishes each item.
        while True:
            participant.tick()
            serve_coverage(coverage, announce)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if resolver is not None:
            resolver.shutdown()
        participant.close(reason="the catalogue stopped")

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
