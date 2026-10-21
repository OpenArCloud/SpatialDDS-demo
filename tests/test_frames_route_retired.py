#!/usr/bin/env python3
"""
`GET /v1/frames` is retired; what it served is still served, asserted.

The bridge route was a view over `Announce.transforms`: the union of every
cached service announce's transforms, keyed by the local frame. The ruling
retired it once two things were shown to serve the same transforms:

* the binding's frames lane, `/{scene}/frames/frame_transform/v1` -- the
  scene's own claim about its frame, which the page already preferred; and
* the `ContentAnnounce` of every placed item, which since the catalogue's 1.8
  migration carries its frame's anchor itself.

The oracle is `tests/fixtures/frames-route-baseline.json`: the route's answer,
captured live with the full stack up before it was removed, beside the lane
and the announces read in the same minute. Frozen, as the catalogue's 34-row
capture is.

Field mapping, because the direction is the part to get right: a
`disco::Transform` names the *local* frame `from` and earth-fixed `to`; a
`core::FrameTransform` names them `child_ref` and `parent_ref`, with
`T_parent_child` carrying the same numbers. So `from` is `child_ref`. Mapping
by name instead inverts the transform with every number still plausible.

**One measured difference, not a disagreement.** The scene's `child_ref`
declares its scale (`SCALE_DECLARED`, 1 m per unit); the catalogue's `from`
says `SCALE_UNKNOWN`. Same frame identity (UUIDv5 of the fqn), same pose.
Nothing that consumed the route read the scale members, so the comparison
below is of identity and pose, and the scale members are pinned separately so
a change to either claim is seen rather than absorbed.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo.json_mapping import to_json                   # noqa: E402
from spatialdds_demo.model_service import venue_georeference       # noqa: E402

BASELINE = REPO / "tests" / "fixtures" / "frames-route-baseline.json"
ANCHOR_FILES = sorted((REPO / "bridges").rglob("frame_anchors*.json"))
BINDING = os.getenv("SPATIALDDS_BINDING_URL", "http://localhost:8089")
BRIDGE = os.getenv("SPATIALDDS_BRIDGE_URL", "http://localhost:8088")
SCENE = "ut-austin-littlefield"
EARTH_FIXED = "earth-fixed"


def _route() -> dict:
    """fqn -> the `disco::Transform` the route served for it."""
    return json.loads(BASELINE.read_text())["route"]["frames"]


def _identity(ref: dict) -> tuple:
    return (ref.get("fqn"), ref.get("uuid"))


def lane_disagreements(route: dict, lane: list) -> list:
    """What the route served that the frames lane does not serve the same."""
    by_child = {(ft.get("child_ref") or {}).get("fqn"): ft for ft in lane}
    problems = []
    for fqn, served in route.items():
        ft = by_child.get(fqn)
        if ft is None:
            problems.append(f"{fqn}: the lane does not serve it")
            continue
        if _identity(ft["child_ref"]) != _identity(served["from"]):
            problems.append(f"{fqn}: child_ref is not the route's `from`")
        if _identity(ft["parent_ref"]) != _identity(served["to"]):
            problems.append(f"{fqn}: parent_ref is not the route's `to`")
        pose = ft["T_parent_child"]
        if (list(pose["t"]), list(pose["q"])) != (served["pose"]["t"],
                                                  served["pose"]["q"]):
            problems.append(f"{fqn}: T_parent_child is not the route's pose")
    return problems


def anchor_disagreements(route: dict, announces: list) -> list:
    """What the route served that no content announce anchors the same."""
    anchors = {}
    for announce in announces:
        for t in announce.get("transforms") or []:
            if (t.get("to") or {}).get("fqn") == EARTH_FIXED:
                anchors.setdefault(t["from"]["fqn"], []).append(t)
    problems = []
    for fqn, served in route.items():
        if not anchors.get(fqn):
            problems.append(f"{fqn}: no announce carries its anchor")
        for t in anchors.get(fqn, []):
            if (_identity(t["from"]), _identity(t["to"]), t["pose"]) != (
                    _identity(served["from"]), _identity(served["to"]),
                    served["pose"]):
                problems.append(f"{fqn}: an announce anchors it differently")
    return problems


class TheCaptureCoversWhatTheRouteCouldServe(unittest.TestCase):
    """A capture of one stack is only the oracle if that stack was the domain."""

    def test_every_anchored_frame_is_in_the_capture(self):
        # The route's only source is the catalogue's announce, whose
        # transforms come from SPATIALDDS_FRAME_ANCHORS. Every anchors file
        # the repository ships is therefore the whole of what it could serve.
        self.assertTrue(ANCHOR_FILES, "no frame_anchors*.json found to check")
        shipped = set()
        for path in ANCHOR_FILES:
            shipped |= set(json.loads(path.read_text()))
        self.assertEqual(shipped, set(_route()))


class TheReplacementsServeTheSameTransforms(unittest.TestCase):
    """Against the code that publishes them; runs anywhere."""

    def test_the_scene_publishes_what_the_route_served(self):
        lane = [to_json(venue_georeference())]
        self.assertEqual(lane_disagreements(_route(), lane), [])

    def test_the_content_announce_carries_what_the_route_served(self):
        # Every seed's announces as the catalogue server builds them, anchors
        # included -- the documents the catalogue chain's own test checks.
        from tests.test_catalogue_chain import chain_documents
        announces = [doc["announce"] for docs in chain_documents().values()
                     for doc in docs]
        self.assertEqual(anchor_disagreements(_route(), announces), [])

    def test_the_captured_replacements_agreed_when_captured(self):
        doc = json.loads(BASELINE.read_text())
        self.assertEqual(lane_disagreements(_route(), doc["binding_lane"]), [])
        self.assertEqual(
            anchor_disagreements(_route(), doc["anchoring_announces"]), [])

    def test_the_scale_members_are_the_one_difference(self):
        doc = json.loads(BASELINE.read_text())
        served = _route()["map/ut-littlefield-fountain"]["from"]
        scene = doc["binding_lane"][0]["child_ref"]
        self.assertEqual((served["has_scale"], served["scale_status"]),
                         (False, "SCALE_UNKNOWN"))
        self.assertEqual((scene["has_scale"], scene["scale_status"]),
                         (True, "SCALE_DECLARED"))


class TheComparisonCanFail(unittest.TestCase):
    """Each fault a replacement could carry is one the comparison reports."""

    def setUp(self):
        doc = json.loads(BASELINE.read_text())
        self.route = _route()
        self.lane = doc["binding_lane"]
        self.announces = doc["anchoring_announces"]

    def test_an_inverted_lane_is_caught(self):
        ft = json.loads(json.dumps(self.lane[0]))
        ft["child_ref"], ft["parent_ref"] = ft["parent_ref"], ft["child_ref"]
        self.assertTrue(lane_disagreements(self.route, [ft]))

    def test_a_moved_lane_is_caught(self):
        ft = json.loads(json.dumps(self.lane[0]))
        ft["T_parent_child"]["t"][2] += 1e-6
        self.assertTrue(lane_disagreements(self.route, [ft]))

    def test_a_missing_lane_is_caught(self):
        self.assertTrue(lane_disagreements(self.route, []))

    def test_a_renamed_frame_identity_is_caught(self):
        ft = json.loads(json.dumps(self.lane[0]))
        ft["child_ref"]["uuid"] = "00000000-0000-0000-0000-000000000000"
        self.assertTrue(lane_disagreements(self.route, [ft]))

    def test_an_unanchored_inventory_is_caught(self):
        self.assertTrue(anchor_disagreements(self.route, []))

    def test_a_rotated_anchor_is_caught(self):
        announce = json.loads(json.dumps(self.announces[0]))
        for t in announce["transforms"]:
            if t["to"]["fqn"] == EARTH_FIXED:
                t["pose"]["q"] = [0.0, 0.0, 0.0, 1.0]
        self.assertTrue(anchor_disagreements(self.route, [announce]))


def _get(url: str, body=None):
    request = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


class TheRunningStackServesThemAndNotTheRoute(unittest.TestCase):
    """
    The same comparison against a running stack, and the route gone from it.

    Skips with its reason when the stack is down, so it starts running again
    on its own once it is up.
    """

    def setUp(self):
        status, self.lane = _get(f"{BINDING}/{SCENE}/frames/frame_transform/v1")
        if status != 200:
            self.skipTest(f"no binding at {BINDING} — run the stack with "
                          "SPATIALDDS_MODEL_LAYER=1")
        if _get(f"{BRIDGE}/health")[0] != 200:
            self.skipTest(f"no bridge at {BRIDGE}")

    def test_the_lane_serves_what_the_route_served(self):
        self.assertEqual(lane_disagreements(_route(), self.lane), [])

    def test_the_inventory_anchors_what_the_route_served(self):
        doc = json.loads(BASELINE.read_text())
        ids = [a["content_id"] for a in doc["anchoring_announces"]]
        status, body = _get(f"{BRIDGE}/v1/content/query",
                            {"content_id_in": ids, "limit": len(ids)})
        self.assertEqual(status, 200)
        self.assertEqual(
            anchor_disagreements(_route(), body.get("results") or []), [])

    def test_the_route_is_gone(self):
        self.assertEqual(_get(f"{BRIDGE}/v1/frames")[0], 404)


if __name__ == "__main__":
    unittest.main()
