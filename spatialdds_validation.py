#!/usr/bin/env python3
"""
SpatialDDS v1.4 Validation Utilities
Lightweight helpers for Time/FrameRef/Coverage/Quaternion validation aligned
with the 1.4 IDL shapes under idl/v1.4.
"""

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass


class SpatialDDSValidator:
    """Validator for SpatialDDS v1.4 structures"""

    # spatialdds://<authority>/zone:<zone_id>/<rtype>:<rid>
    SPATIAL_URI_PATTERN = (
        r"^spatialdds://([^/]+)/zone:([^/]+)/([^:]+):(.+)$"
    )

    VALID_CRS = {"EPSG:4979", "EPSG:4326"}
    VALID_COVERAGE_TYPES = {"bbox", "volume"}
    VALID_SERVICE_KINDS = {
        "VPS",
        "MAPPING",
        "RELOCAL",
        "SEMANTICS",
        "STORAGE",
        "CONTENT",
        "ANCHOR_REGISTRY",
        "OTHER",
    }

    @staticmethod
    def now_time() -> Dict[str, int]:
        """Return current time as builtin::Time dict"""
        now = datetime.now(timezone.utc)
        sec = int(now.timestamp())
        nanosec = int((now.timestamp() - sec) * 1_000_000_000)
        return {"sec": sec, "nanosec": nanosec}

    @staticmethod
    def time_from_iso(iso_str: str) -> Dict[str, int]:
        """Convert ISO8601 string to builtin::Time dict"""
        dt = datetime.fromisoformat(
            iso_str.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        sec = int(dt.timestamp())
        nanosec = int((dt.timestamp() - sec) * 1_000_000_000)
        return {"sec": sec, "nanosec": nanosec}

    @staticmethod
    def create_frame_ref(fqn: str) -> Dict[str, str]:
        """
        Deterministically create a FrameRef using UUIDv5 so the same fqn
        always yields the same uuid (useful for tests/demos).
        """
        return {
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, fqn)),
            "fqn": fqn,
        }

    @classmethod
    def validate_time(cls, t: Dict[str, Any]) -> None:
        """Validate builtin::Time"""
        if not isinstance(t, dict):
            raise ValidationError("Time must be an object")
        if "sec" not in t or "nanosec" not in t:
            raise ValidationError("Time requires 'sec' and 'nanosec'")
        if not isinstance(t["sec"], int) or not isinstance(t["nanosec"], int):
            raise ValidationError("'sec' and 'nanosec' must be integers")
        if t["nanosec"] < 0 or t["nanosec"] >= 1_000_000_000:
            raise ValidationError("nanosec must be in [0, 1e9)")

    @classmethod
    def validate_frame_ref(cls, frame_ref: Dict[str, Any]) -> None:
        """Validate spatial::common::FrameRef"""
        if not isinstance(frame_ref, dict):
            raise ValidationError("FrameRef must be an object")
        if not frame_ref.get("uuid"):
            raise ValidationError("FrameRef.uuid is required")
        if not frame_ref.get("fqn"):
            raise ValidationError("FrameRef.fqn is required")

    @classmethod
    def validate_quaternion_xyzw(
        cls, q: List[float], tolerance: float = 1e-6
    ) -> None:
        """Validate quaternion in [x,y,z,w] GeoPose order"""
        if not q or len(q) != 4:
            raise ValidationError(
                f"Quaternion must have exactly 4 components, got {len(q) if q else 0}"
            )
        x, y, z, w = q
        norm = math.sqrt(x * x + y * y + z * z + w * w)
        if abs(norm - 1.0) > tolerance:
            raise ValidationError(
                f"Quaternion is not unit-norm: ||q||={norm:.6f} (expected 1.0 ± {tolerance})"
            )

    @classmethod
    def normalize_quaternion_xyzw(cls, q: List[float]) -> List[float]:
        """Normalize quaternion and return [x,y,z,w]"""
        cls.validate_quaternion_xyzw(q, tolerance=1e-2)  # allow loose check pre-normalization
        x, y, z, w = q
        norm = math.sqrt(x * x + y * y + z * z + w * w)
        if norm < 1e-9:
            raise ValidationError("Cannot normalize near-zero quaternion")
        return [x / norm, y / norm, z / norm, w / norm]

    @classmethod
    def validate_spatial_uri(cls, uri: str) -> Dict[str, str]:
        """
        Validate SpatialDDS URI format (still used for manifests in 1.4)
        """
        import re

        if not uri:
            raise ValidationError("URI cannot be empty")
        match = re.match(cls.SPATIAL_URI_PATTERN, uri)
        if not match:
            raise ValidationError(
                f"Invalid SpatialDDS URI: {uri}\n"
                "Expected spatialdds://<authority>/zone:<zone_id>/<rtype>:<rid>"
            )
        authority, zone_id, rtype, rid = match.groups()
        return {
            "authority": authority,
            "zone_id": zone_id,
            "rtype": rtype,
            "rid": rid,
        }

    @classmethod
    def validate_coverage_element(
        cls,
        element: Dict[str, Any],
        coverage_frame_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Validate discovery::CoverageElement"""
        if "type" not in element:
            raise ValidationError("CoverageElement missing required 'type'")
        cov_type = element["type"]
        if cov_type not in cls.VALID_COVERAGE_TYPES:
            raise ValidationError(
                f"Invalid coverage type '{cov_type}'. "
                f"Valid types: {', '.join(sorted(cls.VALID_COVERAGE_TYPES))}"
            )

        if element.get("has_bbox"):
            bbox = element.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ValidationError("bbox must be an array [west,south,east,north]")
            if not all(math.isfinite(v) for v in bbox):
                raise ValidationError("bbox values must be finite numbers")
            if coverage_frame_ref and coverage_frame_ref.get("fqn") == "earth-fixed":
                if not element.get("has_crs"):
                    raise ValidationError("earth-fixed bbox requires has_crs=true and crs set")
                crs = element.get("crs")
                if crs not in cls.VALID_CRS:
                    raise ValidationError(f"Invalid CRS '{crs}' (expected one of {sorted(cls.VALID_CRS)})")

        if element.get("has_aabb"):
            aabb = element.get("aabb")
            if not isinstance(aabb, dict):
                raise ValidationError("aabb must be an object with min_xyz/max_xyz")
            if not isinstance(aabb.get("min_xyz"), list) or not isinstance(aabb.get("max_xyz"), list):
                raise ValidationError("aabb.min_xyz and aabb.max_xyz must be arrays")
            if len(aabb["min_xyz"]) != 3 or len(aabb["max_xyz"]) != 3:
                raise ValidationError("aabb vectors must have 3 components each")
            if not all(math.isfinite(v) for v in aabb["min_xyz"] + aabb["max_xyz"]):
                raise ValidationError("aabb values must be finite numbers")

        if element.get("has_frame_ref"):
            frame_ref = element.get("frame_ref")
            if not frame_ref:
                raise ValidationError("has_frame_ref is true but frame_ref missing")
            cls.validate_frame_ref(frame_ref)

        if not element.get("has_bbox") and not element.get("has_aabb"):
            raise ValidationError("CoverageElement must provide at least bbox or aabb geometry")

    @classmethod
    def validate_coverage(
        cls,
        coverage: List[Dict[str, Any]],
        coverage_frame_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Validate list of CoverageElements"""
        if not isinstance(coverage, list):
            raise ValidationError("coverage must be an array of CoverageElement")
        if not coverage:
            raise ValidationError("coverage must contain at least one element")
        for idx, elem in enumerate(coverage):
            try:
                cls.validate_coverage_element(elem, coverage_frame_ref)
            except ValidationError as exc:
                raise ValidationError(f"Coverage element {idx} invalid: {exc}")

    @classmethod
    def check_coverage_intersection(
        cls,
        coverage_a: List[Dict[str, Any]],
        coverage_b: List[Dict[str, Any]],
    ) -> bool:
        """
        Best-effort intersection check for bbox elements in matching frames.
        Returns True if any bbox pair overlaps (2D check).
        """
        for elem_a in coverage_a:
            for elem_b in coverage_b:
                if elem_a.get("has_bbox") and elem_b.get("has_bbox"):
                    if cls._bbox_intersects(elem_a["bbox"], elem_b["bbox"]):
                        return True
        return False

    @staticmethod
    def _bbox_intersects(b1: List[float], b2: List[float]) -> bool:
        west1, south1, east1, north1 = b1
        west2, south2, east2, north2 = b2
        if east1 < west2 or east2 < west1:
            return False
        if north1 < south2 or north2 < south1:
            return False
        return True


def create_coverage_bbox_earth_fixed(
    west: float,
    south: float,
    east: float,
    north: float,
    frame_ref: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Create a coverage_frame_ref + CoverageElement tuple for an earth-fixed bbox.
    Returns (coverage_frame_ref, coverage_element).
    """
    frame_ref = frame_ref or SpatialDDSValidator.create_frame_ref("earth-fixed")
    element = {
        "type": "bbox",
        "has_crs": True,
        "crs": "EPSG:4979",
        "has_bbox": True,
        "bbox": [west, south, east, north],
        "has_aabb": False,
        "global": False,
        "has_frame_ref": False,
    }
    SpatialDDSValidator.validate_frame_ref(frame_ref)
    SpatialDDSValidator.validate_coverage_element(element, frame_ref)
    return frame_ref, element


def demo_geo_pose(lat: float, lon: float, alt: float) -> Dict[str, Any]:
    """Create a simple GeoPose with ENU frame and unit quaternion"""
    q = [0.0, 0.0, 0.0, 1.0]
    SpatialDDSValidator.validate_quaternion_xyzw(q)
    return {
        "lat_deg": lat,
        "lon_deg": lon,
        "alt_m": alt,
        "q_xyzw": q,
        "frame_kind": "ENU",
        "frame_ref": SpatialDDSValidator.create_frame_ref("earth-fixed"),
        "stamp": SpatialDDSValidator.now_time(),
        "cov": "COV_NONE",
    }


if __name__ == "__main__":
    print("Testing SpatialDDS v1.4 Validation...")

    # FrameRef + Time
    fr = SpatialDDSValidator.create_frame_ref("earth-fixed")
    SpatialDDSValidator.validate_frame_ref(fr)
    now = SpatialDDSValidator.now_time()
    SpatialDDSValidator.validate_time(now)
    print(f"✓ FrameRef valid: {fr}")
    print(f"✓ Time valid: {now}")

    # Quaternion
    q = [0.0, 0.0, 0.0, 1.0]
    SpatialDDSValidator.validate_quaternion_xyzw(q)
    print(f"✓ Quaternion valid: {q}")

    # Coverage
    frame_ref, elem = create_coverage_bbox_earth_fixed(-122.52, 37.70, -122.35, 37.85)
    SpatialDDSValidator.validate_coverage([elem], frame_ref)
    print(f"✓ Coverage element valid: {elem}")

    # Intersection
    _, elem2 = create_coverage_bbox_earth_fixed(-122.50, 37.72, -122.30, 37.90)
    intersects = SpatialDDSValidator.check_coverage_intersection([elem], [elem2])
    print(f"✓ Bbox intersection: {intersects}")

    # GeoPose helper
    pose = demo_geo_pose(37.7749, -122.4194, 15.0)
    print(f"✓ GeoPose sample: {pose}")

    print("\nAll validation checks completed.")
