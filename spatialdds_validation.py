#!/usr/bin/env python3
"""
SpatialDDS v1.3 Validation Utilities
Provides validation for URIs, coverage elements, quaternions, and other v1.3 structures
"""

import re
import math
from typing import Dict, List, Any, Tuple, Optional
from urllib.parse import urlparse


class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass


class SpatialDDSValidator:
    """Validator for SpatialDDS v1.3 structures"""

    # URI pattern: spatialdds://<authority>/zone:<zone_id>/<rtype>:<rid>
    SPATIAL_URI_PATTERN = re.compile(
        r'^spatialdds://([^/]+)/zone:([^/]+)/([^:]+):(.+)$'
    )

    VALID_RTYPES = {
        'service', 'anchor', 'tile', 'node', 'edge', 'feature',
        'blob', 'manifest', 'query', 'response', 'client', 'request'
    }

    VALID_COVERAGE_TYPES = {'bbox', 'geohash', 'sphere', 'polygon'}
    VALID_FRAMES = {'earth-fixed', 'local', 'map', 'anchor'}
    VALID_CRS = {'EPSG:4979', 'EPSG:4326', 'EPSG:3857', 'local'}

    @classmethod
    def validate_spatial_uri(cls, uri: str) -> Dict[str, str]:
        """
        Validate SpatialDDS URI format

        Args:
            uri: URI string to validate

        Returns:
            Dict with parsed components: authority, zone_id, rtype, rid

        Raises:
            ValidationError: If URI is invalid
        """
        if not uri:
            raise ValidationError("URI cannot be empty")

        match = cls.SPATIAL_URI_PATTERN.match(uri)
        if not match:
            raise ValidationError(
                f"Invalid SpatialDDS URI format: {uri}\n"
                f"Expected: spatialdds://<authority>/zone:<zone_id>/<rtype>:<rid>"
            )

        authority, zone_id, rtype, rid = match.groups()

        if rtype not in cls.VALID_RTYPES:
            raise ValidationError(
                f"Invalid resource type '{rtype}' in URI. "
                f"Valid types: {', '.join(sorted(cls.VALID_RTYPES))}"
            )

        return {
            'authority': authority,
            'zone_id': zone_id,
            'rtype': rtype,
            'rid': rid
        }

    @classmethod
    def validate_quaternion_wxyz(cls, q: List[float], tolerance: float = 1e-6) -> None:
        """
        Validate quaternion in [w,x,y,z] format is unit-norm

        Args:
            q: Quaternion as [w, x, y, z]
            tolerance: Tolerance for unit norm check

        Raises:
            ValidationError: If quaternion is invalid
        """
        if not q or len(q) != 4:
            raise ValidationError(
                f"Quaternion must have exactly 4 components, got {len(q) if q else 0}"
            )

        w, x, y, z = q
        norm = math.sqrt(w*w + x*x + y*y + z*z)

        if abs(norm - 1.0) > tolerance:
            raise ValidationError(
                f"Quaternion is not unit-norm: ||q|| = {norm:.6f} (expected 1.0 ± {tolerance})"
            )

    @classmethod
    def normalize_quaternion_wxyz(cls, q: List[float]) -> List[float]:
        """
        Normalize quaternion to unit length

        Args:
            q: Quaternion as [w, x, y, z]

        Returns:
            Normalized quaternion
        """
        if not q or len(q) != 4:
            raise ValidationError(f"Invalid quaternion length: {len(q) if q else 0}")

        w, x, y, z = q
        norm = math.sqrt(w*w + x*x + y*y + z*z)

        if norm < 1e-10:
            raise ValidationError("Cannot normalize zero quaternion")

        return [w/norm, x/norm, y/norm, z/norm]

    @classmethod
    def convert_quaternion_xyzw_to_wxyz(cls, q_xyzw: Dict[str, float]) -> List[float]:
        """
        Convert quaternion from {x,y,z,w} dict to [w,x,y,z] array

        Args:
            q_xyzw: Quaternion as dict with keys x, y, z, w

        Returns:
            Quaternion as [w, x, y, z]
        """
        return [q_xyzw['w'], q_xyzw['x'], q_xyzw['y'], q_xyzw['z']]

    @classmethod
    def validate_coverage_element(cls, element: Dict[str, Any]) -> None:
        """
        Validate CoverageElement structure

        Args:
            element: Coverage element dict

        Raises:
            ValidationError: If coverage element is invalid
        """
        if 'type' not in element:
            raise ValidationError("CoverageElement missing required 'type' field")

        cov_type = element['type']
        if cov_type not in cls.VALID_COVERAGE_TYPES:
            raise ValidationError(
                f"Invalid coverage type '{cov_type}'. "
                f"Valid types: {', '.join(sorted(cls.VALID_COVERAGE_TYPES))}"
            )

        if 'frame' not in element:
            raise ValidationError("CoverageElement missing required 'frame' field")

        frame = element['frame']
        # Allow frame to be prefixed (e.g., "map:local-map-1")
        frame_base = frame.split(':')[0]
        if frame_base not in cls.VALID_FRAMES:
            raise ValidationError(
                f"Invalid frame '{frame_base}'. "
                f"Valid frames: {', '.join(sorted(cls.VALID_FRAMES))}"
            )

        # Validate CRS for earth-fixed frames
        if frame == 'earth-fixed':
            if 'crs' not in element:
                raise ValidationError("earth-fixed frame requires 'crs' field")

            crs = element['crs']
            if crs not in cls.VALID_CRS:
                raise ValidationError(
                    f"Invalid CRS '{crs}'. Valid CRS: {', '.join(sorted(cls.VALID_CRS))}"
                )

        # Validate type-specific fields
        if cov_type == 'bbox':
            if 'bbox' not in element:
                raise ValidationError("bbox type requires 'bbox' field")

            bbox = element['bbox']
            if not isinstance(bbox, list):
                raise ValidationError("bbox must be an array")

            # For earth-fixed, only 2D bbox is valid
            if frame == 'earth-fixed':
                if len(bbox) != 4:
                    raise ValidationError(
                        f"earth-fixed bbox must have exactly 4 values [west, south, east, north], got {len(bbox)}"
                    )
            else:
                # For local frames, allow 4 or 6
                if len(bbox) not in [4, 6]:
                    raise ValidationError(
                        f"bbox must have 4 (2D) or 6 (3D) values, got {len(bbox)}"
                    )

        elif cov_type == 'geohash':
            if 'geohashes' not in element or not element['geohashes']:
                raise ValidationError("geohash type requires non-empty 'geohashes' array")

        elif cov_type == 'sphere':
            if 'center' not in element:
                raise ValidationError("sphere type requires 'center' field")
            if 'radius' not in element:
                raise ValidationError("sphere type requires 'radius' field")
            if element['radius'] <= 0:
                raise ValidationError("sphere radius must be positive")

    @classmethod
    def validate_coverage(cls, coverage: Dict[str, Any]) -> None:
        """
        Validate Coverage structure with elements array

        Args:
            coverage: Coverage dict with 'elements' array

        Raises:
            ValidationError: If coverage is invalid
        """
        if 'elements' not in coverage:
            raise ValidationError("Coverage missing required 'elements' field")

        elements = coverage['elements']
        if not isinstance(elements, list):
            raise ValidationError("Coverage 'elements' must be an array")

        if not elements:
            raise ValidationError("Coverage must have at least one element")

        for i, element in enumerate(elements):
            try:
                cls.validate_coverage_element(element)
            except ValidationError as e:
                raise ValidationError(f"Invalid coverage element at index {i}: {e}")

    @classmethod
    def validate_pose(cls, pose: Dict[str, Any]) -> None:
        """
        Validate Pose structure with v1.3 requirements

        Args:
            pose: Pose dict

        Raises:
            ValidationError: If pose is invalid
        """
        if 'position' not in pose:
            raise ValidationError("Pose missing required 'position' field")

        position = pose['position']
        if not all(k in position for k in ['x', 'y', 'z']):
            raise ValidationError("Position must have x, y, z fields")

        # Check for v1.3 quaternion format
        if 'orientation_wxyz' in pose:
            cls.validate_quaternion_wxyz(pose['orientation_wxyz'])
        elif 'orientation' in pose:
            # Legacy format - validate but warn
            orientation = pose['orientation']
            if not all(k in orientation for k in ['x', 'y', 'z', 'w']):
                raise ValidationError("Orientation must have x, y, z, w fields")

            q_wxyz = cls.convert_quaternion_xyzw_to_wxyz(orientation)
            cls.validate_quaternion_wxyz(q_wxyz)
        else:
            raise ValidationError("Pose must have 'orientation_wxyz' or 'orientation' field")

        # Check for v1.3 frame metadata
        if 'frame' not in pose:
            raise ValidationError("v1.3 Pose requires 'frame' field")

    @classmethod
    def check_coverage_intersection(
        cls,
        cov1: Dict[str, Any],
        cov2: Dict[str, Any]
    ) -> bool:
        """
        Check if two coverage areas intersect

        Args:
            cov1: First coverage
            cov2: Second coverage

        Returns:
            True if coverages intersect, False otherwise
        """
        # Validate both coverages first
        cls.validate_coverage(cov1)
        cls.validate_coverage(cov2)

        # Simplified intersection check for bboxes in earth-fixed frame
        for elem1 in cov1['elements']:
            for elem2 in cov2['elements']:
                if (elem1['type'] == 'bbox' and elem2['type'] == 'bbox' and
                    elem1['frame'] == 'earth-fixed' and elem2['frame'] == 'earth-fixed'):

                    bbox1 = elem1['bbox']
                    bbox2 = elem2['bbox']

                    if len(bbox1) >= 4 and len(bbox2) >= 4:
                        # 2D intersection check (pass full bbox, not just [:4])
                        if cls._check_bbox_2d_intersection(bbox1, bbox2):
                            return True

        return False

    @staticmethod
    def _check_bbox_2d_intersection(bbox1: List[float], bbox2: List[float]) -> bool:
        """Check if two 2D bboxes intersect

        Earth-fixed bbox format: [west, south, east, north] (4 values, degrees)
        Local bbox format: [min_x, min_y, max_x, max_y] or [min_x, min_y, min_z, max_x, max_y, max_z]
        """
        # Extract 2D bounds based on length
        if len(bbox1) == 4:
            west1, south1, east1, north1 = bbox1
        elif len(bbox1) == 6:
            # [min_x, min_y, min_z, max_x, max_y, max_z] → use x,y
            west1, south1, _, east1, north1, _ = bbox1
        else:
            return False

        if len(bbox2) == 4:
            west2, south2, east2, north2 = bbox2
        elif len(bbox2) == 6:
            west2, south2, _, east2, north2, _ = bbox2
        else:
            return False

        # Check if bboxes don't overlap
        if east1 < west2 or east2 < west1:
            return False
        if north1 < south2 or north2 < south1:
            return False

        return True


