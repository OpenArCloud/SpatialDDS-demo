"""
The catalogue filter, at the one place it is applied.

There used to be two copies of this matcher -- the 1.7 catalogue server and
the conformance harness -- differing only in how they grew; then one shared
module both imported. Since the 1.8 migration there is one caller as well:
`content_catalogue.filter_inventory`, which every consumer of the inventory
goes through. The suite runs its cases through that call site and keeps the
check that matters after consolidation: that it has not quietly grown a local
copy. `is` rather than `==`, because a re-implementation that happens to agree
today is exactly what drifts tomorrow.

`content_id_in` is lookup-by-id. Before it, a `catalog:<content_id>` reference
was resolvable only by a client that had already queried the right area --
reference-by-id existed and lookup-by-id did not. See SPEC_COMPLIANCE.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DUCK = "89f2d953-076d-5c7d-9b74-1193f71685a6"
FOUNTAIN = "11111111-2222-3333-4444-555555555555"

ENTRIES = [
    {"content_id": DUCK, "kind": "model", "name": "Rubber duck"},
    {"content_id": FOUNTAIN, "kind": "poi", "name": "Littlefield Fountain"},
]


def _matchers():
    """Every call site's matcher, by whatever name each gives it."""
    from spatialdds_demo import content_catalogue
    return {"spatialdds_demo/content_catalogue.py":
            content_catalogue.matches_catalog_filter}


def _query(**filters):
    return {"has_filter": True, "filter": filters}


class CatalogFilter(unittest.TestCase):
    def setUp(self):
        self.matchers = _matchers()

    def _each(self, entry, query):
        """Every implementation's answer, so a disagreement is the failure."""
        answers = {name: fn(entry, query) for name, fn in self.matchers.items()}
        self.assertEqual(len(set(answers.values())), 1,
                         f"implementations disagree: {answers}")
        return next(iter(answers.values()))

    def test_neither_call_site_has_its_own_copy(self):
        """
        The consolidation, guarded.

        A second implementation that agrees on today's cases is not a
        different outcome from a shared one -- until someone edits one of
        them. Identity is the only assertion that catches the re-introduction
        rather than its consequences.
        """
        from spatialdds_demo.catalog_semantics import matches_catalog_filter
        for name, fn in self.matchers.items():
            with self.subTest(call_site=name):
                self.assertIs(fn, matches_catalog_filter,
                              f"{name} has its own matcher again")

    def test_no_filter_matches_everything(self):
        for entry in ENTRIES:
            self.assertTrue(self._each(entry, {"has_filter": False}))

    def test_an_empty_lane_means_match_all_in_that_lane(self):
        for entry in ENTRIES:
            self.assertTrue(self._each(entry, _query(kind_in=[], content_id_in=[])))

    def test_lookup_by_id(self):
        self.assertTrue(self._each(ENTRIES[0], _query(content_id_in=[DUCK])))
        self.assertFalse(self._each(ENTRIES[1], _query(content_id_in=[DUCK])))

    def test_an_id_nobody_has_matches_nothing(self):
        for entry in ENTRIES:
            self.assertFalse(self._each(entry, _query(content_id_in=["no-such-id"])))

    def test_the_lanes_intersect_rather_than_either_winning(self):
        """An id list is not a way around a kind filter, and a kind filter is
        not a way around an id list."""
        duck = ENTRIES[0]
        self.assertTrue(self._each(duck, _query(content_id_in=[DUCK],
                                                kind_in=["model"])))
        self.assertFalse(self._each(duck, _query(content_id_in=[DUCK],
                                                 kind_in=["poi"])))
        self.assertFalse(self._each(duck, _query(content_id_in=[FOUNTAIN],
                                                 kind_in=["model"])))

    def test_several_ids_at_once(self):
        query = _query(content_id_in=[DUCK, FOUNTAIN])
        for entry in ENTRIES:
            self.assertTrue(self._each(entry, query))

    def test_kind_filtering_still_works_on_its_own(self):
        self.assertTrue(self._each(ENTRIES[0], _query(kind_in=["model"])))
        self.assertFalse(self._each(ENTRIES[0], _query(kind_in=["poi"])))


class TheInventoryIsFilteredByTheSameRule(unittest.TestCase):
    """An announce's kind is its `class_id`; the adaptation is the only seam."""

    def setUp(self):
        from spatialdds_validation import create_coverage_bbox_earth_fixed
        # Real coverage at the fountain, so "ignores the area" is a claim
        # about an area that would otherwise exclude it.
        _, austin = create_coverage_bbox_earth_fixed(-97.7397, 30.2838, -97.7396, 30.2839)
        self.items = [
            {"content_id": DUCK, "class_id": "model", "coverage": [austin]},
            {"content_id": FOUNTAIN, "class_id": "poi", "coverage": [austin]},
        ]

    def _ids(self, **kw):
        from spatialdds_demo.content_catalogue import filter_inventory
        return [i["content_id"] for i in filter_inventory(self.items, **kw)]

    def test_an_area_elsewhere_excludes_it(self):
        self.assertEqual(self._ids(geopose={"lat_deg": -33.86, "lon_deg": 151.21}), [])

    def test_the_area_it_is_in_includes_it(self):
        self.assertEqual(len(self._ids(geopose={"lat_deg": 30.28385, "lon_deg": -97.73965})), 2)

    def test_kind_filters_on_class_id(self):
        self.assertEqual(self._ids(kind_in=["poi"]), [FOUNTAIN])

    def test_lookup_by_id_ignores_the_area(self):
        far = {"lat_deg": -33.86, "lon_deg": 151.21}
        self.assertEqual(self._ids(content_id_in=[DUCK], geopose=far), [DUCK])

    def test_no_filter_returns_everything(self):
        self.assertEqual(sorted(self._ids()), sorted([DUCK, FOUNTAIN]))

if __name__ == "__main__":
    unittest.main()
