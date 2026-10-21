#!/usr/bin/env python3
"""
Every SpatialDDS URI this repository writes follows Appendix F.

Until 2026-10-21 the repository held **zero** conformant `spatialdds://`
URIs: 42 occurrences across 26 files, all of the form
`spatialdds://<authority>/zone:<zone>/<manifest|service>:<id>`, which neither
1.7's nor 1.8's ABNF admits. The validator enforced that same invented shape,
so validator and corpus agreed with each other and with nothing else -- the
reference gateway uses `spatialdds://<authority>/<zone>/<rtype>/<rid>`
throughout. A check of the validator alone could never have found it; the
corpus has to be read.

Literals assembled at runtime (f-strings with `{...}`) are skipped by
construction: they are templates, not URIs, and the values they are filled
with are checked where they are used.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_validation import SpatialDDSValidator, ValidationError  # noqa: E402

URI = re.compile(r"spatialdds://[A-Za-z0-9.\-]+/[^\s\"'`)<>,\]]+")
# History and vendored text are not the repository's own claims.
EXEMPT = ("spatialdds_idl", "idl/", "directions/", "web/node_modules",
          "docs/SpatialDDS-1.7-full.md",
          # Deleted with the pre-1.8 trees; this exemption leaves with them.
          "manifests/v1.4/", "manifests/v1.6/")


def corpus():
    files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                           text=True).stdout.split()
    for f in files:
        if f.startswith(EXEMPT) or not f.endswith(
                (".py", ".ts", ".json", ".sh", ".md", ".yml", ".yaml")):
            continue
        try:
            text = (REPO / f).read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for m in URI.finditer(text):
            uri = m.group(0).rstrip(".;:")
            if "{" in uri:                     # an f-string template
                continue
            yield f, text.count("\n", 0, m.start()) + 1, uri


class EveryUriIsAppendixF(unittest.TestCase):
    def test_the_corpus_is_not_empty(self):
        """A gate that finds nothing to check proves nothing."""
        self.assertGreater(len(list(corpus())), 10)

    def test_every_literal_uri_parses(self):
        bad = []
        for f, line, uri in corpus():
            try:
                SpatialDDSValidator.validate_spatial_uri(uri)
            except ValidationError:
                bad.append(f"{f}:{line}  {uri}")
        self.assertEqual(bad, [], "not Appendix F:\n  " + "\n  ".join(bad))

    def test_the_old_shape_is_rejected(self):
        # Assembled rather than written as one literal, so this file's own
        # negative example is not read as a claim by the corpus check above.
        old = "spatialdds://" + "vps.example.com/zone:sf-downtown/manifest:vps"
        with self.assertRaises(ValidationError):
            SpatialDDSValidator.validate_spatial_uri(old)

    def test_the_specs_own_examples_pass(self):
        for uri in ("spatialdds://museum.example.org/hall1/anchor/01J8QDFQX3W9X4CEX39M9ZP6TQ",
                    "spatialdds://city.example.net/downtown/service/01HA7M6XVBTF6RWCGN3X05S0SM;v=2024-q2",
                    "spatialdds://studio.example.com/stage/content/01HCQF7DGKKB3J8F4AR98MJ6EH"):
            with self.subTest(uri=uri):
                SpatialDDSValidator.validate_spatial_uri(uri)


if __name__ == "__main__":
    unittest.main()
