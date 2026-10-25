#!/usr/bin/env python3
"""
The venue's own georeference, and the one check that catches an inverted frame.

`model_service` publishes where `map/ut-littlefield-fountain` sits on the earth
--- the scene's claim about its own frame. The catalogue anchors the same
frame in the content announces it places there, which is a *different* claim
about where its map sits, and keeps doing so. Neither is crowned; see
`docs/venue-georeference-census.md`.

**Why a same-answer check and not just a numbers check.** The dangerous way
this goes wrong is silent. `core::FrameTransform` names the global frame
`parent_ref` and the local one `child_ref`; the announce's anchor says `from` = the
*local* frame and `to` = earth-fixed. Map those by name instead of by role and
the transform inverts --- and an inverted transform still produces a
well-formed pose with plausible-looking numbers, placing the venue near the
centre of the earth. No exception, no malformed record, nothing a schema check
would catch. What catches it is composing the whole thing: take one entity's
local pose through each route and see whether the duck lands on the same spot
of the globe.

The unit half runs anywhere. The two-route half needs both surfaces up and
skips with its reason when they are not, so it re-arms itself rather than
needing to be remembered.
"""
from __future__ import annotations

import json
import math
import os
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo.model_service import (                     # noqa: E402
    VENUE_FRAME_FQN, earth_fixed_frame, venue_georeference)
from spatialdds_demo.plaza import (                             # noqa: E402
    EARTH_FIXED_FQN, VENUE_ANCHOR_HEIGHT_M, VENUE_ANCHOR_LAT_DEG,
    VENUE_ANCHOR_LON_DEG, enu_to_ecef_pose)

BINDING = os.getenv("SPATIALDDS_BINDING_URL", "http://localhost:8089")
BRIDGE = os.getenv("SPATIALDDS_BRIDGE_URL", "http://localhost:8088")
SCENE = "ut-austin-littlefield"

#: How close the two routes must land. Generous against float noise on
#: numbers of magnitude 1e6, and far tighter than any inversion: getting the
#: direction wrong moves the answer by thousands of kilometres, not
#: centimetres.
TOLERANCE_M = 0.01


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=6) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _rotate(q, v):
    """The same quaternion rotation the browser's `resolveInFrame` applies."""
    a, b, c, d = q
    t = [2 * (b * v[2] - c * v[1]),
         2 * (c * v[0] - a * v[2]),
         2 * (a * v[1] - b * v[0])]
    return [v[0] + d * t[0] + (b * t[2] - c * t[1]),
            v[1] + d * t[1] + (c * t[0] - a * t[2]),
            v[2] + d * t[2] + (a * t[1] - b * t[0])]


def _to_geodetic(x, y, z):
    a, f = 6378137.0, 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p)
    for _ in range(8):
        n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - e2 * n / (n + h)))
    n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    return math.degrees(lat), math.degrees(lon), p / math.cos(lat) - n


def _place(frame_pose, local):
    """An entity's local pose as geodetic, through one frame transform."""
    e = _rotate(frame_pose["q"], local)
    return _to_geodetic(e[0] + frame_pose["t"][0],
                        e[1] + frame_pose["t"][1],
                        e[2] + frame_pose["t"][2])


def _metres_apart(a, b):
    mlat = 111320.0
    dn = (a[0] - b[0]) * mlat
    de = (a[1] - b[1]) * mlat * math.cos(math.radians(a[0]))
    return math.sqrt(dn * dn + de * de + (a[2] - b[2]) ** 2)


class TheVenueDeclaresWhereItIs(unittest.TestCase):
    def test_the_transform_runs_earth_fixed_to_the_venue(self):
        """
        The direction, asserted at the record.

        `parent_ref` is the global frame and `child_ref` the local one. This is
        the assertion that fails if someone follows the announce anchor's `from`
        and `to` names instead of their roles.
        """
        ft = venue_georeference()
        self.assertEqual(ft.parent_ref.fqn, EARTH_FIXED_FQN,
                         "parent_ref is the global frame")
        self.assertEqual(ft.child_ref.fqn, VENUE_FRAME_FQN,
                         "child_ref is the venue's local frame")

    def test_the_translation_is_the_venue_origin_not_the_earth_centre(self):
        """An inverted transform's giveaway: a translation near zero."""
        ft = venue_georeference()
        magnitude = math.hypot(*ft.T_parent_child.t)
        self.assertGreater(magnitude, 6.0e6,
                           "the venue origin is one earth radius from the "
                           "earth's centre; a near-zero translation means the "
                           "transform was inverted")

    def test_the_child_frame_carries_the_scale_declaration(self):
        """The one thing the scene's voice says that the catalogue's does not.

        1.8's `FrameRef` has room for scale; the catalogue's anchor leaves it
        `SCALE_UNKNOWN`. Measured when `/v1/frames` was retired --- see
        tests/test_frames_route_retired.py.
        """
        child = venue_georeference().child_ref
        self.assertTrue(child.has_scale)
        self.assertEqual(child.scale_status.name, "SCALE_DECLARED")
        self.assertEqual(child.meters_per_unit, 1.0)

    def test_the_global_frame_claims_no_axis_convention(self):
        """ECEF is not a convention `CoordConvention` can name."""
        self.assertFalse(earth_fixed_frame().has_coord_convention)

    def test_the_covariance_arm_is_chosen_explicitly(self):
        """A union has to say which arm it is; there is no implicit absent."""
        self.assertEqual(venue_georeference().cov.discriminator.name, "COV_NONE")

    def test_the_record_survives_the_wire(self):
        from spatialdds.dds.types.spatial.core import FrameTransform
        ft = venue_georeference()
        back = FrameTransform.deserialize(ft.serialize())
        self.assertEqual(back.child_ref.fqn, VENUE_FRAME_FQN)
        self.assertEqual(back.T_parent_child.t, ft.T_parent_child.t)

    def test_the_projection_is_shared_with_the_catalogue(self):
        """One implementation, so a difference can only come from an anchor.

        Two copies of this arithmetic would make a drift look like a
        disagreement, and the census ruled that disagreements are meaningful.
        """
        import ar_demo.spatialdds_catalog_server as catalog
        anchor = {"latitude": VENUE_ANCHOR_LAT_DEG,
                  "longitude": VENUE_ANCHOR_LON_DEG,
                  "height": VENUE_ANCHOR_HEIGHT_M}
        announced = catalog._enu_to_ecef_transform(VENUE_FRAME_FQN, anchor)
        mine_t, mine_q = enu_to_ecef_pose(VENUE_ANCHOR_LAT_DEG,
                                          VENUE_ANCHOR_LON_DEG,
                                          VENUE_ANCHOR_HEIGHT_M)
        self.assertEqual(announced["pose"]["t"], mine_t)
        self.assertEqual(announced["pose"]["q"], mine_q)


