"""
The synthetic query frame claims nothing the venue's anchor config doesn't.

web/public/query-frames-synthetic/ is what a fresh clone localizes with. Its
whole licence is that it is labelled synthetic and anchored where the venue
already says it is, so both are pinned here rather than trusted to the
generator: the manifest must be exactly what
scripts/make_synthetic_query_frame.py would write from
bridges/web_bridge/tests/frame_anchors_fountain.json, it must carry no
real-dataset provenance (mapId, datasetId), and the frame must say SYNTHETIC
in its name and its JPEG comment.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "web" / "public" / "query-frames-synthetic"
ANCHORS = REPO / "bridges" / "web_bridge" / "tests" / "frame_anchors_fountain.json"
GENERATOR = REPO / "scripts" / "make_synthetic_query_frame.py"


class SyntheticQueryFrameTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((BUNDLE / "manifest.json").read_text())

    def test_manifest_is_what_the_anchor_config_generates(self):
        run = subprocess.run([sys.executable, str(GENERATOR), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_anchor_is_the_venue_anchor_exactly(self):
        (fqn, a), = json.loads(ANCHORS.read_text()).items()
        self.assertEqual(self.manifest["mapFqn"], fqn)
        self.assertEqual(self.manifest["anchor"],
                         {"lat_deg": a["latitude"], "lon_deg": a["longitude"],
                          "alt_m": a["height"]})

    def test_labelled_and_without_dataset_provenance(self):
        self.assertIs(self.manifest["synthetic"], True)
        self.assertIn("SYNTHETIC", self.manifest["label"])
        self.assertNotIn("mapId", self.manifest)
        self.assertNotIn("datasetId", self.manifest)

    def test_frames_are_named_and_tagged_synthetic(self):
        self.assertTrue(self.manifest["frames"])
        on_disk = sorted(p.name for p in BUNDLE.iterdir() if p.name != "manifest.json")
        self.assertEqual(on_disk, sorted(self.manifest["frames"]))
        for name in self.manifest["frames"]:
            self.assertIn("synthetic", name)
            data = (BUNDLE / name).read_bytes()
            self.assertEqual(data[:3], b"\xff\xd8\xff", f"{name} is not a JPEG")
            # JPEG COM segment, written by the generator.
            self.assertIn(b"\xff\xfe", data[:4096])
            self.assertIn(b"SYNTHETIC query frame, not a photograph", data[:4096])


if __name__ == "__main__":
    unittest.main()
