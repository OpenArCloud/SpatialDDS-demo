"""
Every catalogue seed row deserializes into the type it claims to be.

Added after a hand-authored row cost an afternoon. A robot asset was written
with `bytes` and `media_type` and without `meta`; `AssetRef` requires `meta`,
so the catalogue server raised while decoding the query response -- and the
symptom was **every catalogue query returning no results**, including for rows
that were perfectly fine. (That was the 1.7 catalogue protocol; since 1.8 the
same row would fail building its ContentAnnounce at startup.) The bad row was not named anywhere a caller could
see it.

That is the shape of failure worth guarding: not "the row is wrong" but "one
wrong row makes the whole service look empty". A seed is data that has to
satisfy a schema, and nothing checked it until it was loaded by a server whose
only way of complaining was to answer nothing.

Cheap here, and at the fast tier rather than after a container restart.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import content_catalogue as cc  # noqa: E402
from spatialdds.dds import from_json  # noqa: E402
from spatialdds_idl18.spatial.disco import ContentAnnounce  # noqa: E402

SEEDS = sorted(
    p for p in REPO.rglob("catalog_seed*.json")
    if "node_modules" not in p.parts and "cdk.out" not in str(p)
)


class SeedRowsAreValid(unittest.TestCase):
    def test_there_are_seeds_to_check(self):
        self.assertTrue(SEEDS, "no catalogue seeds found — did they move?")

    def test_every_row_becomes_valid_1_8_documents(self):
        """
        Through the catalogue's own loader and builders, not stricter ones of
        our own: seeds deliberately omit presence-flagged fields that
        `load_seed` completes. Since 1.8 a bad row's blast radius is the
        catalogue's startup -- its announce is built for the inventory before
        anything is served -- and the manifest's assets must be AssetRefs
        with the members 1.8 requires.
        """
        for seed in SEEDS:
            for index, row in enumerate(cc.load_seed(str(seed))):
                with self.subTest(seed=seed.relative_to(REPO), row=index,
                                  content_id=row.get("content_id")):
                    try:
                        from_json(ContentAnnounce, cc.content_announce(
                            row, provider_id="svc:content:test"))
                    except Exception as error:
                        self.fail(
                            f"{seed.relative_to(REPO)} row {index} "
                            f"({row.get('name')}) does not announce: {error}")
                    for asset in cc.content_manifest(row)["assets"]:
                        for key in ("uri", "media_type", "hash"):
                            self.assertTrue(asset.get(key), f"AssetRef.{key}")

    def test_rows_within_a_seed_agree_on_their_shape(self):
        """
        A row copied from a neighbour and edited by hand is the normal way
        these are written, so the neighbours are the specification. Divergent
        keys are how the bad asset got in: it had `bytes`, which nothing else
        had, and lacked `meta`, which everything else had.
        """
        for seed in SEEDS:
            rows = json.loads(seed.read_text())
            if len(rows) < 2:
                continue
            reference = set(rows[0])
            for index, row in enumerate(rows[1:], start=1):
                with self.subTest(seed=seed.relative_to(REPO), row=index):
                    self.assertEqual(
                        set(row), reference,
                        f"row {index} has a different key set from row 0")
            ref_asset = set(rows[0].get("asset") or {})
            for index, row in enumerate(rows[1:], start=1):
                if not row.get("asset"):
                    continue
                with self.subTest(seed=seed.relative_to(REPO), row=index,
                                  part="asset"):
                    self.assertEqual(set(row["asset"]), ref_asset)


if __name__ == "__main__":
    unittest.main()
