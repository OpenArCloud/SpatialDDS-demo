#!/usr/bin/env python3
"""
The robot lane's generated types and the SDK's agree on the wire.

**The two-copies-drift class, after the copies became two packages.** Until
the SDK adoption this repository shipped a root `spatialdds_idl18`, and
`spatialdds_ros` installs one of its own, vendored from it. This gate held the
two byte-identical. The census's answer was one tree, not two: the root copy
is retired, so the robot lane (`spatialdds_ros`) imports the library's tree,
and everything else here imports the SDK's types (`spatialdds.dds.types`).

So there are still two generated trees on one bus, from two generators and two
spec checkouts (the library's from SpatialDDS-spec 9c3e1f2, the SDK's from the
`v1.8` tag), and **the failure mode is still not an exception**: two trees
from different revisions decode each other's bytes into plausible wrong values.
Nothing raises.

What is compared is what DDS compares: each type's **minimal** XTypes
TypeObject, by its identifier. That is the form type assignability is decided
on, and it carries every member, its type, its order and the extensibility.
The **complete** form differs, and is not compared, on purpose: cyclonedds-python
names each typedef by the Python package it was generated into
(`spatialdds_idl18::spatial::common::Vec3` against
`spatialdds::dds::types::spatial::common::Vec3`), so the two complete forms
differ for every type that uses a typedef while the wire is the same.
Measured, not assumed: for the nine types below the complete identifiers
differ for seven and the minimal ones agree for all nine.

The subject is every type `spatialdds_ros` imports from its tree, read off the
library's source, so a type it starts using cannot be left unchecked.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _library_tree() -> Path | None:
    """
    The library's `spatialdds_idl18`, resolved where this repository cannot
    shadow it (a subprocess, outside the checkout). Until the root copy was
    retired, importing from here found ours; the test must never compare a
    tree with itself.
    """
    code = ("import importlib.util, os;"
            "spec = importlib.util.find_spec('spatialdds_idl18');"
            "print(os.path.dirname(spec.origin) if spec else '')")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd="/", env={**os.environ, "PYTHONPATH": ""})
    path = result.stdout.strip()
    return Path(path) if path else None


def _used_types() -> list[tuple[str, str]]:
    """(module, Type) for every name `spatialdds_ros` imports from its tree."""
    spec = importlib.util.find_spec("spatialdds_ros")
    if spec is None:
        raise unittest.SkipTest("spatialdds_ros is not installed: "
                                "pip install -r requirements.txt")
    out = set()
    pattern = re.compile(
        r"from spatialdds_idl18\.([\w.]+) import \(?([\w\s,]+?)\)?\s*$", re.M)
    for path in Path(spec.origin).parent.glob("*.py"):
        text = path.read_text()
        # Parenthesised imports span lines; fold them first.
        text = re.sub(r"\(\s*([^)]*?)\s*\)", lambda m: " ".join(m.group(1).split()),
                      text)
        for module, names in pattern.findall(text):
            for name in names.replace(",", " ").split():
                out.add((module, name))
    return sorted(out)


def _minimal(cls):
    """The minimal TypeIdentifier of a generated class: what DDS matches on."""
    complete = cls.__idl__.get_type_id()
    for pair in cls.__idl__.get_type_mapping().identifier_complete_minimal:
        if pair.type_identifier1 == complete:
            return pair.type_identifier2
    return complete


class TheRobotLaneTypesAgreeOnTheWire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = _library_tree()
        if cls.tree is None:
            raise unittest.SkipTest("spatialdds_ros's spatialdds_idl18 is not "
                                    "installed: pip install -r requirements.txt")
        cls.types = _used_types()

    def test_the_library_tree_is_the_library_s(self):
        """This repository no longer carries one, so nothing can shadow it."""
        self.assertFalse((REPO / "spatialdds_idl18").exists(),
                         "a root spatialdds_idl18 is back; it shadows the "
                         "library's from this directory")
        self.assertIn("site-packages", str(self.tree))

    def test_the_subject_is_not_empty(self):
        """The guard on the guard: no types agree perfectly."""
        names = {name for _, name in self.types}
        self.assertGreaterEqual(len(names), 8, f"only {sorted(names)}")
        for expected in ("Announce", "Entity", "ModelPose", "ModelCommand",
                         "FrameTransform"):
            self.assertIn(expected, names)

    def test_every_type_the_library_uses_matches_the_sdk_s(self):
        for module, name in self.types:
            with self.subTest(type=f"{module}.{name}"):
                ours = getattr(importlib.import_module(
                    f"spatialdds.dds.types.{module}"), name)
                theirs = getattr(importlib.import_module(
                    f"spatialdds_idl18.{module}"), name)
                if not hasattr(theirs, "__idl__"):
                    continue          # an enum or a typedef: checked through its users
                self.assertEqual(_minimal(ours), _minimal(theirs),
                                 f"{module}.{name}: the library's type and the "
                                 f"SDK's are not the same on the wire")


if __name__ == "__main__":
    unittest.main()
