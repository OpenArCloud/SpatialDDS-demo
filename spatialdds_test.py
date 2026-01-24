#!/usr/bin/env python3
"""
SpatialDDS Test Implementation v1.4
Demonstrates discovery (Announce/CoverageQuery), a mock localization exchange
using 1.4 primitives, and an anchor delta publication.
"""

import json
import os
import time
import uuid
import random
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
import subprocess
import sys

from spatialdds_validation import (
    SpatialDDSValidator,
    create_coverage_bbox_earth_fixed,
    demo_geo_pose,
)
from spatialdds_demo.manifest_resolver import resolve_manifest
from spatialdds_demo.topics import (
    TOPIC_ANCHORS_DELTA,
    TOPIC_BOOTSTRAP_QUERY_V1,
    TOPIC_BOOTSTRAP_RESPONSE_V1,
    TOPIC_CATALOG_QUERY_V1,
    TOPIC_CATALOG_REPLIES,
    TOPIC_DISCOVERY_ANNOUNCE_V1,
    TOPIC_SOURCE_ANNOUNCE_PREVIEW,
    TOPIC_SOURCE_FALLBACK,
    TOPIC_SOURCE_MANIFEST,
    TOPIC_SOURCE_REQUEST,
    TOPIC_SOURCE_SPEC,
    TOPIC_VPS_COVERAGE_QUERY_V1,
    TOPIC_VPS_COVERAGE_REPLIES_V1,
    TOPIC_VPS_LOCALIZE_REQUEST_V1,
    TOPIC_VPS_LOCALIZE_RESPONSE_V1,
    validate_topics_are_canonical,
)


def _time_to_iso(t: Dict[str, int]) -> str:
    dt = datetime.fromtimestamp(t["sec"] + t["nanosec"] / 1e9, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "0").lower() in {"1", "true", "yes"}