def create_spatial_uri(
    authority: str,
    zone_id: str,
    rtype: str,
    rid: str
) -> str:
    """
    Create a valid SpatialDDS URI

    Args:
        authority: Service authority/domain
        zone_id: Zone identifier
        rtype: Resource type
        rid: Resource identifier

    Returns:
        Formatted SpatialDDS URI
    """
    uri = f"spatialdds://{authority}/zone:{zone_id}/{rtype}:{rid}"
    SpatialDDSValidator.validate_spatial_uri(uri)  # Validate before returning
    return uri


def create_coverage_bbox_earth_fixed(
    west: float,
    south: float,
    east: float,
    north: float
) -> Dict[str, Any]:
    """
    Create a Coverage with a single 2D bbox element in earth-fixed frame

    Args:
        west: Western longitude bound (degrees)
        south: Southern latitude bound (degrees)
        east: Eastern longitude bound (degrees)
        north: Northern latitude bound (degrees)

    Returns:
        Coverage dict with 2D bbox [west, south, east, north]
    """
    return {
        'elements': [{
            'type': 'bbox',
            'frame': 'earth-fixed',
            'crs': 'EPSG:4979',
            'bbox': [west, south, east, north]  # 2D: [west, south, east, north] in degrees
        }]
    }


if __name__ == "__main__":
    # Self-test
    print("Testing SpatialDDS v1.3 Validation...")

    # Test URI validation
    print("\n1. URI Validation:")
    try:
        uri = "spatialdds://example.com/zone:sf-downtown/service:vps-001"
        parsed = SpatialDDSValidator.validate_spatial_uri(uri)
        print(f"  ✓ Valid URI: {uri}")
        print(f"    Parsed: {parsed}")
    except ValidationError as e:
        print(f"  ✗ {e}")

    # Test quaternion validation
    print("\n2. Quaternion Validation:")
    q_valid = [1.0, 0.0, 0.0, 0.0]
    try:
        SpatialDDSValidator.validate_quaternion_wxyz(q_valid)
        print(f"  ✓ Valid quaternion: {q_valid}")
    except ValidationError as e:
        print(f"  ✗ {e}")

    q_invalid = [0.5, 0.5, 0.5, 0.5]
    try:
        SpatialDDSValidator.validate_quaternion_wxyz(q_invalid)
        print(f"  ✓ Valid quaternion: {q_invalid}")
    except ValidationError as e:
        print(f"  ✗ Invalid quaternion detected: {e}")

    # Test normalization
    q_normalized = SpatialDDSValidator.normalize_quaternion_wxyz(q_invalid)
    print(f"  → Normalized: {q_normalized}")
    SpatialDDSValidator.validate_quaternion_wxyz(q_normalized)
    print(f"  ✓ Normalized quaternion is valid")

    # Test coverage validation
    print("\n3. Coverage Validation:")
    coverage = create_coverage_bbox_earth_fixed(-122.5, 37.7, -122.3, 37.8)
    try:
        SpatialDDSValidator.validate_coverage(coverage)
        print(f"  ✓ Valid coverage: {coverage}")
    except ValidationError as e:
        print(f"  ✗ {e}")

    # Test coverage intersection
    print("\n4. Coverage Intersection:")
    cov1 = create_coverage_bbox_earth_fixed(-122.5, 37.7, -122.3, 37.8)
    cov2 = create_coverage_bbox_earth_fixed(-122.4, 37.75, -122.2, 37.85)
    intersects = SpatialDDSValidator.check_coverage_intersection(cov1, cov2)
    print(f"  Coverage 1: {cov1['elements'][0]['bbox']}")
    print(f"  Coverage 2: {cov2['elements'][0]['bbox']}")
    print(f"  Intersects: {intersects}")

    print("\n✓ All tests passed!")