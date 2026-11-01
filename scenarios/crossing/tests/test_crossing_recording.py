#!/usr/bin/env python3
"""The committed crossing sessions: their files, their checksums, their beats
and their teeth.

`scenarios/crossing/recording/` holds two real-time sessions of the scene, as
the exhibit precedent holds its fixture (`tests/cross_era/MANIFEST.sha256`):
`crossing.*`, every participant running, and `crossing-silenced.*`, the same
seed with mmwave-ut-04 SIGKILLed at t = 25 s. For each:

* `.mcap`, the recording; `.provenance.json` beside it;
* `.truth.jsonl.gz`, the world's ground truth for the same session;
* `.watch.jsonl.gz`, the live subscriber's log of the claims as they came;

and `MANIFEST.sha256`, the digest of each, whose own digest is pinned here,
outside the directory, so no file can change without this failing.

The beats docs/crossing-claims.md quotes are read back from the truth logs,
not retyped, and the teeth are measured from the files by the same checks a
fresh pair is held to (`teeth_checks.py`). Needs no SUMO; it reads files.
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

from scenarios.crossing.tests.teeth_checks import TheTeeth   # noqa: E402

HERE = REPO / "scenarios" / "crossing" / "recording"
MANIFEST_SHA256 = "df8dfaa830f577d5b35ec5e9653bb8e3d9094f3249fcb83ed067d609c04ba1d3"
ARMS = ("crossing", "crossing-silenced")
PARTS = ("mcap", "provenance.json", "truth.jsonl.gz", "watch.jsonl.gz")

#: The beats, as docs/crossing-claims.md quotes them, in session seconds.
BEATS = {
    "crossing": {
        "bus-3 stopped enter": 22.0,
        "ped-17 held at the kerb": 27.4,
        "car-a1 command: claim pedestrian in the crosswalk": 27.6,
        "ped-17 released": 34.0,
        "ped-17 crosswalk enter": 34.4,
        "car-b2 command: claim gate-9 closes the crossing zone": 41.9,
        "ped-17 crosswalk leave": 45.4,
        "gate warning": 46.6,
        "car-a1 crosswalk enter": 47.3,
        "gate closed": 49.6,
        "train-7 crossing-zone enter": 63.8,
        "train-7 crossing-zone leave": 70.5,
        "gate opening": 72.5,
        "gate open": 75.5,
        "car-a1 crossing-zone enter": 76.8,
        "car-b2 crossing-zone enter": 76.8,
    },
    "crossing-silenced": {
        "ped-17 crosswalk enter": 34.4,
        "car-a1 command: own perception": 35.3,
        "car-a1 crosswalk enter": 36.6,
        "ped-17 crosswalk leave": 63.3,
        "car-a1 crosswalk leave": 66.1,
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TheCommittedSessions(TheTeeth, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from scenarios.crossing import teeth
        cls.teeth = teeth.measure(HERE / "crossing.mcap",
                                  HERE / "crossing-silenced.mcap")
        cls.session = teeth.Session(HERE / "crossing.mcap")

    def test_the_manifest_is_the_pinned_one(self):
        self.assertEqual(sha256(HERE / "MANIFEST.sha256"), MANIFEST_SHA256)

    def test_every_file_matches_the_manifest(self):
        rows = [line.split() for line in
                (HERE / "MANIFEST.sha256").read_text().splitlines() if line]
        self.assertEqual(sorted(name for _d, name in rows),
                         sorted(f"{a}.{p}" for a in ARMS for p in PARTS))
        for digest, name in rows:
            with self.subTest(file=name):
                self.assertEqual(sha256(HERE / name), digest)

    def test_the_provenance_names_each_recording(self):
        for arm in ARMS:
            with self.subTest(arm=arm):
                prov = json.loads((HERE / f"{arm}.provenance.json").read_text())
                self.assertEqual(prov["sha256"], sha256(HERE / f"{arm}.mcap"))
                self.assertEqual(prov["scene"], "crossing")
                self.assertIn("rate 1", prov["driven_by"])
        self.assertIn("SIGKILL mmwave-ut-04 at t=25", json.loads(
            (HERE / "crossing-silenced.provenance.json").read_text())["driven_by"])

    def test_the_beats_are_the_quoted_beats(self):
        from scenarios.crossing import events
        for arm, beats in BEATS.items():
            with self.subTest(arm=arm):
                got = events.extract(HERE / f"{arm}.truth.jsonl.gz")
                self.assertEqual({k: got.get(k) for k in beats}, beats)


if __name__ == "__main__":
    unittest.main()
