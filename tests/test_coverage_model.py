"""
The §3.3.4 coverage model as a predicate: bbox, aabb, circle, frames.

`coverage_intersects` decides what every discovery query in the repo returns —
HTTP search, the on-bus `CoverageQuery` responder, and the catalogue server all
reach it, the latter two through
`SpatialDDSValidator.check_coverage_intersection`. It considered `bbox` alone
until this file existed, so a service announcing an `aabb` or a `circle`
matched no query anywhere: the multi-operator fusion demo, whose footprints are
circles, was undiscoverable by construction and nothing said so.

Both entry points are exercised, because the guarantee is that they are the
same predicate rather than two that currently agree.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo.discovery_http import (  # noqa: E402
    coverage_extents, coverage_intersects,
)
from spatialdds_validation import (  # noqa: E402
    SpatialDDSValidator, complete_coverage_element,
)

SF = (-122.4194, 37.7749)


def frame(fqn):
    return {"uuid": "u", "fqn": fqn, "has_coord_convention": False,
            "coord_convention": "ENU"}


def bbox(west, south, east, north, **rest):
    return complete_coverage_element(
        has_bbox=True, bbox=[west, south, east, north], **rest)


def aabb(min_x, min_y, max_x, max_y, **rest):
    return complete_coverage_element(
        has_aabb=True,
        aabb={"min_xyz": [min_x, min_y, 0.0], "max_xyz": [max_x, max_y, 0.0]},
        **rest)


def circle(cx, cy, radius_m, **rest):
    return complete_coverage_element(
        has_circle=True, circle_center=[cx, cy, 0.0],
        circle_radius_m=radius_m, **rest)


class Geometries(unittest.TestCase):
    """Each geometry a CoverageElement can carry participates in matching."""

    def test_bbox_against_bbox(self):
        self.assertTrue(coverage_intersects(
            [bbox(-122.5, 37.7, -122.3, 37.8)],
            [bbox(-122.45, 37.75, -122.35, 37.78)]))
        self.assertFalse(coverage_intersects(
            [bbox(-122.5, 37.7, -122.3, 37.8)], [bbox(2.0, 48.0, 2.5, 48.9)]))

    def test_aabb_against_aabb(self):
        """In CoverageElement since 1.4, and never matched before this."""
        local = {"has_frame_ref": True, "frame_ref": frame("scene/intersection")}
        self.assertTrue(coverage_intersects(
            [aabb(-100, -100, 100, 100, **local)],
            [aabb(-50, -50, 50, 50, **local)]))
        self.assertFalse(coverage_intersects(
            [aabb(-100, -100, -90, -90, **local)],
            [aabb(50, 50, 60, 60, **local)]))

    def test_circle_against_circle(self):
        """Added by 1.7's findings-batch-2 revision."""
        local = {"has_frame_ref": True, "frame_ref": frame("scene/intersection")}
        self.assertTrue(coverage_intersects(
            [circle(0, 0, 100, **local)], [circle(150, 0, 100, **local)]))
        self.assertFalse(coverage_intersects(
            [circle(0, 0, 10, **local)], [circle(500, 0, 10, **local)]))

    def test_geographic_circle_converts_its_radius_to_degrees(self):
        """
        `circle_radius_m` is metres even when the centre is lon/lat, so a
        geographic circle only intersects a bbox once the radius is converted.
        A metres-as-degrees reading would make a 100 m footprint span a
        continent — which is worse than not matching, because it matches
        everything.
        """
        lon, lat = SF
        here = bbox(lon - 0.0001, lat - 0.0001, lon + 0.0001, lat + 0.0001)
        self.assertTrue(coverage_intersects([here], [circle(lon, lat, 100.0)]))
        # ~1.1 km east: outside a 100 m footprint, inside a 1 km one.
        away = bbox(lon + 0.0125, lat, lon + 0.0126, lat + 0.0001)
        self.assertFalse(coverage_intersects([away], [circle(lon, lat, 100.0)]))
        self.assertTrue(coverage_intersects([away], [circle(lon, lat, 2000.0)]))

    def test_a_circle_is_approximated_by_its_bounding_box(self):
        """
        §3.3.4 permits it explicitly. Documented as a test because it is a
        visible behaviour: a query clipping the corner of a circle's bounding
        box matches even though it misses the circle.
        """
        local = {"has_frame_ref": True, "frame_ref": frame("scene/intersection")}
        corner = aabb(99, 99, 120, 120, **local)
        self.assertTrue(coverage_intersects([corner], [circle(0, 0, 100, **local)]))

    def test_mixed_geometries_take_the_union(self):
        """§3.3.4: consumers SHOULD treat the union of all regions."""
        element = complete_coverage_element(
            has_bbox=True, bbox=[-122.5, 37.7, -122.4, 37.8],
            has_circle=True, circle_center=[2.35, 48.85, 0.0],
            circle_radius_m=5000.0)
        self.assertTrue(coverage_intersects([bbox(-122.45, 37.75, -122.44, 37.76)],
                                            [element]))
        self.assertTrue(coverage_intersects([bbox(2.34, 48.84, 2.36, 48.86)],
                                            [element]))
        self.assertFalse(coverage_intersects([bbox(139.6, 35.6, 139.8, 35.8)],
                                             [element]))


