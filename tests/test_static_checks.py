"""
Cheap static guards for the failures that unit tests structurally cannot catch.

Two real bugs sat in this repo for six days, both invisible to a green suite:

* `bridges/ros2_bridge/bridge_node.py` referenced `_TypedWriters` and
  `_image_chunks`, neither of which was ever defined. The bridge could not
  start, and once it could, it crashed on the first image. Its 41 unit tests
  passed throughout, because they test the conversion functions the node
  calls, not the node.
* The ROS 2 tier runner ran `test_envelope_roundtrip.py`, deleted when the
  envelope was removed. Because the tiers are an `&&` chain, that killed the
  two tiers after it — including the only one that runs the node — so the
  bugs above had nowhere to surface.

Neither needs a bus, a broker or ROS 2 to detect. Both take milliseconds.
"""

import os
import pathlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Generated output is not ours to lint, and idlc's star-imports trip the
# checker for reasons that say nothing about this repo's correctness.
SKIP_PREFIXES = ("spatialdds_idl/", "idl/", "web/public/")


def _tracked_python() -> list:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split()
            if p and not p.startswith(SKIP_PREFIXES)]


class NoUndefinedNames(unittest.TestCase):
    """
    A name used but never defined is a crash waiting for the right branch.

    pyflakes finds these without importing anything, so a module needing ROS 2
    or a GPU is checked as readily as one that is not — which is exactly the
    code least likely to be exercised by a routine test run.
    """

    def test_no_undefined_names(self):
        try:
            import pyflakes  # noqa: F401
        except ImportError:
            self.skipTest(
                "STATIC-CHECK-UNAVAILABLE: pip install pyflakes. This guard "
                "catches undefined names in modules the host suite never "
                "imports; skipping it hides exactly that class of bug.")

        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", *_tracked_python()],
            cwd=REPO, capture_output=True, text=True)
        # pyflakes reports many things; only undefined names are errors here.
        # Unused imports and star-import warnings are style, and this is a
        # correctness gate — a gate that also fails on style gets ignored.
        undefined = [ln for ln in result.stdout.splitlines()
                     if re.search(r"undefined name '", ln)]
        self.assertEqual(undefined, [], "undefined names:\n" + "\n".join(undefined))


class TestScriptsReferenceRealFiles(unittest.TestCase):
    """
    A tier that runs a deleted file fails for the wrong reason, and takes the
    tiers after it down with it.
    """

    HARNESSES = [
        "bridges/ros2_bridge/Dockerfile.test",
        "bridges/ros2_bridge/run_docker_tests.sh",
        "run_bridge_http_tests_docker.sh",
        "bridges/mqtt_bridge/docker-compose.test.yaml",
    ]

    def test_every_referenced_test_file_exists(self):
        pattern = re.compile(r"[\w./-]+/test_[\w-]+\.py")
        missing = []
        for harness in self.HARNESSES:
            path = REPO / harness
            if not path.exists():
                missing.append(f"{harness}: harness itself is missing")
                continue
            for referenced in set(pattern.findall(path.read_text())):
                if not (REPO / referenced).exists():
                    missing.append(f"{harness} runs {referenced}, which does not exist")
        self.assertEqual(missing, [], "\n".join(missing))


class CdkStackSynthesises(unittest.TestCase):
    """
    The deploy stack builds without executing AWS.

    `cdk synth` runs the stack definition as ordinary Python, so it catches
    what any other module would catch — a name used before assignment, a bad
    reference, a typo in a construct argument. It just is not run by anything,
    because the file only executes during a deploy.

    That cost a failed deployment: a config value read into `ar` was used by
    the image asset above where it was assigned, and `UnboundLocalError`
    surfaced only after the base image had been pulled and cdk had started.
    A second of synth beforehand would have said so.
    """

    def test_the_stack_synthesises(self):
        cdk_dir = REPO / "deploy" / "aws" / "cdk"
        if not (cdk_dir / "app.py").exists():
            self.skipTest("no CDK app in this checkout")
        try:
            import aws_cdk  # noqa: F401
        except ImportError:
            self.skipTest(
                "CDK-UNAVAILABLE: pip install aws-cdk-lib. Without it a stack "
                "that cannot even be constructed reaches a real deploy.")

        result = subprocess.run([sys.executable, "app.py"], cwd=cdk_dir,
                                capture_output=True, text=True, timeout=180)
        self.assertEqual(
            result.returncode, 0,
            f"cdk app failed to synthesise:\n{result.stderr[-2000:]}")


