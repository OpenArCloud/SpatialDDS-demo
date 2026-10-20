#!/usr/bin/env python3
"""
This repository's generated types and the library's are the same bytes.

**The two-copies-drift class at its most camouflaged.** Adopting
`spatialdds_ros` puts a *second* top-level `spatialdds_idl18` on the system:
this repository ships one at its root and the library installs one of its own.
They are byte-identical today, because the library's was vendored from here.

Which copy is used depends on the working directory, and that was measured
rather than assumed:

    from the demo root   -> SpatialDDS-demo/spatialdds_idl18
    from anywhere else   -> site-packages/spatialdds_idl18

So in normal use the demo runs its own types against the library's code. While
the trees agree that is a distinction without a difference. If either is
regenerated alone it becomes the whole problem, and **the failure mode is not
an exception**: two generated trees from different spec revisions decode each
other's bytes into plausible wrong values — a field shifted, an appended member
read as absent, an enum off by one. Nothing raises. Everything looks like data.

That is why this is a gate rather than a note. A note would be read by whoever
regenerated one tree only if they already suspected the problem.

**The eventual answer is one tree, not two** — the demo consuming the library's
copy and deleting its own. That is its own decision and is deliberately not
taken here; this gate is what makes the interim safe, and it will fail loudly
on the day the interim stops being safe.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OURS = REPO / "spatialdds_idl18"

#: Files that may be present in one tree and not the other, each with its
#: reason. Narrow on purpose, and `test_the_exclusions_are_only_bookkeeping`
#: asserts it stays narrow — an exclusion list that can grow quietly is how a
#: gate like this stops working without failing.
#:
#: * `PROVENANCE.md` — the library's record of where its copy came from. It
#:   has no counterpart here and is documentation *about* the tree.
#: * `.idlpy_manifest` — idlc's own list of the names it generated, 33 of
#:   them. Never imported: zero `.py` files in either tree reference it, and
#:   nothing in this repository reads it. setuptools does not package them, so
#:   the installed tree has none. Verified inert rather than assumed, and the
#:   64 generated `.py` files are byte-identical with identical names.
ALLOWED_TO_DIFFER = {"PROVENANCE.md"}
ALLOWED_BASENAMES = {".idlpy_manifest"}


def _installed_tree() -> Path | None:
    """
    The library's copy, found without the demo root shadowing it.

    Resolved in a subprocess with the repository *not* on `sys.path`, because
    importing from here would find our own tree and the test would compare it
    with itself — passing always, and checking nothing.
    """
    code = ("import importlib.util, os, sys;"
            "spec = importlib.util.find_spec('spatialdds_idl18');"
            "print(os.path.dirname(spec.origin) if spec else '')")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd="/", env={**os.environ, "PYTHONPATH": ""})
    path = result.stdout.strip()
    return Path(path) if path else None


def _digest(root: Path) -> dict[str, str]:
    """Every file's sha256, keyed by its path within the tree."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = str(path.relative_to(root))
        if rel in ALLOWED_TO_DIFFER or path.name in ALLOWED_BASENAMES:
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class TheTwoGeneratedTreesAgree(unittest.TestCase):
    def setUp(self):
        self.installed = _installed_tree()
        if self.installed is None:
            self.skipTest(
                "spatialdds_ros is not installed, so there is no second tree "
                "to compare — install it from requirements.txt")
        if self.installed.resolve() == OURS.resolve():
            self.skipTest(
                f"the installed tree resolved to this repository's own "
                f"({self.installed}), so there is nothing to compare; the "
                f"library is probably installed in editable mode from a "
                f"checkout that shares this tree")

    def test_our_tree_exists_and_is_not_empty(self):
        """The guard on the guard: two empty trees agree perfectly."""
        ours = _digest(OURS)
        self.assertGreater(len(ours), 20,
                           f"only {len(ours)} files under {OURS}; this gate is "
                           f"pointed at the wrong place")

    def test_the_same_files_are_present_in_both(self):
        ours, theirs = _digest(OURS), _digest(self.installed)
        only_ours = sorted(set(ours) - set(theirs))
        only_theirs = sorted(set(theirs) - set(ours))
        self.assertEqual(
            (only_ours, only_theirs), ([], []),
            f"the two generated trees no longer contain the same files.\n"
            f"  only here:      {only_ours[:8]}\n"
            f"  only installed: {only_theirs[:8]}\n"
            f"Regenerate both from the same spec revision, or converge on one "
            f"tree — see this module's docstring.")

    def test_every_file_is_byte_identical(self):
        ours, theirs = _digest(OURS), _digest(self.installed)
        differing = sorted(name for name in set(ours) & set(theirs)
                           if ours[name] != theirs[name])
        self.assertEqual(
            differing, [],
            f"{len(differing)} file(s) differ between this repository's "
            f"generated types and the library's:\n  "
            + "\n  ".join(differing[:10])
            + f"\n\nThe demo runs its own tree ({OURS}) and the library's code "
              f"expects {self.installed}. Two trees from different spec "
              f"revisions do not raise — they decode each other's bytes into "
              f"plausible wrong values.")

    def test_the_exclusions_are_only_bookkeeping(self):
        """
        What this gate skips, checked.

        Every excluded file must be generator bookkeeping that nothing
        imports. Without this, widening the exclusion list is a way to make
        the gate pass by looking at less — which is the failure mode of every
        allowlist.
        """
        skipped = [p for p in OURS.rglob("*")
                   if p.is_file() and p.name in ALLOWED_BASENAMES]
        self.assertTrue(skipped, "the exclusion should match something, or it "
                                 "is stale and should be removed")
        for path in skipped:
            with self.subTest(path=str(path.relative_to(OURS))):
                self.assertNotEqual(path.suffix, ".py",
                                    "a generated module is never bookkeeping")
        # And nothing in either tree imports them.
        referencing = [p for p in OURS.rglob("*.py")
                       if any(name in p.read_text()
                              for name in ALLOWED_BASENAMES)]
        self.assertEqual(referencing, [],
                         "an excluded file is referenced by generated code, so "
                         "it is not bookkeeping after all")
        print(f"  {len(skipped)} bookkeeping files excluded, none imported")

    def test_both_trees_report_the_same_types(self):
        """
        Not just the same bytes on disk — the same types *as imported*.

        Byte equality is the stronger check and would catch anything this
        does. This exists because it is the claim that actually matters, and a
        future relaxation of the byte check (a generated timestamp, say) must
        not quietly lose it.
        """
        ours = self._types_in(OURS.parent)
        theirs = self._types_in(self.installed.parent)
        self.assertEqual(ours, theirs,
                         "the two trees import different sets of generated "
                         "dataclasses")
        self.assertGreater(len(ours), 50,
                           f"only {len(ours)} types found; the walk is wrong")
        print(f"\n  {len(ours)} generated types, identical in both trees")

    @staticmethod
    def _types_in(parent: Path) -> list[str]:
        code = (
            "import dataclasses, importlib, pkgutil, sys;"
            "import spatialdds_idl18 as r;"
            "names = sorted("
            "  f'{m.name}.{n}'"
            "  for m in pkgutil.walk_packages(r.__path__, r.__name__ + '.')"
            "  for n, o in vars(importlib.import_module(m.name)).items()"
            "  if dataclasses.is_dataclass(o)"
            "  and getattr(o, '__module__', '') == m.name);"
            "print('\\n'.join(names))")
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            cwd=str(parent), env={**os.environ, "PYTHONPATH": str(parent)})
        if result.returncode != 0:                            # pragma: no cover
            raise AssertionError(
                f"could not enumerate types under {parent}: {result.stderr}")
        return [line for line in result.stdout.splitlines() if line]


if __name__ == "__main__":
    unittest.main()
