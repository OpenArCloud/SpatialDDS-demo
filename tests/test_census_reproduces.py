"""The adoption census's numbers reproduce from their source.

`docs/sdk-adoption-census.md` is a ruled record: its tables describe the tree
at the commit the census was taken at, and its "after" block the working tree
now, both counted by `tools/sdk_adoption_census.py`. Nothing ran the tool's
check until the end of the migration, and by then it raised on the first
module the census never saw (`lanes.py`). This runs it.
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TheCensusReproduces(unittest.TestCase):
    def test_the_tables_and_the_shrink_match_their_trees(self):
        has_history = subprocess.run(
            ["git", "cat-file", "-e", "f4890e8^{commit}"], cwd=REPO,
            capture_output=True).returncode == 0
        if not has_history:
            raise unittest.SkipTest("GIT-HISTORY: the census commit is not in "
                                    "this clone (shallow?), so its tree cannot "
                                    "be read")
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "sdk_adoption_census.py"),
             "--check-doc"], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
