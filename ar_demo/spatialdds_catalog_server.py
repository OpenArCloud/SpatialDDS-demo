#!/usr/bin/env python3
import argparse
import json
import math
import os
import uuid
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


def _complete_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fill the presence-flagged members a hand-authored row omits.

    Same bargain as `complete_coverage_element`: the seed carries what a human
    cares about, and the builder supplies the rest so a row that predates a
    field still types. `pose` and `asset` were appended after the 1.7 review of
    this response, and the tower seed does not carry either.
    """
    entry.setdefault("has_pose", False)
    entry.setdefault("pose", {"t": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]})
    entry.setdefault("has_asset", False)
    entry.setdefault("asset", {"uri": "", "media_type": "", "hash": "", "meta": []})
    return entry


def _absolutise(entry: Dict[str, Any], base: str) -> Dict[str, Any]:
    """
    Resolve the authored asset URI against the publisher's configured base.

    `href` is relative and names no base, so it means one thing to a client
    that reached the catalogue through the web bridge and another to one
    talking to this service -- the ambiguity the spec's manifests avoid by
    being absolute. The seed stays relative because that is what is portable
    between deployments; the base is deployment configuration
    (`SPATIALDDS_ASSET_BASE`), and what goes on the wire is absolute.

    With no base configured the URI is left as authored, which is what the
    local demo wants: there the page and the asset share an origin.
    """
    asset = entry.get("asset") or {}
    uri = asset.get("uri", "")
    if base and uri and not uri.startswith(("http://", "https://", "spatialdds://")):
        asset["uri"] = f"{base.rstrip('/')}/{uri.lstrip('/')}"
    return entry


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
    base = os.getenv("SPATIALDDS_ASSET_BASE", "")
    for entry in payload:
        entry["coverage"] = [
            complete_coverage_element(**element)
            for element in (entry.get("coverage") or [])
        ]
        _absolutise(_complete_entry(entry), base)
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
    """
    lat = math.radians(float(anchor["latitude"]))
    lon = math.radians(float(anchor["longitude"]))
    h = float(anchor.get("height", 0.0))

    # WGS84.
    a, f = 6378137.0, 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    tx = (n + h) * math.cos(lat) * math.cos(lon)
    ty = (n + h) * math.cos(lat) * math.sin(lon)
    tz = (n * (1.0 - e2) + h) * math.sin(lat)

    # Columns are the ENU axes expressed in ECEF.
    sl, cl = math.sin(lon), math.cos(lon)
    sp, cp = math.sin(lat), math.cos(lat)
    r = [[-sl, -sp * cl, cp * cl],
         [cl, -sp * sl, cp * sl],
         [0.0, cp, sp]]

    # Rotation matrix to quaternion (x, y, z, w), the GeoPose order.
    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw, qx = 0.25 * s, (r[2][1] - r[1][2]) / s
        qy, qz = (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2.0
        qw, qx = (r[2][1] - r[1][2]) / s, 0.25 * s
        qy, qz = (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2.0
        qw, qx = (r[0][2] - r[2][0]) / s, (r[0][1] + r[1][0]) / s
        qy, qz = 0.25 * s, (r[1][2] + r[2][1]) / s
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2.0
        qw, qx = (r[1][0] - r[0][1]) / s, (r[0][2] + r[2][0]) / s
        qy, qz = (r[1][2] + r[2][1]) / s, 0.25 * s

    now = SpatialDDSValidator.now_time()
    return {
        "from": SpatialDDSValidator.create_frame_ref(fqn),
        # The flag is false because ECEF is not an axis convention this enum
        # can name. The member still carries its zero value, which happens to
        # be ENU -- see the note in `complete_coverage_element`.
        "to": {"uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "earth-fixed")),
               "fqn": "earth-fixed", "has_coord_convention": False,
               "coord_convention": "ENU"},
        "pose": {"t": [tx, ty, tz], "q": [qx, qy, qz, qw]},
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
        "transforms": _frame_transforms(dataset),
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
    An empty list in either lane means "match all" in that lane; the lanes
    intersect. `content_id_in` is lookup-by-id, which the catalogue could not
    answer before -- see SPEC_COMPLIANCE.
    """
    if not query.get("has_filter"):
        return True
    filters = query.get("filter") or {}

    # An id list, when given, is the narrowest thing in the query: it names
    # exactly the rows the caller wants. It intersects with kind_in rather
    # than overriding it -- an id list is not a way around a kind filter.
    ids = filters.get("content_id_in") or []
    if ids and entry.get("content_id") not in ids:
        return False

    kinds = filters.get("kind_in") or []
    if kinds and entry.get("kind") not in kinds:
        return False
    return True


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
