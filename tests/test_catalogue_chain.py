#!/usr/bin/env python3
"""
Everything the 1.7 catalogue returned is reachable through the 1.8 chain.

The catalogue moved off the demo's own request/reply protocol
(``oarc_demo::CatalogQuery`` -> ``CatalogResponse``) onto the spec's:
a latched ``ContentAnnounce`` per item, whose ``manifest_uri`` names a
``spatial.manifest/1.8`` content manifest behind the resolver. The ruling's
acceptance for that move was "field by field, everything /v1/catalog/query
returned is reachable through the new chain, asserted, not assumed".

So the oracle is not this repository's reading of what the old protocol did:
it is ``tests/fixtures/catalogue-1.7-baseline.json``, captured by running the
pre-migration code (commit ``a18bd99``) over all three seeds before any of it
changed, and frozen. Each row is rebuilt here from the announce and the
manifest alone and compared with the captured row, every key of it. 1.8's
appended members may appear as extras -- that is appendable evolution -- but
nothing the page once received may be missing or different.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import content_catalogue as cc               # noqa: E402
from spatialdds.dds import from_json, to_json        # noqa: E402
from spatialdds_idl18.spatial.disco import ContentAnnounce         # noqa: E402
from spatialdds_validation import SpatialDDSValidator              # noqa: E402

BASELINE = REPO / "tests" / "fixtures" / "catalogue-1.7-baseline.json"
PROVIDER = "svc:content:demo/catalog"
# The page's half of the chain is TypeScript and is pinned to the same
# capture by web/tests/content-chain.spec.ts, over the documents below.
WEB_FIXTURE = REPO / "web" / "tests" / "fixtures" / "content_chain.json"
ANCHORS = REPO / "bridges" / "web_bridge" / "tests" / "frame_anchors_fountain.json"
# Publication times -- the announce's and its frame anchor's -- are the only
# fields that are not a function of the row; fixed here so the fixture is
# reproducible.
FIXED_STAMP = {"sec": 1_700_000_000, "nanosec": 0}


def on_the_wire(announce: dict) -> dict:
    """The announce as a reader receives it: through the 1.8 type and back."""
    return to_json(from_json(ContentAnnounce, announce))


def chain_documents() -> dict:
    """
    Every row's announce and manifest, as the bus and the resolver serve them.

    Frame anchors come from the catalogue server's own derivation, so a placed
    row's announce carries exactly what the running service would put there.
    """
    sys.path.insert(0, str(REPO / "ar_demo"))
    import spatialdds_catalog_server as server
    out = {}
    os.environ["SPATIALDDS_FRAME_ANCHORS"] = str(ANCHORS)
    try:
        for seed in json.loads(BASELINE.read_text())["seeds"]:
            rows = cc.load_seed(str(REPO / seed))
            anchors = {t["from"]["fqn"]: {**t, "stamp": dict(FIXED_STAMP)}
                       for t in server._frame_transforms(rows)}
            out[seed] = [{
                "announce": on_the_wire(cc.content_announce(
                    row, provider_id=PROVIDER, stamp=FIXED_STAMP,
                    frame_anchor=anchors.get((row.get("frame_ref") or {}).get("fqn", ""))
                    if row.get("has_pose") else None)),
                "manifest": cc.content_manifest(row),
            } for row in rows]
    finally:
        os.environ.pop("SPATIALDDS_FRAME_ANCHORS", None)
    return out


def reconstruct(announce: dict, manifest: dict) -> dict:
    """
    A CatalogEntry rebuilt from the two 1.8 documents and nothing else -- the
    question the page and any other consumer now answers the same way.
    """
    content_frame = None
    placement = None
    for t in announce.get("transforms") or []:
        if t["from"]["fqn"].endswith(f"/content/{cc.content_rid(announce['content_id'])}"):
            placement = t
            content_frame = t["from"]["fqn"]
    ext = manifest.get(cc.EXTENSION_KEY) or {}
    assets = manifest.get("assets") or []
    return {
        "content_id": announce["content_id"],
        "kind": announce["class_id"],
        "name": announce["title"],
        "coverage": announce["coverage"],
        "frame_ref": placement["to"] if placement else ext.get("frame_ref"),
        "formats": [a["media_type"] for a in assets] if assets else ext.get("formats", []),
        # The content's update time is the manifest's stamp; the announce's
        # is its own publication time, which pairs with its TTL.
        "updated_sec": int(manifest["stamp"]["sec"]),
        "href": assets[0]["uri"] if assets else ext.get("href", ""),
        "has_pose": placement is not None,
        "pose": placement["pose"] if placement else None,
        "has_asset": bool(assets),
        "asset": assets[0] if assets else None,
        "_content_frame": content_frame,
    }


def contained(old, new, path="") -> list:
    """Every key and value of `old` present and equal in `new`; extras allowed."""
    if isinstance(old, dict):
        if not isinstance(new, dict):
            return [f"{path}: expected an object, got {type(new).__name__}"]
        out = []
        for k, v in old.items():
            if k not in new:
                out.append(f"{path}.{k}: missing")
            else:
                out += contained(v, new[k], f"{path}.{k}")
        return out
    if isinstance(old, list):
        if not isinstance(new, list) or len(old) != len(new):
            return [f"{path}: {old!r} != {new!r}"]
        out = []
        for i, (a, b) in enumerate(zip(old, new)):
            out += contained(a, b, f"{path}[{i}]")
        return out
    # N.1 writes an int64 as a decimal string, and the oracle was captured
    # in the demo's old dialect, which wrote an integer. The same value, so
    # equal, and only that: an int against the string of itself.
    if isinstance(old, int) and not isinstance(old, bool) \
            and isinstance(new, str) and new == str(old):
        return []
    return [] if old == new else [f"{path}: {old!r} != {new!r}"]


class TheChainCarriesEveryField(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("SPATIALDDS_ASSET_BASE", None)   # as the baseline ran
        cls.baseline = json.loads(BASELINE.read_text())["seeds"]

    def _rows(self):
        for seed, captured in self.baseline.items():
            for row, old in zip(cc.load_seed(str(REPO / seed)), captured):
                yield seed, row, old

    def test_the_baseline_covers_every_seed(self):
        """The oracle is the capture, so it has to be the whole of it."""
        self.assertEqual(sum(len(v) for v in self.baseline.values()), 34)

    def test_every_field_of_every_row_is_reachable(self):
        problems = []
        for seed, row, old in self._rows():
            announce = on_the_wire(cc.content_announce(row, provider_id=PROVIDER))
            manifest = cc.content_manifest(row)
            new = reconstruct(announce, manifest)
            # The old row had has_pose/pose and has_asset/asset as presence
            # pairs whose payload was meaningless when the flag was false; a
            # 1.8 consumer gets "absent" instead of a zero value it must
            # ignore, which is the presence rule (§2.x) stated structurally.
            expected = {k: v for k, v in old.items()}
            if not old["has_pose"]:
                expected.pop("pose")
            if not old["has_asset"]:
                expected.pop("asset")
            for p in contained(expected, new, ""):
                problems.append(f"{seed} {old['content_id']}{p}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems[:40]))

    def test_and_nothing_present_in_the_old_row_was_dropped_silently(self):
        """
        The two presence pairs above are the only keys not compared value for
        value, and only when their flag was false. Every other key of every
        captured row is in the comparison -- asserted, so a key the old
        protocol returned cannot be left out of `reconstruct` unnoticed.
        """
        compared = {"content_id", "kind", "name", "coverage", "frame_ref",
                    "formats", "updated_sec", "href", "has_pose", "pose",
                    "has_asset", "asset"}
        for seed, captured in self.baseline.items():
            for old in captured:
                self.assertEqual(set(old) - compared, set(),
                                 f"{seed}: a 1.7 field with no 1.8 home")


class ThePageReadsTheSameDocuments(unittest.TestCase):
    def test_the_web_fixture_is_what_the_builders_produce(self):
        """
        Drift gate for the TypeScript half's input. Regenerate with
        ``python3 tests/test_catalogue_chain.py --write-web-fixture``.
        """
        self.assertEqual(json.loads(WEB_FIXTURE.read_text()), chain_documents(),
                         "web/tests/fixtures/content_chain.json is stale")

    def test_some_announce_in_it_anchors_a_frame(self):
        """Else the page's frames-from-announces path is never exercised."""
        docs = chain_documents()
        anchored = [d for rows in docs.values() for d in rows
                    if any(t["to"]["fqn"] == "earth-fixed"
                           for t in d["announce"]["transforms"])]
        self.assertTrue(anchored)