def _post(url: str, body: dict):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


class BothRoutesLandTheDuckOnTheSameSpot(unittest.TestCase):
    """
    The composed check, against a running stack.

    The two routes are the scene's frames lane and the anchor the catalogue's
    content announces carry. The second was the bridge's `/v1/frames` until
    2026-10-21, when that route was retired for serving the same transform.

    Skips with its reason when either surface is absent, so it starts running
    again on its own once both are up rather than waiting to be re-enabled.
    """

    def setUp(self):
        self.entities = _get(f"{BINDING}/{SCENE}/world/entity/v1")
        self.scene_frames = _get(
            f"{BINDING}/{SCENE}/frames/frame_transform/v1")
        if self.entities is None or self.scene_frames is None:
            self.skipTest(f"no binding at {BINDING} — run the stack with "
                          "SPATIALDDS_MODEL_LAYER=1")
        self.inventory = _post(f"{BRIDGE}/v1/content/query", {"geopose": {
            "lat_deg": VENUE_ANCHOR_LAT_DEG, "lon_deg": VENUE_ANCHOR_LON_DEG,
            "alt_m": VENUE_ANCHOR_HEIGHT_M}})
        if self.inventory is None:
            self.skipTest(f"no bridge at {BRIDGE}; this check needs both "
                          "routes alive to compare them")

    def test_both_frame_routes_place_one_entity_identically(self):
        placed = [e for e in self.entities if e.get("has_pose")]
        self.assertTrue(placed, "the venue must have a placed entity to check")
        subject = placed[0]
        fqn = (subject.get("frame_ref") or {}).get("fqn")
        local = subject["pose"]["t"]

        scene = next((ft for ft in self.scene_frames
                      if ft["child_ref"]["fqn"] == fqn), None)
        self.assertIsNotNone(
            scene, f"the scene must publish a transform for {fqn}")
        announced = next(
            (t for item in self.inventory.get("results") or []
             for t in item.get("transforms") or []
             if (t.get("from") or {}).get("fqn") == fqn
             and (t.get("to") or {}).get("fqn") == EARTH_FIXED_FQN), None)
        self.assertIsNotNone(
            announced, f"the catalogue must still anchor {fqn} in the content "
                       "it places there")

        via_scene = _place(
            {"t": scene["T_parent_child"]["t"], "q": scene["T_parent_child"]["q"]},
            local)
        via_announce = _place(announced["pose"], local)
        apart = _metres_apart(via_scene, via_announce)
        print(f"\n  {subject['entity_id']} via the scene's lane : "
              f"{via_scene[0]:.9f}, {via_scene[1]:.9f}, {via_scene[2]:.3f}")
        print(f"  {subject['entity_id']} via a content anchor : "
              f"{via_announce[0]:.9f}, {via_announce[1]:.9f}, "
              f"{via_announce[2]:.3f}")
        print(f"  apart: {apart:.6f} m (tolerance {TOLERANCE_M})")
        self.assertLess(
            apart, TOLERANCE_M,
            "the two routes must land the same entity on the same spot; a "
            "large divergence means one transform's direction is inverted")

    def test_the_scene_lane_is_public_so_a_browser_can_read_it(self):
        """No credential, or a cross-origin page cannot read it at all.

        An `Authorization` header forces a CORS preflight the gateway does not
        answer, so a gated georeference would be unreachable from the browser
        that needs it. See docs/browser-credential-finding.md, blocker B.
        """
        request = urllib.request.Request(
            f"{BINDING}/{SCENE}/frames/frame_transform/v1",
            headers={"Origin": "http://localhost:5173"})
        with urllib.request.urlopen(request, timeout=6) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(
                response.headers.get("Access-Control-Allow-Origin"), "*",
                "the frames lane must be readable cross-origin without a "
                "credential")


if __name__ == "__main__":
    unittest.main()