class EntryPointsAreImportable(unittest.TestCase):
    """
    Every runnable script parses and its module-level code is sound.

    Not an import — importing `bridge_node` needs rclpy — but a compile, which
    catches syntax errors and is free. The undefined-name guard above covers
    what compiling cannot.
    """

    def test_entry_points_compile(self):
        entry_points = [
            "ar_demo/spatialdds_demo_server.py",
            "ar_demo/spatialdds_catalog_server.py",
            "ar_demo/spatialdds_demo_client.py",
            "ar_demo/spatialdds_bootstrap_server.py",
            "ar_demo/http_binding.py",
            "bridges/ros2_bridge/bridge_node.py",
            "bridges/web_bridge/server.py",
            "bridges/mqtt_bridge/bridge.py",
            "bridges/mcap_bridge/recorder.py",
            "bridges/mcap_bridge/replayer.py",
            "multi_operator_fusion/fusion_service.py",
            "multi_operator_fusion/synthetic_publisher.py",
        ]
        for rel in entry_points:
            path = REPO / rel
            with self.subTest(entry_point=rel):
                self.assertTrue(path.exists(), f"{rel} is missing")
                compile(path.read_text(), str(path), "exec")


if __name__ == "__main__":
    unittest.main()


class MermaidDiagramsParse(unittest.TestCase):
    """
    Mermaid blocks that GitHub can actually render.

    A diagram that fails to parse does not degrade — GitHub replaces the whole
    block with "Unable to render rich display", so a README's most useful part
    silently becomes an error box. Nothing in the normal test run touches
    markdown, so it stays broken until someone opens the page.

    This is a targeted check, not a parser. It catches the construct that
    actually broke ar_demo/README.md: a semicolon in message or note text.
    Mermaid treats `;` as a statement separator, so `cached; dispose, Depart`
    ends the message at the semicolon and then fails on what follows. The rest
    of the line reads perfectly well to a human, which is what makes it worth a
    test rather than a review note.
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent
    # cdk.out holds build-cache copies of files that live elsewhere in the tree.
    SKIP = ("node_modules", "cdk.out", "directions")

    def _blocks(self):
        for md in sorted(self.ROOT.rglob("*.md")):
            rel = md.relative_to(self.ROOT)
            if any(part in self.SKIP for part in rel.parts):
                continue
            text = md.read_text(errors="ignore")
            for n, block in enumerate(
                    re.findall(r"```mermaid\n(.*?)```", text, re.S), 1):
                yield rel, n, block

    def test_no_semicolons_in_diagram_text(self):
        found = []
        for rel, n, block in self._blocks():
            for line in block.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("%%"):
                    continue
                # Only the text after the first ':' is message/note content.
                _, sep, text = stripped.partition(":")
                if sep and ";" in text:
                    found.append(f"{rel} (block {n}): {stripped}")
        self.assertEqual(found, [], "semicolons break mermaid parsing:\n  " +
                         "\n  ".join(found))

    def test_every_participant_is_declared(self):
        """
        An undeclared participant still renders, in first-use order rather than
        the order the diagram intends — so the columns silently rearrange.
        """
        problems = []
        for rel, n, block in self._blocks():
            if not block.lstrip().startswith("sequenceDiagram"):
                continue
            declared = set(re.findall(r"^\s*participant\s+(\w+)", block, re.M))
            used = set()
            for a, b in re.findall(r"^\s*(\w+)\s*-+>>?\s*(\w+)\s*:", block, re.M):
                used |= {a, b}
            for note in re.findall(r"^\s*Note over ([\w, ]+):", block, re.M):
                used |= {x.strip() for x in note.split(",")}
            missing = sorted(used - declared)
            if missing:
                problems.append(f"{rel} (block {n}): {', '.join(missing)}")
        self.assertEqual(problems, [], "undeclared mermaid participants:\n  " +
                         "\n  ".join(problems))
