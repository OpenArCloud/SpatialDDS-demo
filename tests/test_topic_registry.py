#!/usr/bin/env python3
"""
The deployed topic names against the spec's registry, at the pinned commit.

A topic name is the whole of interop — two participants meet because they
chose the same string — and its failure mode is an empty bus, not an error.
So the strings this demo deploys (`spatialdds_demo/topics.py`) and the ones the
`spatialdds_ros` release it runs publishes are checked against the registry
in SpatialDDS-spec **at the commit `scripts/generate_types.py` pins, from the
checkout its resync command reads** — the same source the vendored IDL came
from, so the types and the names cannot be from two revisions.

Both directions fail. A deployed string that moves off its registered name
fails; a registry row that moves, or disappears, fails; a new `TOPIC_*`
constant that nobody has classified fails; a registry row appearing for a
string this file calls unregistered fails.

**One divergence is known and recorded rather than tolerated.** The registry
names the world-model entity lane `spatialdds/<scene>/model/entity/v0`; this
demo and `spatialdds_ros` both use `…/world/entity/v1`. Which side moves is a
pre-stamp decision for James (filed in spatialdds-ros `docs/spec-queue.md` §5).
`KNOWN_DIVERGENCE` pins both halves exactly, so the moment either side moves
the entry fails and has to be removed — it cannot outlive the disagreement.

Skips with a reason when the spec checkout or the commit is absent; the
blocker is probed, so the skip ends when the checkout exists.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import topics                           # noqa: E402

REGISTRY_FILE = "sections/v1.8/02-idl-profiles.md"

#: Deployed constant -> the registry row it implements (the row's first cell).
REGISTERED = {
    "TOPIC_DISCOVERY_ANNOUNCE_V1": "Announce",
    "TOPIC_DISCOVERY_DEPART_V1": "Depart",
    "TOPIC_DISCOVERY_QUERY_V1": "CoverageQuery",
    "TOPIC_DISCOVERY_CONTENT_V1": "ContentAnnounce",
    "TOPIC_MODEL_ENTITY_V1": "model_entity",
}

#: Deployed constants the registry has no row for, and why that is right.
UNREGISTERED = {
    "TOPIC_VPS_QUERY_V1": "demo-owned VPS lane (a §3.3.2 deployment extension)",
    "TOPIC_VPS_RESULT_V1": "demo-owned VPS lane (a §3.3.2 deployment extension)",
    "TOPIC_BOOTSTRAP_QUERY_V1": "demo-owned bootstrap type (idl/demo18)",
    "TOPIC_BOOTSTRAP_RESPONSE_V1": "demo-owned bootstrap type (idl/demo18)",
    "TOPIC_DDS_ENVELOPE_V1": "demo-owned transport envelope",
    "TOPIC_MODEL_POSE_V1": "spec type with no registry row at the pin",
    "TOPIC_MODEL_COMMAND_V1": "spec type with no registry row at the pin",
    "TOPIC_FRAME_TRANSFORM_V1": "spec type with no topic name in the registry",
    "TOPIC_ANCHORS_DELTA": "`anchor_delta` is registered as a type, with no name",
    "TOPIC_DISCOVERY_RESPONSE": "CoverageResponse rides the query's reply_topic",
}

#: Deployed constant -> the registered name it knowingly differs from.
KNOWN_DIVERGENCE = {
    "TOPIC_MODEL_ENTITY_V1": "spatialdds/<scene>/model/entity/v0",
}


def _generate_types():
    spec = importlib.util.spec_from_file_location(
        "generate_types", REPO / "scripts" / "generate_types.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry_text(gt) -> str:
    checkout = Path(gt.SPEC_CHECKOUT)
    if not (checkout / ".git").exists():
        raise unittest.SkipTest(f"no SpatialDDS-spec checkout at {checkout}, "
                                f"which generate_types.py resyncs from")
    out = subprocess.run(
        ["git", "-C", str(checkout), "show",
         f"{gt.SPEC_COMMIT}:{REGISTRY_FILE}"],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise unittest.SkipTest(f"{checkout} lacks {gt.SPEC_COMMIT}:"
                                f"{REGISTRY_FILE}: {out.stderr.strip()}")
    return out.stdout


def registry(text: str) -> dict[str, str]:
    """
    Row name -> registered topic pattern, from the two tables that register
    names: Well-Known Discovery Topics, and §3.3.2's rows that carry one.
    """
    out: dict[str, str] = {}
    for heading in (r"#### \*\*Well-Known Discovery Topics",
                    r"#### \*\*3\.3\.2 Typed Topics Registry"):
        section = re.split(heading, text, maxsplit=1)[1]
        section = re.split(r"\n#{2,5} ", section, maxsplit=1)[0]
        for row in re.findall(r"^\|(.+)\|\s*$", section, re.M):
            cells = [c.strip() for c in row.split("|")]
            names = re.findall(r"`(spatialdds/[^`]+)`", row)
            if len(names) == 1:
                out[cells[0].strip("`")] = names[0]
    return out


def matches(name: str, pattern: str) -> bool:
    regex = "^" + "/".join(
        "[^/]+" if re.fullmatch(r"<[^>]+>", seg) else re.escape(seg)
        for seg in pattern.split("/")) + "$"
    return re.match(regex, name) is not None


def deployed() -> dict[str, str]:
    """
    Every topic name the module defines, chosen by value rather than by name:
    any module-level string that is a topic name, whatever it is called, and
    each `TOPIC_*` builder, called with a placeholder segment. (`TOPIC_SOURCE_*`
    are provenance labels, not names, and a prefix rule would have swept them.)
    """
    out = {}
    for name in dir(topics):
        value = getattr(topics, name)
        if callable(value) and name.startswith("TOPIC_"):
            value = value("x")
        if isinstance(value, str) and value.startswith("spatialdds/"):
            out[name] = value
    return out


class TheDeployedNamesAreTheRegisteredOnes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.gt = _generate_types()
        cls.registry = registry(_registry_text(cls.gt))
        cls.deployed = deployed()

    def test_the_registry_was_read_at_the_pin(self):
        """An empty registry would pass everything below."""
        self.assertIn(f"archive {self.gt.SPEC_COMMIT} idl/v1.8",
                      self.gt.__doc__, "the prose pin and the constant differ")
        self.assertIn("Announce", self.registry)
        self.assertIn("model_entity", self.registry)
        self.assertGreaterEqual(len(self.registry), 6)

    def test_every_deployed_topic_is_classified(self):
        classified = set(REGISTERED) | set(UNREGISTERED)
        self.assertEqual(sorted(set(self.deployed) - classified), [],
                         "a new topic constant needs a registry row or a reason")
        self.assertEqual(sorted(classified - set(self.deployed)), [],
                         "a classified constant no longer exists")
        self.assertFalse(set(REGISTERED) & set(UNREGISTERED))

    def test_registered_names_match_in_both_directions(self):
        for constant, row in REGISTERED.items():
            with self.subTest(constant=constant):
                self.assertIn(row, self.registry,
                              f"the registry no longer has a {row!r} row")
                pattern, name = self.registry[row], self.deployed[constant]
                if constant in KNOWN_DIVERGENCE:
                    self.assertEqual(
                        pattern, KNOWN_DIVERGENCE[constant],
                        "the registry moved: remove the divergence entry")
                    self.assertFalse(
                        matches(name, pattern),
                        "the deployed name now matches: remove the entry")
                else:
                    self.assertTrue(matches(name, pattern),
                                    f"{name} is not {pattern}")

    def test_unregistered_names_stay_unregistered(self):
        """If the registry starts naming one of these, it must move above."""
        for constant in UNREGISTERED:
            name = self.deployed[constant]
            for row, pattern in self.registry.items():
                with self.subTest(constant=constant, row=row):
                    self.assertFalse(matches(name, pattern),
                                     f"{name} is now registered as {row!r}")

    def test_the_library_release_says_the_same(self):
        """The strings the pinned spatialdds_ros release publishes are the
        demo's, so the registry check above covers them too — checked
        against the release requirements.txt pins, not whatever is
        installed."""
        import spatialdds_ros
        from spatialdds_ros.announce import ANNOUNCE_TOPIC
        from spatialdds_ros.topics import Topics
        pin = re.search(r"^spatialdds_ros==(\S+)",
                        (REPO / "requirements.txt").read_text(), re.M).group(1)
        self.assertEqual(spatialdds_ros.__version__, pin,
                         "installed spatialdds_ros is not the pinned release: "
                         "pip install -r requirements.txt")
        lib = Topics(topics.SCENE)
        self.assertEqual(lib.entity, topics.TOPIC_MODEL_ENTITY_V1)
        self.assertEqual(lib.pose, topics.TOPIC_MODEL_POSE_V1)
        self.assertEqual(lib.command, topics.TOPIC_MODEL_COMMAND_V1)
        self.assertEqual(lib.frame_transform, topics.TOPIC_FRAME_TRANSFORM_V1)
        self.assertEqual(ANNOUNCE_TOPIC, topics.TOPIC_DISCOVERY_ANNOUNCE_V1)


if __name__ == "__main__":
    unittest.main()
