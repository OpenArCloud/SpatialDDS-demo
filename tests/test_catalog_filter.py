"""
The demo-local catalogue filter, in both places it is implemented.

There are two copies of this matcher -- the AR demo's catalogue server and the
conformance harness -- and they are supposed to agree. They are tested through
one parameterised suite for that reason: a filter that means different things
on two servers is worse than a filter that is wrong on both, because only one
of those gets noticed.

`content_id_in` is lookup-by-id, added in Part 2. Before it, the catalogue
could answer "what is near here" and not "what is this id", so a
`catalog:<content_id>` reference was resolvable only by a client that had
already queried the right area -- reference-by-id existed and lookup-by-id did
not. See SPEC_COMPLIANCE.
"""

import importlib.util
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


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _matchers():
    """Both implementations, by whatever name each gives it."""
    try:
        server = _load("ar_demo/spatialdds_catalog_server.py", "catalog_server")
        harness = _load("spatialdds_test.py", "spatialdds_test")
    except Exception as exc:  # pragma: no cover - import-time environment
        raise unittest.SkipTest(f"could not load a matcher: {exc}")
    return {
        "ar_demo/spatialdds_catalog_server.py": server._matches_filter,
        "spatialdds_test.py": harness.matches_catalog_filter,
    }


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


if __name__ == "__main__":
    unittest.main()