class TheProseCountsComeFromTheSeeds(unittest.TestCase):
    """SPEC_COMPLIANCE's gap table states counts; they are derived, here."""

    def test_the_gap_counts(self):
        text = (REPO / "ar_demo" / "SPEC_COMPLIANCE.md").read_text()
        m = re.search(r"<!-- catalogue-gaps: (.*?) -->", text)
        self.assertIsNotNone(m, "the catalogue-gaps marker is gone")
        stated = dict(kv.split("=") for kv in m.group(1).split())
        counted = {"rows": 0, "unhashed": 0, "formats": 0, "frame_ref": 0, "placed": 0}
        for seed in json.loads(BASELINE.read_text())["seeds"]:
            for row in cc.load_seed(str(REPO / seed)):
                m = cc.content_manifest(row)
                ext = m.get(cc.EXTENSION_KEY) or {}
                counted["rows"] += 1
                counted["unhashed"] += not m["assets"]
                counted["formats"] += "formats" in ext
                counted["frame_ref"] += "frame_ref" in ext
                counted["placed"] += "transforms" in m
        self.assertEqual({k: int(v) for k, v in stated.items()}, counted)


class TheInventoryDropsWhatHasLapsed(unittest.TestCase):
    """§3.3: discard an announce once ``now - stamp > 2 * ttl_sec``."""

    def test_staleness_follows_the_announce_stamp(self):
        from spatialdds_demo.discovery_bus import _stale
        row = cc.load_seed(str(REPO / "bridges/web_bridge/tests/catalog_seed_fountain.json"))[0]
        a = on_the_wire(cc.content_announce(row, provider_id=PROVIDER, ttl_sec=300,
                                            stamp={"sec": 1000, "nanosec": 0}))
        self.assertFalse(_stale(a, 1000 + 600))
        self.assertTrue(_stale(a, 1000 + 601))


