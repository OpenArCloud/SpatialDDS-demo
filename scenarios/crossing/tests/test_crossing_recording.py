#!/usr/bin/env python3
"""The committed crossing session: its files, their checksums, and its beats.

`scenarios/crossing/recording/` holds one real-time session of the scene, as
the exhibit precedent holds its fixture (`tests/cross_era/MANIFEST.sha256`):

* `crossing.mcap`, the recording; `crossing.provenance.json` beside it;
* `crossing.truth.jsonl.gz`, the world's ground truth for the same session;
* `MANIFEST.sha256`, the digest of each, whose own digest is pinned here,
  outside the directory, so no file can change without this failing.

The beats the census quotes (docs/crossing-census.md §7) are read back from
these files, not retyped: if a re-recording moves one, this says which.
Needs no SUMO; it reads files.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HERE = REPO / "scenarios" / "crossing" / "recording"
MANIFEST_SHA256 = "d80954b5bb20b9eb5df54c8654c86044650211150259c517943349d36058cb57"

#: The two beats, as docs/crossing-census.md quotes them, in session seconds.
BEATS = {
    "bus-3 stopped enter": 22.0,
    "ped-17 held at the kerb": 27.4,
    "ped-17 released": 34.0,
    "ped-17 crosswalk enter": 34.4,
    "car-a1 crosswalk enter": 41.0,
    "ped-17 crosswalk leave": 45.4,
    "gate warning": 46.6,
    "gate closed": 49.6,
    "bus-3 stopped leave": 52.0,
    "train-7 crossing-zone enter": 63.8,
    "train-7 crossing-zone leave": 70.5,
    "queue at the gate reaches 9": 72.4,
    "gate opening": 72.5,
    "gate open": 75.5,
    "car-a1 crossing-zone enter": 76.3,
    "car-b2 crossing-zone enter": 76.3,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TheCommittedSession(unittest.TestCase):

    def test_the_manifest_is_the_pinned_one(self):
        self.assertEqual(sha256(HERE / "MANIFEST.sha256"), MANIFEST_SHA256)

    def test_every_file_matches_the_manifest(self):
        rows = [line.split() for line in
                (HERE / "MANIFEST.sha256").read_text().splitlines() if line]
        self.assertEqual(sorted(name for _d, name in rows),
                         ["crossing.mcap", "crossing.provenance.json",
                          "crossing.truth.jsonl.gz"])
        for digest, name in rows:
            with self.subTest(file=name):
                self.assertEqual(sha256(HERE / name), digest)

    def test_the_provenance_names_this_recording(self):
        prov = json.loads((HERE / "crossing.provenance.json").read_text())
        self.assertEqual(prov["sha256"], sha256(HERE / "crossing.mcap"))
        self.assertEqual(prov["scene"], "crossing")
        self.assertIn("rate 1", prov["driven_by"])

    def test_the_beats_are_the_census_beats(self):
        from scenarios.crossing import events
        got = events.extract(HERE / "crossing.truth.jsonl.gz")
        self.assertEqual({k: got.get(k) for k in BEATS}, BEATS)


if __name__ == "__main__":
    unittest.main()