class GlobalFlag(unittest.TestCase):
    """`global == true` is the explicit worldwide toggle, on either side."""

    def test_a_global_service_answers_any_query(self):
        worldwide = complete_coverage_element(**{"global": True})
        self.assertTrue(coverage_intersects([bbox(2.0, 48.0, 2.5, 48.9)], [worldwide]))

    def test_a_global_query_reaches_any_service(self):
        worldwide = complete_coverage_element(**{"global": True})
        self.assertTrue(coverage_intersects([worldwide], [bbox(2.0, 48.0, 2.5, 48.9)]))


class Frames(unittest.TestCase):
    """
    Local frames are metres, earth-fixed frames are degrees, and nothing here
    resolves a transform between them.
    """

    LOCAL = frame("scene/intersection")

    def test_a_local_footprint_does_not_match_a_lon_lat_query(self):
        """
        Comparing 500 metres against 500 degrees is not a near miss, it is a
        category error. Returning False is the honest answer until someone
        resolves the frame; the alternative is a local service matching every
        query on earth because its coordinates are small numbers.
        """
        self.assertFalse(coverage_intersects(
            [bbox(-180, -90, 180, 90)],
            [circle(0, 0, 500)],
            record_frame_ref=self.LOCAL))

    def test_the_same_local_frame_matches(self):
        self.assertTrue(coverage_intersects(
            [aabb(-10, -10, 10, 10)], [circle(0, 0, 500)],
            query_frame_ref=self.LOCAL, record_frame_ref=self.LOCAL))

    def test_two_different_local_frames_do_not_match(self):
        self.assertFalse(coverage_intersects(
            [aabb(-10, -10, 10, 10)], [circle(0, 0, 500)],
            query_frame_ref=frame("warehouse/floor2"),
            record_frame_ref=self.LOCAL))

    def test_element_frame_ref_overrides_the_announcement_frame(self):
        """§3.3.4: the element's own frame_ref wins when has_frame_ref is set."""
        element = circle(0, 0, 500, has_frame_ref=True, frame_ref=self.LOCAL)
        self.assertFalse(coverage_intersects(
            [bbox(-1, -1, 1, 1)], [element], record_frame_ref=frame("earth-fixed")))
        self.assertTrue(coverage_intersects(
            [aabb(-1, -1, 1, 1)], [element], query_frame_ref=self.LOCAL,
            record_frame_ref=frame("earth-fixed")))

    def test_an_absent_frame_reads_as_earth_fixed(self):
        """What every announcement in this repo that omits one means."""
        self.assertEqual(
            [e[0] for e in coverage_extents([bbox(-1, -1, 1, 1)])], ["earth-fixed"])


class NonFinite(unittest.TestCase):
    """§3.3.4: consumers SHALL reject non-finite coordinates."""

    def test_a_nan_bbox_is_ignored_rather_than_raising(self):
        self.assertFalse(coverage_intersects(
            [bbox(float("nan"), 0, 1, 1)], [bbox(-1, -1, 1, 1)]))

    def test_an_infinite_circle_radius_is_ignored(self):
        self.assertEqual(coverage_extents([circle(0, 0, float("inf"))]), [])

    def test_a_finite_sibling_still_matches(self):
        """One bad element must not take the whole announcement down."""
        self.assertTrue(coverage_intersects(
            [bbox(-1, -1, 1, 1)],
            [bbox(float("inf"), 0, 1, 1), bbox(-0.5, -0.5, 0.5, 0.5)]))


class SharedWithTheBus(unittest.TestCase):
    """
    The bus responder and the catalogue server call the validator; HTTP search
    calls the core. They must be the same predicate, not two that agree today.
    """

    def test_the_validator_entry_point_is_the_same_predicate(self):
        cases = [
            ([bbox(-122.5, 37.7, -122.3, 37.8)], [bbox(-122.4, 37.75, -122.35, 37.78)]),
            ([aabb(-100, -100, 100, 100)], [aabb(-50, -50, 50, 50)]),
            ([circle(0, 0, 100)], [circle(0.0005, 0, 100)]),
            ([bbox(2.0, 48.0, 2.5, 48.9)], [bbox(-122.5, 37.7, -122.3, 37.8)]),
        ]
        for query, record in cases:
            with self.subTest(query=query[0].get("has_bbox")):
                self.assertEqual(
                    SpatialDDSValidator.check_coverage_intersection(query, record),
                    coverage_intersects(query, record))

    def test_a_circle_announcement_is_now_discoverable_at_all(self):
        """
        The regression this file exists for. `multi_operator_fusion` announces
        circular coverage; before the predicate understood circles, every one
        of its services was invisible to every query.
        """
        local = frame("scene/intersection")
        fusion = circle(0.0, 0.0, 500.0)
        self.assertTrue(SpatialDDSValidator.check_coverage_intersection(
            [aabb(-600, -600, 600, 600)], [fusion], local, local))


if __name__ == "__main__":
    unittest.main()