class TheDocumentsAreConformant(unittest.TestCase):
    """The chain is only the spec's if both documents are valid 1.8."""

    def test_every_announce_builds_into_the_1_8_type(self):
        for seed in json.loads(BASELINE.read_text())["seeds"]:
            for row in cc.load_seed(str(REPO / seed)):
                with self.subTest(seed=seed, content_id=row["content_id"]):
                    typed = from_json(ContentAnnounce, cc.content_announce(
                        row, provider_id=PROVIDER))
                    self.assertEqual(to_json(typed)["content_id"], row["content_id"])

    def test_every_manifest_meets_section_8(self):
        for seed in json.loads(BASELINE.read_text())["seeds"]:
            for row in cc.load_seed(str(REPO / seed)):
                m = cc.content_manifest(row)
                with self.subTest(seed=seed, content_id=row["content_id"]):
                    SpatialDDSValidator.validate_spatial_uri(m["id"])
                    self.assertEqual(SpatialDDSValidator.validate_spatial_uri(
                        m["id"])["rtype"], "content")
                    self.assertEqual(m["profile"], "spatial.manifest/1.8")
                    self.assertEqual(m["rtype"], "content")
                    self.assertEqual(m["content"]["content_id"], row["content_id"])
                    # N.1: an int64 in JSON is a decimal string.
                    self.assertIsInstance(m["stamp"]["sec"], str)
                    for a in m["assets"]:
                        for k in ("uri", "media_type", "hash"):
                            self.assertTrue(a.get(k), f"AssetRef.{k} is required")
                        self.assertRegex(a["hash"], r"^[a-z0-9-]+:[0-9a-f]+$")

    def test_the_announce_names_the_manifest(self):
        for seed in json.loads(BASELINE.read_text())["seeds"]:
            for row in cc.load_seed(str(REPO / seed)):
                a = cc.content_announce(row, provider_id=PROVIDER)
                self.assertEqual(a["manifest_uri"], cc.content_manifest(row)["id"])

    def test_the_announce_stamp_is_its_publication_time(self):
        """
        Not the content's update time. ``stamp`` pairs with ``ttl_sec``, so an
        announce stamped with a January edit and a 300 s TTL declares itself
        expired in January -- which the first cut of the builder did.
        """
        import time
        row = cc.load_seed(str(REPO / "bridges/web_bridge/tests/catalog_seed_fountain.json"))[0]
        a = cc.content_announce(row, provider_id=PROVIDER)
        self.assertLess(abs(a["stamp"]["sec"] - time.time()), 5)
        self.assertNotEqual(a["stamp"]["sec"], row["updated_sec"])
        m = cc.content_manifest(row)
        self.assertEqual(m["stamp"]["sec"], str(row["updated_sec"]))
        self.assertNotIn("ttl_sec", m, "a manifest TTL would count from the edit time")

    def test_ids_that_are_not_rids_map_deterministically(self):
        """`mesh/sf-downtown/item-000` is an id but not an Appendix F rid."""
        rid = cc.content_rid("mesh/sf-downtown/item-000")
        self.assertRegex(rid, r"^[A-Za-z0-9_-]+$")
        self.assertEqual(rid, cc.content_rid("mesh/sf-downtown/item-000"))
        self.assertNotEqual(rid, cc.content_rid("mesh_sf-downtown_item-000"))
        self.assertEqual(cc.content_rid("89f2d953-076d-5c7d-9b74-1193f71685a6"),
                         "89f2d953-076d-5c7d-9b74-1193f71685a6")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-web-fixture"]:
        WEB_FIXTURE.write_text(json.dumps(chain_documents(), indent=1) + "\n")
        print(f"wrote {WEB_FIXTURE.relative_to(REPO)}")
    else:
        unittest.main()
