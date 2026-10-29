"""The builders' stamps are the SDK's, and in range.

`spatialdds_demo.payloads` kept its own `stamp`, labelled, because
spatialdds 0.7.3's `sd.stamp` did not normalise: `sd.stamp(5.9999999999)`
had `nanosec` 1000000000, and `sd.stamp(-0.25)` was +0.75 s (spatialdds-py
S16). 0.7.4 fixed it and the copy is gone, so these hold the builders to
the SDK's answer at the two values that used to be wrong.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import spatialdds as sd  # noqa: E402

from spatialdds_demo import payloads  # noqa: E402

CASES = {5.9999999999: {"sec": "6", "nanosec": 0},
         -0.25: {"sec": "-1", "nanosec": 750000000}}


class Stamps(unittest.TestCase):
    def test_the_sdk_normalises(self):
        for t, want in CASES.items():
            self.assertEqual(sd.stamp(t), want, f"sd.stamp({t})")

    def test_the_builders_stamp_through_it(self):
        for t, want in CASES.items():
            pose = payloads.framed_pose(0, 0, 0, [0, 0, 0, 1], "map/x", t)
            self.assertEqual(pose["stamp"], want, f"framed_pose at {t}")

    def test_no_copy_of_its_own(self):
        self.assertFalse(hasattr(payloads, "stamp"),
                         "payloads has a stamp of its own again: use sd.stamp")


if __name__ == "__main__":
    unittest.main()
