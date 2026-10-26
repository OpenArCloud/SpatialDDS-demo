#!/usr/bin/env python3
"""
The deployed topic names against the spec's registry, at the pinned commit.

A topic name is the whole of interop — two participants meet because they
chose the same string — and its failure mode is an empty bus, not an error.
So the strings this demo deploys and the ones the `spatialdds_ros` release it
runs publishes are checked against the registry in SpatialDDS-spec **at the
commit the SDK's registry was generated from** (`sd.dds.registry.pin()`), read
from the checkout beside this one. Two kinds of name are deployed:

* this deployment's own, in `spatialdds_demo/lanes.py`: the VPS, bootstrap,
  blob, reply and anchor lanes, which the registry does not name;
* the venue's lanes, which the SDK names from its registry when the venue
  opens them (`sd.dds.registry.topic_name`), and the well-known discovery
  topics. Reading those here, from the spec text, by a parser that is not the
  SDK's generator, is a second reading of the same rows.

Until the SDK adoption the subject was `spatialdds_demo/topics.py`, and the
pin was `scripts/generate_types.py`'s, from which the v1.8 IDL tree was
vendored.

Both directions fail. A deployed string that moves off its registered name
fails; a registry row that moves, or disappears, fails; a new deployed name
that nobody has classified fails; a registry row appearing for a string this
file calls unregistered fails.

**Divergences are recorded rather than tolerated.** `KNOWN_DIVERGENCE` pins
both halves of one exactly, so the moment either side moves the entry fails
and has to be removed. The one it held (the registry's `model/entity/v0`
against the deployed `world/entity/v1`) expired at the SDK's pin, where the
registry names `world/entity/v1`.

Skips with a reason when the spec checkout or the commit is absent; the
blocker is probed, so the skip ends when the checkout exists.
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

import spatialdds as sd                                       # noqa: E402

from spatialdds_demo import lanes                              # noqa: E402

SPEC_CHECKOUT = REPO.parent / "SpatialDDS-spec"
REGISTRY_FILE = sd.dds.registry.pin()["source"]

#: Deployed name -> the registry row it implements (the row's first cell).
REGISTERED = {
    "Announce": "Announce",
    "Depart": "Depart",
    "CoverageQuery": "CoverageQuery",
    "ContentAnnounce": "ContentAnnounce",
    "model_entity": "model_entity",
    "model_pose": "model_pose",
    "model_command": "model_command",
}

#: Deployed names the registry has no row for, and why that is right.
UNREGISTERED = {
    "lanes.TOPIC_VPS_QUERY": "demo-owned VPS lane (a §3.3.2 deployment extension)",
    "lanes.TOPIC_VPS_RESULT": "demo-owned VPS lane (a §3.3.2 deployment extension)",
    "lanes.TOPIC_BOOTSTRAP_QUERY": "demo-owned bootstrap type (idl/demo18)",
    "lanes.TOPIC_BOOTSTRAP_RESPONSE": "demo-owned bootstrap type (idl/demo18)",
    "lanes.RELATIONSHIP_TOPIC": "demo-owned extension (§3.3.2): the OWM 0.2 "
                                "relationship prototype, which spatial.owm/0.1 "
                                "reserves as open design",
    "lanes.BLOB_TOPIC": "1.8 defines BlobChunk and names no lane (candidates 9.6)",
    "lanes.anchors_delta": "`anchor_delta` is registered as a type, with no name",
    "lanes.reply_topic": "CoverageResponse rides the query's reply_topic",
    "frame_transform": "spec type with no topic name in the registry",
}

#: Deployed name -> the registered name it knowingly differs from. Empty: see
#: the module docstring for the one it held.
KNOWN_DIVERGENCE: dict = {}


def _registry_text() -> str:
    commit = sd.dds.registry.pin()["commit"]
    if not (SPEC_CHECKOUT / ".git").exists():
        raise unittest.SkipTest(f"no SpatialDDS-spec checkout at {SPEC_CHECKOUT}")
    out = subprocess.run(
        ["git", "-C", str(SPEC_CHECKOUT), "show", f"{commit}:{REGISTRY_FILE}"],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise unittest.SkipTest(f"{SPEC_CHECKOUT} lacks {commit}:"
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
    Every topic name this deployment uses. Its own, from `lanes.py`, chosen by
    value rather than by name: any module-level string that is a topic name,
    and each function that builds one, called with a placeholder segment. The
    venue's, as the SDK names them when the venue opens them, and the
    well-known topics, as the SDK's registry holds them.
    """
    import inspect
    out = {}
    for name in dir(lanes):
        value = getattr(lanes, name)
        if inspect.isfunction(value) and inspect.getmodule(value) is lanes:
            try:
                value = value("x")
            except Exception:
                continue
        if isinstance(value, str) and value.startswith("spatialdds/"):
            out[f"lanes.{name}"] = value
    for message in ("Announce", "Depart", "CoverageQuery", "ContentAnnounce"):
        out[message] = sd.dds.registry.well_known(message).topic
    for type_name in ("model_entity", "model_pose", "model_command"):
        out[type_name] = sd.dds.registry.topic_name(lanes.SCENE, None, type_name)
    # As model_service opens it: stream "frames", an unregistered type.
    out["frame_transform"] = sd.dds.registry.topic_name(
        lanes.SCENE, "frames", "frame_transform")
    return out


class TheDeployedNamesAreTheRegisteredOnes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = _registry_text()
        cls.registry = registry(cls.text)
        cls.deployed = deployed()

    def test_the_registry_was_read_at_the_pin(self):
        """An empty registry would pass everything below, and a different
        revision would check the names against rows the SDK never read."""
        import hashlib
        self.assertEqual(hashlib.sha256(self.text.encode()).hexdigest(),
                         sd.dds.registry.pin()["source_sha256"])
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
        lib, ours = Topics(lanes.SCENE), self.deployed
        self.assertEqual(lib.entity, ours["model_entity"])
        self.assertEqual(lib.pose, ours["model_pose"])
        self.assertEqual(lib.command, ours["model_command"])
        self.assertEqual(lib.frame_transform, ours["frame_transform"])
        self.assertEqual(ANNOUNCE_TOPIC, ours["Announce"])


if __name__ == "__main__":
    unittest.main()