class SpatialDDSLogger:
    """Logger for detailed message tracking"""

    def __init__(self):
        self.messages: List[Dict[str, Any]] = []
        self.start_time = time.time()
        self.detailed_content = False
        self.slide_mode = _env_flag("SLIDE_MODE")

    def log_message(
        self,
        msg_type: str,
        direction: str,
        source: str,
        destination: str,
        data: Dict[str, Any],
        topic_name: Optional[str] = None,
        topic_source: Optional[str] = None,
        show_content: bool = True,
    ):
        """Log a message with timestamp and details"""
        timestamp = time.time() - self.start_time
        log_entry = {
            "timestamp": timestamp,
            "datetime": datetime.now().isoformat(),
            "type": msg_type,
            "direction": direction,
            "source": source,
            "destination": destination,
            "data": data,
            "topic": topic_name,
            "topic_source": topic_source,
            "size_bytes": len(json.dumps(data).encode("utf-8")),
        }
        self.messages.append(log_entry)

        topic_label = topic_name or "?"
        topic_source_label = topic_source or "?"
        direction_symbol = "→" if direction == "SEND" else "←"
        print(
            f"[{timestamp:6.3f}s] {direction_symbol} {msg_type}   "
            f"topic={topic_label}   topic_source={topic_source_label}"
        )
        print(f"  From: {source}")
        print(f"  To:   {destination}")
        print(f"  Size: {log_entry['size_bytes']} bytes")

        if msg_type in ["LOCALIZE_REQUEST", "LOCALIZE_RESPONSE"]:
            if "request_id" in data:
                print(f"  ID:   {data['request_id']}")
            if "quality" in data and "confidence" in data["quality"]:
                print(f"  Confidence: {data['quality']['confidence']:.3f}")

        if self.slide_mode:
            self._print_slide_fields(msg_type, data)
        elif show_content:
            display_data = self._shrink_payload(data)
            formatted_json = json.dumps(display_data, indent=4, default=str)
            for line in formatted_json.split("\n"):
                print(f"    {line}")

        print()

    def _print_slide_fields(self, msg_type: str, data: Dict[str, Any]) -> None:
        fields = []
        if msg_type == "ANNOUNCE":
            fields.append(("service_id", data.get("service_id")))
            fields.append(("manifest_uri", data.get("manifest_uri")))
            coverage = data.get("coverage", [])
            if coverage:
                bbox = coverage[0].get("bbox")
                fields.append(("coverage_bbox", bbox))
            topics = data.get("topics", [])
            if topics:
                fields.append(("qos_profile", topics[0].get("qos_profile")))
        elif msg_type in ["COVERAGE_QUERY", "COVERAGE_RESPONSE"]:
            fields.append(("query_id", data.get("query_id")))
            coverage = data.get("coverage", [])
            if coverage:
                fields.append(("coverage_bbox", coverage[0].get("bbox")))
        elif msg_type in ["LOCALIZE_REQUEST", "LOCALIZE_RESPONSE"]:
            fields.append(("request_id", data.get("request_id")))
            if msg_type == "LOCALIZE_RESPONSE":
                quality = data.get("quality", {})
                if "confidence" in quality:
                    fields.append(("confidence", f"{quality['confidence']:.3f}"))
                if "rmse_m" in quality:
                    fields.append(("rmse_m", f"{quality['rmse_m']:.3f}"))
                node_geo = data.get("node_geo", {})
                geopose = node_geo.get("geopose", {})
                if {"lat_deg", "lon_deg", "alt_m"} <= geopose.keys():
                    summary = (
                        f"lat={geopose['lat_deg']:.6f}, "
                        f"lon={geopose['lon_deg']:.6f}, "
                        f"alt={geopose['alt_m']:.2f}m"
                    )
                    fields.append(("geopose", summary))
        elif msg_type == "ANCHOR_DELTA":
            fields.append(("set_id", data.get("set_id")))
            fields.append(("op", data.get("op")))
            entry = data.get("entry", {})
            fields.append(("anchor_id", entry.get("anchor_id")))

        for key, value in fields:
            if value is not None:
                print(f"  {key}: {value}")

    def _shrink_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Trim heavy payloads for display"""
        display_data = json.loads(json.dumps(data))  # deep copy

        # Trim blobs/descriptors unless detailed mode is enabled
        if not self.detailed_content:
            if "vision_frame" in display_data:
                hdr = display_data["vision_frame"].get("hdr", {})
                blobs = hdr.get("blobs", [])
                for blob in blobs:
                    if blob.get("checksum"):
                        blob["checksum"] = blob["checksum"][:12] + "..."

            if "keyframe_features" in display_data:
                kf = display_data["keyframe_features"]
                if "descriptors" in kf and isinstance(kf["descriptors"], str):
                    if len(kf["descriptors"]) > 32:
                        kf["descriptors"] = kf["descriptors"][:32] + "...(truncated)"

        return display_data

    def print_summary(self):
        """Print communication summary"""
        print("=" * 80)
        print("SPATIALDDS COMMUNICATION SUMMARY")
        print("=" * 80)

        total_time = self.messages[-1]["timestamp"] if self.messages else 0
        total_bytes = sum(msg["size_bytes"] for msg in self.messages)

        print(f"Total Duration: {total_time:.3f}s")
        print(f"Total Messages: {len(self.messages)}")
        print(f"Total Data:     {total_bytes} bytes\n")

        msg_types: Dict[str, Dict[str, int]] = {}
        for msg in self.messages:
            msg_type = msg["type"]
            if msg_type not in msg_types:
                msg_types[msg_type] = {"count": 0, "bytes": 0}
            msg_types[msg_type]["count"] += 1
            msg_types[msg_type]["bytes"] += msg["size_bytes"]

        print("Message Types:")
        for msg_type, stats in msg_types.items():
            print(f"  {msg_type:20} {stats['count']:3d} messages, {stats['bytes']:6d} bytes")
        print()


class MockSensorData:
    """Generator for mock sensor data"""

    @staticmethod
    def blob_ref(role: str, payload: bytes) -> Dict[str, Any]:
        checksum = hashlib.sha256(payload).hexdigest()
        return {
            "blob_id": str(uuid.uuid4()),
            "role": role,
            "checksum": checksum,
        }

    @staticmethod
    def generate_features(count: int = 256) -> List[Dict[str, Any]]:
        features = []
        for i in range(count):
            features.append(
                {
                    "u": random.uniform(0, 1280),
                    "v": random.uniform(0, 960),
                    "score": random.uniform(0.5, 1.0),
                }
            )
        return features


class VPSServiceV14:
    """Mock VPS service implementing v1.4 shapes"""

    def __init__(self, logger: SpatialDDSLogger):
        self.logger = logger
        self.service_id = "svc:vps:demo/sf-downtown"
        self.service_name = "MockVPS-v1.4"
        self.manifest_uri = os.getenv(
            "SPATIALDDS_DEMO_MANIFEST_URI",
            "spatialdds://vps.example.com/zone:sf-downtown/manifest:vps",
        )
        self.coverage_frame_ref, bbox_elem = create_coverage_bbox_earth_fixed(
            -122.52, 37.70, -122.35, 37.85
        )
        volume_frame_ref = SpatialDDSValidator.create_frame_ref("map/sf-downtown")
        volume_elem = {
            "type": "volume",
            "has_crs": False,
            "has_bbox": False,
            "has_aabb": True,
            "aabb": {"min_xyz": [-100.0, -100.0, -10.0], "max_xyz": [100.0, 100.0, 40.0]},
            "global": False,
            "has_frame_ref": True,
            "frame_ref": volume_frame_ref,
        }
        self.coverage = [bbox_elem, volume_elem]
        self.transforms = []
        self.seq = 0

    def _capabilities(self) -> Dict[str, Any]:
        return {
            "supported_profiles": [
                {"name": "core", "major": 1, "min_minor": 4, "max_minor": 4, "preferred": True},
                {"name": "discovery", "major": 1, "min_minor": 4, "max_minor": 4, "preferred": True},
                {"name": "sensing.vision", "major": 1, "min_minor": 4, "max_minor": 4, "preferred": False},
                {"name": "anchors", "major": 1, "min_minor": 4, "max_minor": 4, "preferred": False},
            ],
            "preferred_profiles": ["discovery@1.4", "core@1.4"],
            "features": [{"name": "blob.crc32"}, {"name": "vision.codec.jpeg"}],
        }

    def _topic_preview(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": TOPIC_VPS_LOCALIZE_REQUEST_V1,
                "type": "vision.localize.request",
                "version": "v1",
                "qos_profile": "VIDEO_LIVE",
                "target_rate_hz": 10.0,
                "preview_only": True,
            }
        ]

    def create_announce(self) -> Dict[str, Any]:
        stamp = SpatialDDSValidator.now_time()
        announce = {
            "service_id": self.service_id,
            "name": self.service_name,
            "kind": "VPS",
            "version": "1.4",
            "org": "ExampleOrg",
            "hints": [{"key": "priority", "value": "edge"}],
            "caps": self._capabilities(),
            "topics": self._topic_preview(),
            "topics_preview_only": True,
            "coverage": self.coverage,
            "coverage_frame_ref": self.coverage_frame_ref,
            "has_coverage_eval_time": False,
            "manifest_uri": self.manifest_uri,
            "auth_hint": "oauth2:https://auth.example.com",
            "stamp": stamp,
            "ttl_sec": 300,
        }
        if self.transforms:
            announce["transforms"] = self.transforms
        SpatialDDSValidator.validate_coverage(self.coverage, self.coverage_frame_ref)
        return announce

    def handle_coverage_query(self, query: Dict[str, Any]) -> Dict[str, Any]:
        SpatialDDSValidator.validate_coverage(query["coverage"], query["coverage_frame_ref"])

        intersects = SpatialDDSValidator.check_coverage_intersection(
            query["coverage"], self.coverage
        )
        results = [self.create_announce()] if intersects else []
        response = {
            "query_id": query["query_id"],
            "results": results,
            "next_page_token": "",
        }
        return response

    def process_localize_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        # Simulate processing delay
        time.sleep(random.uniform(0.05, 0.15))
        self.seq += 1

        # Mock pose in local map frame
        pose_local = {
            "t": [random.uniform(-2, 2), random.uniform(-2, 2), random.uniform(0, 2)],
            "q_xyzw": SpatialDDSValidator.normalize_quaternion_xyzw(
                [random.uniform(-0.05, 0.05), random.uniform(-0.05, 0.05), random.uniform(-0.05, 0.05), 1.0]
            ),
        }

        geopose = demo_geo_pose(
            request["prior_geopose"]["lat_deg"] + random.uniform(-0.00005, 0.00005),
            request["prior_geopose"]["lon_deg"] + random.uniform(-0.00005, 0.00005),
            request["prior_geopose"]["alt_m"] + random.uniform(-1.0, 1.0),
        )

        node_geo = {
            "map_id": "sf-downtown-map",
            "node_id": f"node-{self.seq:04d}",
            "pose": pose_local,
            "geopose": geopose,
            "cov": "COV_NONE",
            "stamp": SpatialDDSValidator.now_time(),
            "frame_ref": SpatialDDSValidator.create_frame_ref("map/sf-downtown"),
            "source_id": self.service_id,
            "seq": self.seq,
            "graph_epoch": 1,
        }

        confidence = random.uniform(0.7, 0.95)
        accuracy = random.uniform(0.05, 0.15)
        response = {
            "request_id": request["request_id"],
            "service_id": self.service_id,
            "node_geo": node_geo,
            "quality": {
                "success": True,
                "confidence": confidence,
                "rmse_m": accuracy,
            },
            "stamp": SpatialDDSValidator.now_time(),
        }
        return response


class SpatialDDSClientV14:
    """Mock SpatialDDS client"""

    def __init__(self, logger: SpatialDDSLogger):
        self.logger = logger
        self.client_ref = SpatialDDSValidator.create_frame_ref("client/handset")
        self.stream_ref = SpatialDDSValidator.create_frame_ref("rig/front_cam")
        self.camera_id = "rig/front_cam"
        self.frame_seq = 1

    def create_coverage_query(self) -> Dict[str, Any]:
        query_id = str(uuid.uuid4())
        coverage_frame_ref, coverage_elem = create_coverage_bbox_earth_fixed(
            -122.45, 37.75, -122.40, 37.80
        )
        query = {
            "query_id": query_id,
            "coverage": [coverage_elem],
            "coverage_frame_ref": coverage_frame_ref,
            "has_coverage_eval_time": False,
            "expr": 'kind=="VPS"',
            "reply_topic": TOPIC_VPS_COVERAGE_REPLIES_V1,
            "stamp": SpatialDDSValidator.now_time(),
            "ttl_sec": 60,
        }
        return query

    def _vision_frame(self) -> Dict[str, Any]:
        stamp = SpatialDDSValidator.now_time()
        payload = f"MOCK_IMAGE_{self.frame_seq}".encode("utf-8")
        blob = MockSensorData.blob_ref("image/jpeg", payload)
        hdr = {
            "stream_id": self.stream_ref["fqn"],
            "frame_seq": self.frame_seq,
            "t_start": stamp,
            "t_end": stamp,
            "has_sensor_pose": True,
            "sensor_pose": {"t": [0.0, 0.0, 0.0], "q_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "blobs": [blob],
        }
        frame = {
            "stream_id": self.stream_ref["fqn"],
            "frame_seq": self.frame_seq,
            "hdr": hdr,
            "codec": "JPEG",
            "pix": "RGB8",
            "color": "SRGB",
            "has_line_readout_us": False,
            "rectified": True,
            "quality": {
                "has_snr_db": True,
                "snr_db": 28.0,
                "percent_valid": 99.0,
                "health": "OK",
                "note": "synthetic frame",
            },
        }
        return frame

    def _keyframe_features(self, request_id: str) -> Dict[str, Any]:
        features = MockSensorData.generate_features(300)
        descriptors = "orb32:" + hashlib.sha256(request_id.encode()).hexdigest()
        return {
            "node_id": request_id,
            "camera_id": self.camera_id,
            "desc_type": "ORB32",
            "desc_len": 32,
            "row_major": True,
            "keypoints": features,
            "descriptors": descriptors,
            "stamp": SpatialDDSValidator.now_time(),
            "source_id": self.client_ref["fqn"],
            "seq": self.frame_seq,
        }

    def create_localize_request(self, service_id: str) -> Dict[str, Any]:
        request_id = str(uuid.uuid4())
        prior = demo_geo_pose(
            37.7749 + random.uniform(-0.001, 0.001),
            -122.4194 + random.uniform(-0.001, 0.001),
            15.0 + random.uniform(-2, 2),
        )
        request = {
            "request_id": request_id,
            "client_frame_ref": self.client_ref,
            "service_id": service_id,
            "prior_geopose": prior,
            "vision_frame": self._vision_frame(),
            "keyframe_features": self._keyframe_features(request_id),
            "stamp": SpatialDDSValidator.now_time(),
            "quality_requirements": {"max_rmse_m": 0.2, "min_confidence": 0.6},
        }
        self.frame_seq += 1
        return request

    def create_anchor_delta(self, node_geo: Dict[str, Any], confidence: float) -> Dict[str, Any]:
        anchor_id = f"anchor-{uuid.uuid4()}"
        checksum = hashlib.sha256(anchor_id.encode()).hexdigest()
        delta = {
            "set_id": "sf-downtown",
            "op": "ADD",
            "entry": {
                "anchor_id": anchor_id,
                "name": "vps-anchor",
                "geopose": node_geo["geopose"],
                "confidence": confidence,
                "tags": ["vps", "demo"],
                "stamp": SpatialDDSValidator.now_time(),
                "checksum": checksum[:16],
            },
            "revision": random.randint(1, 1000),
            "stamp": SpatialDDSValidator.now_time(),
            "post_checksum": checksum,
        }
        return delta

    def create_catalog_query(
        self,
        lat_deg: float,
        lon_deg: float,
        reply_topic: str,
        limit: int = 20,
        page_token: str = "",
        expr: str = "",
    ) -> Dict[str, Any]:
        query_id = str(uuid.uuid4())
        padding = 0.005
        coverage_frame_ref, coverage_elem = create_coverage_bbox_earth_fixed(
            lon_deg - padding,
            lat_deg - padding,
            lon_deg + padding,
            lat_deg + padding,
        )
        query = {
            "query_id": query_id,
            "reply_topic": reply_topic,
            "coverage": [coverage_elem],
            "coverage_frame_ref": coverage_frame_ref,
            "has_coverage_eval_time": False,
            "expr": expr,
            "limit": limit,
            "page_token": page_token,
            "stamp": SpatialDDSValidator.now_time(),
            "ttl_sec": 30,
        }
        return query


def simulate_dds_communication(logger: SpatialDDSLogger):
    """Simulate the DDS communication layer"""
    print("🔧 Initializing SpatialDDS communication layer...")
    print("   - v1.4 typed topics + QoS metadata")
    print("   - Coverage-aware discovery")
    print("   - Blob references for heavy payloads\n")


def _index_manifest_topics(manifest: Dict[str, Any]) -> Dict[str, str]:
    indexed = {}
    for entry in manifest.get("topics", []):
        role = entry.get("role")
        name = entry.get("name")
        if role and name:
            indexed[role] = name
    return indexed


def _select_topic(
    manifest_topics: Dict[str, str],
    role: str,
    fallback: str,
) -> Tuple[str, str]:
    if role in manifest_topics:
        return manifest_topics[role], TOPIC_SOURCE_MANIFEST
    return fallback, TOPIC_SOURCE_FALLBACK


def _load_manifest(announce: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
    manifest_uri = announce.get("manifest_uri")
    if not manifest_uri:
        print("No manifest_uri provided; using spec defaults for topics")
        print("manifest_resolver: MISSING")
        print("manifest_loaded: no\n")
        return None, {"mode": "MISSING", "path": ""}

    ttl_sec = announce.get("ttl_sec", 300)
    manifest, status = resolve_manifest(manifest_uri, ttl_sec=ttl_sec)

    print(f"manifest_uri: {manifest_uri}")
    status_mode = status.get("mode", "UNKNOWN")
    status_path = status.get("path", "")
    if status_path:
        print(f"manifest_resolver: {status_mode} (path={status_path})")
    else:
        print(f"manifest_resolver: {status_mode}")
    print(f"manifest_loaded: {'yes' if manifest else 'no'}\n")

    if not manifest:
        return None, status

    manifest_service_id = manifest.get("service_id")
    if manifest_service_id != announce.get("service_id"):
        print(
            "Manifest service_id mismatch; using spec defaults for topics "
            f"(announce={announce.get('service_id')}, manifest={manifest_service_id})"
        )
        return None, status

    topic_names = [entry.get("name") for entry in manifest.get("topics", []) if entry.get("name")]
    valid, errors = validate_topics_are_canonical(topic_names, service_kind=announce.get("kind"))
    if not valid:
        print("Manifest topics failed canonical validation:")
        for error in errors:
            print(f"  - {error}")
        if _env_flag("STRICT"):
            raise ValueError("Manifest topics failed canonical validation")
        print("Falling back to spec defaults for topics")
        return None, status

    return manifest, status


def _load_catalog_seed(seed_path: str) -> List[Dict[str, Any]]:
    with open(seed_path, "r", encoding="utf-8") as handle:
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


def _manifest_list(raw: str) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


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


def _catalog_response(
    dataset: List[Dict[str, Any]],
    query: Dict[str, Any],
) -> Dict[str, Any]:
    query_coverage = query.get("coverage", [])
    results = []
    for entry in dataset:
        if not _matches_expr(entry, query.get("expr", "")):
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

    limit = int(query.get("limit", 20) or 20)
    offset = _parse_page_token(query.get("page_token", ""))
    page = results[offset : offset + limit]
    next_token = ""
    if offset + limit < len(results):
        next_token = f"o={offset + limit}"

    return {
        "query_id": query.get("query_id", ""),
        "results": page,
        "next_page_token": next_token,
        "stamp": SpatialDDSValidator.now_time(),
    }


def run_spatialdds_test(show_message_content: bool = True, detailed_content: bool = False):
    """Run comprehensive SpatialDDS test"""
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content
    anchor_zone = "sf-downtown"

    print("=" * 80)
    print("🚀 SPATIALDDS PROTOCOL TEST v1.4")
    print("=" * 80)
    print("📋 Testing SpatialDDS v1.4 features:")
    print("   • FrameRef + Time payloads")
    print("   • CoverageElement presence flags")
    print("   • discovery.Announce/CoverageQuery/Response")
    print("   • GeoPose (x,y,z,w) quaternions")
    print("   • catalog content discovery")
    print("   • AnchorDelta publication\n")

    if show_message_content:
        print("📄 Message content display: ENABLED")
        if detailed_content:
            print("📄 Detailed content mode: ENABLED (full blobs/descriptors)")
        else:
            print("📄 Detailed content mode: DISABLED (checksums truncated)")
    else:
        print("📄 Message content display: DISABLED")
    print()

    simulate_dds_communication(logger)

    canonical_topics = [
        TOPIC_BOOTSTRAP_QUERY_V1,
        TOPIC_BOOTSTRAP_RESPONSE_V1,
        TOPIC_DISCOVERY_ANNOUNCE_V1,
        TOPIC_VPS_COVERAGE_QUERY_V1,
        TOPIC_VPS_COVERAGE_REPLIES_V1,
        TOPIC_VPS_LOCALIZE_REQUEST_V1,
        TOPIC_VPS_LOCALIZE_RESPONSE_V1,
        TOPIC_ANCHORS_DELTA(anchor_zone),
    ]
    valid, errors = validate_topics_are_canonical(canonical_topics, service_kind="VPS")
    if not valid:
        print("Canonical topic validation failed:")
        for error in errors:
            print(f"  - {error}")
        if _env_flag("STRICT"):
            raise ValueError("Canonical topic validation failed")

    service = VPSServiceV14(logger)
    client = SpatialDDSClientV14(logger)

    if os.getenv("SPATIALDDS_BOOTSTRAP", "0") == "1":
        print("🧭 Phase 0: DDS Bootstrap (bootstrap.Query → bootstrap.Response)")
        print("-" * 40)
        client_id = f"client-{uuid.uuid4().hex[:6]}"
        site = os.getenv("SPATIALDDS_BOOTSTRAP_SITE", "sf-downtown")
        query = {
            "client_id": client_id,
            "client_kind": os.getenv("SPATIALDDS_BOOTSTRAP_KIND", "robot"),
            "capabilities": ["localize", "catalog"],
            "location_hint": site,
            "stamp": SpatialDDSValidator.now_time(),
        }
        logger.log_message(
            "BOOTSTRAP_QUERY",
            "SEND",
            client_id,
            "BootstrapService",
            query,
            TOPIC_BOOTSTRAP_QUERY_V1,
            TOPIC_SOURCE_SPEC,
            show_message_content,
        )
        response = {
            "client_id": client_id,
            "dds_domain": int(os.getenv("SPATIALDDS_BOOTSTRAP_DOMAIN", "1")),
            "cyclonedds_profile": "",
            "manifest_uris": _manifest_list(
                os.getenv(
                    "SPATIALDDS_BOOTSTRAP_MANIFESTS",
                    "spatialdds://vps.example.com/zone:sf-downtown/manifest:vps",
                )
            ),
            "ttl_sec": int(os.getenv("SPATIALDDS_BOOTSTRAP_TTL", "300")),
            "stamp": SpatialDDSValidator.now_time(),
        }
        logger.log_message(
            "BOOTSTRAP_RESPONSE",
            "RECV",
            "BootstrapService",
            client_id,
            response,
            TOPIC_BOOTSTRAP_RESPONSE_V1,
            TOPIC_SOURCE_REQUEST,
            show_message_content,
        )
        print(f"bootstrap: using dds_domain={response['dds_domain']}\n")

    announce = service.create_announce()
    manifest, _ = _load_manifest(announce)
    manifest_topics = _index_manifest_topics(manifest) if manifest else {}

    print("📡 Phase 1: Service Announcement (discovery.Announce)")
    print("-" * 40)
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

    time.sleep(0.25)

    print("🔍 Phase 2: Coverage Query → Response")
    print("-" * 40)
    coverage_query = client.create_coverage_query()
    logger.log_message(
        "COVERAGE_QUERY",
        "SEND",
        "Client",
        "DDS_NETWORK",
        coverage_query,
        TOPIC_VPS_COVERAGE_QUERY_V1,
        TOPIC_SOURCE_SPEC,
        show_message_content,
    )

    time.sleep(0.1)

    coverage_response = service.handle_coverage_query(coverage_query)
    logger.log_message(
        "COVERAGE_RESPONSE",
        "RECV",
        f"VPS:{service.service_name}",
        "Client",
        coverage_response,
        coverage_query.get("reply_topic"),
        TOPIC_SOURCE_REQUEST,
        show_message_content,
    )

    if not coverage_response["results"]:
        print("❌ No services matched coverage query")
        return False

    time.sleep(0.2)

    print("📤 Phase 3: Localization Request")
    print("-" * 40)
    loc_request = client.create_localize_request(service.service_id)
    loc_request_topic, loc_request_source = _select_topic(
        manifest_topics, "localize_request", TOPIC_VPS_LOCALIZE_REQUEST_V1
    )
    logger.log_message(
        "LOCALIZE_REQUEST",
        "SEND",
        "Client",
        f"VPS:{service.service_name}",
        loc_request,
        loc_request_topic,
        loc_request_source,
        show_message_content,
    )

    time.sleep(0.1)

    print("📥 Phase 4: Localization Response")
    print("-" * 40)
    loc_response = service.process_localize_request(loc_request)
    loc_response_topic, loc_response_source = _select_topic(
        manifest_topics, "localize_response", TOPIC_VPS_LOCALIZE_RESPONSE_V1
    )
    logger.log_message(
        "LOCALIZE_RESPONSE",
        "RECV",
        f"VPS:{service.service_name}",
        "Client",
        loc_response,
        loc_response_topic,
        loc_response_source,
        show_message_content,
    )

    if loc_response["quality"]["success"]:
        geopose = loc_response["node_geo"]["geopose"]
        print("✅ Localization successful!")
        print(
            f"   GeoPose: lat={geopose['lat_deg']:.7f}°, "
            f"lon={geopose['lon_deg']:.7f}°, h={geopose['alt_m']:.2f}m"
        )
        print(f"   Quaternion (x,y,z,w): {geopose['q_xyzw']}")
        print(f"   Confidence: {loc_response['quality']['confidence']:.3f}")
        print(f"   RMSE: {loc_response['quality']['rmse_m']:.3f} m")
    else:
        print("❌ Localization failed")

    time.sleep(0.2)

    print("🔎 Phase 5: Content Discovery (catalog.CatalogQuery → CatalogResponse)")
    print("-" * 40)
    seed_path = os.getenv("SPATIALDDS_CATALOG_SEED", "catalog_seed.json")
    try:
        dataset = _load_catalog_seed(seed_path)
    except Exception as exc:
        print(f"⚠️  catalog seed load failed: {exc}")
        dataset = []

    client_id = f"client-{uuid.uuid4().hex[:6]}"
    reply_topic = TOPIC_CATALOG_REPLIES(client_id)
    geopose = loc_response["node_geo"]["geopose"]
    catalog_query = client.create_catalog_query(
        geopose["lat_deg"],
        geopose["lon_deg"],
        reply_topic,
        limit=20,
        expr='kind=="mesh" OR kind=="poi"',
    )
    logger.log_message(
        "CATALOG_QUERY",
        "SEND",
        "Client",
        "DDS_NETWORK",
        catalog_query,
        TOPIC_CATALOG_QUERY_V1,
        TOPIC_SOURCE_SPEC,
        show_message_content,
    )
    catalog_response = _catalog_response(dataset, catalog_query) if dataset else None
    if catalog_response:
        logger.log_message(
            "CATALOG_RESPONSE",
            "RECV",
            "Catalog:MockCatalog-v1",
            "Client",
            catalog_response,
            reply_topic,
            TOPIC_SOURCE_REQUEST,
            show_message_content,
        )
        count = len(catalog_response.get("results", []))
        next_token = catalog_response.get("next_page_token", "")
        print(
            f"✅ Content discovery: {count} results"
            f"{' (next_page_token=' + next_token + ')' if next_token else ''}"
        )
    else:
        print("⚠️  catalog timeout (no CATALOG_RESPONSE)")

    time.sleep(0.2)

    print("🔗 Phase 6: Anchor Delta (anchors.AnchorDelta)")
    print("-" * 40)
    anchor_delta = client.create_anchor_delta(
        loc_response["node_geo"], loc_response["quality"]["confidence"]
    )
    anchor_topic = (
        TOPIC_ANCHORS_DELTA(anchor_delta.get("set_id"))
        if anchor_delta.get("set_id")
        else None
    )
    logger.log_message(
        "ANCHOR_DELTA",
        "SEND",
        "Client",
        "DDS_NETWORK",
        anchor_delta,
        anchor_topic,
        TOPIC_SOURCE_SPEC,
        show_message_content,
    )

    time.sleep(0.2)

    logger.print_summary()
    return True


def test_dds_integration():
    """Lightweight DDS tool check (best-effort)"""
    print("🔧 Testing DDS Integration")
    print("-" * 40)

    try:
        result = subprocess.run(
            ["ddsperf", "help"], capture_output=True, text=True, timeout=5
        )
        if result.returncode in (0, 3):
            print("✅ DDS tools available")
            return True
        print("⚠️  DDS tools not available, continuing without live DDS")
        return True
    except Exception:
        print("⚠️  DDS tools not available in this environment (expected in CI)")
        return True


def main():
    """Main test function"""
    import argparse

    parser = argparse.ArgumentParser(description="SpatialDDS Protocol Test v1.4")
    parser.add_argument(
        "--show-content",
        action="store_true",
        default=True,
        help="Show message content (default: True)",
    )
    parser.add_argument(
        "--hide-content",
        action="store_true",
        help="Hide message content (overrides --show-content)",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show detailed content including blobs/descriptors",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Show only message headers, no content",
    )

    args = parser.parse_args()

    if os.getenv("SPATIALDDS_TRANSPORT", "mock") == "dds":
        print("SPATIALDDS_TRANSPORT=dds is set.")
        print("Run the DDS demo with spatialdds_demo_server.py and spatialdds_demo_client.py.")
        return False

    if args.hide_content or args.summary_only:
        show_content = False
        detailed = False
    else:
        show_content = args.show_content
        detailed = args.detailed

    print("Initializing SpatialDDS Test Environment...\n")

    if not test_dds_integration():
        print("Warning: DDS integration issues detected, but continuing with protocol test...\n")

    success = run_spatialdds_test(
        show_message_content=show_content, detailed_content=detailed
    )

    print("\n🎯 Test Recommendations:")
    print("   1. Exercise multiple services to validate paging in CoverageResponse")
    print("   2. Swap mock frames with live camera blobs")
    print("   3. Emit AnchorDelta streams into a registry for persistence")
    print("   4. Validate manifests against manifests/v1.4/* examples")

    return success


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        sys.exit(1)
