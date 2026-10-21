#!/usr/bin/env python3
"""
One era: no 1.7 import and no live pre-1.8 reference outside the exhibit.

The repository is SpatialDDS 1.8 throughout except `tests/cross_era/`, which
keeps a 1.7 participant on purpose (see its README). This gate is what makes
"except" mean one directory rather than a tendency. Nothing before it asserted
one era: `tests/test_idl_trees_agree.py` compares two copies of *1.8*, and
the era-claims gate checks the literals it knows the shape of.

**What counts.** The era is the one vendored spec bundle, `idl/v<era>`;
anything versioned below it is pre-era. Four properties:

1. **No pre-era import.** No tracked Python file outside the exhibit imports
   the 1.7 package, statically or by string.
2. **No pre-era artifact.** One `idl/v*` bundle and one `manifests/v*` set,
   both the era; and every generated tree's `MODULE_ID`s are the era (or a
   provisional module's own `0.x`).
3. **No live pre-era reference.** Nothing that would be *used* names a
   retired path or the 1.7 package: Python string literals other than
   docstrings, non-comment lines of shell, Dockerfiles, YAML, JSON, TypeScript
   and IDL, and Markdown link targets. Prose that says what happened -- a
   comment, a docstring, a census -- is history and stays; a command, a
   COPY, an import or a link that points at what is gone is a defect.
4. **Not importable at all.** From the repository root, with the repository
   on the path, the 1.7 package does not import -- which also catches one
   installed in the environment, which no static check can see.

The exhibit is exempt as a directory, and this file is exempt because it has
to name what it forbids. Nothing else is: `tests/test_cross_era_fixture.py`
takes the 1.7 package's name from the exhibit's directory instead of spelling
it, so that it passes as any other file does.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXHIBIT = "tests/cross_era/"
SELF = "tests/test_one_era.py"
OLD_PACKAGE = "spatialdds_idl"           # the 1.7 package's import name

BUNDLE = re.compile(r"^v1\.(\d+)$")


def era_minor() -> int:
    bundles = [p.name for p in (REPO / "idl").iterdir()
               if p.is_dir() and BUNDLE.match(p.name)]
    if len(bundles) != 1:
        raise AssertionError(f"expected one spec bundle under idl/, found "
                             f"{sorted(bundles)}")
    return int(BUNDLE.match(bundles[0]).group(1))


def retired_patterns(minor: int) -> list:
    """What a live reference to an earlier era looks like."""
    before = "|".join(str(n) for n in range(minor))
    return [
        re.compile(r"(?<![\w.])(?:idl|manifests)/v1\.(?:%s)(?![\d])" % before),
        re.compile(r"(?<![\w.])idl/demo(?![\w])"),
        re.compile(r"(?<![\w./])%s(?![\w])" % re.escape(OLD_PACKAGE)),
    ]


def tracked() -> list:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"],
                         cwd=REPO, capture_output=True, text=True,
                         check=True).stdout.split("\n")
    return sorted(f for f in out
                  if f and not f.startswith((EXHIBIT, "directions/"))
                  and f != SELF and (REPO / f).is_file())


# --- 1 · imports -------------------------------------------------------------

def old_imports(path: str, source: str) -> list:
    hits = []
    for node in ast.walk(ast.parse(source, filename=path)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names = [node.module or ""]
        for name in names:
            if name.split(".")[0] == OLD_PACKAGE:
                hits.append(f"{path}:{node.lineno}: imports {name}")
    return hits


# --- 3 · live references -----------------------------------------------------

def _docstring_nodes(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def live_python(path: str, source: str, patterns) -> list:
    tree = ast.parse(source, filename=path)
    docs = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docs:
            for p in patterns:
                if p.search(node.value):
                    hits.append(f"{path}:{node.lineno}: {node.value[:80]!r}")
    return hits


COMMENT = {".sh": "#", ".yml": "#", ".yaml": "#", ".toml": "#", ".cfg": "#",
           ".ts": "//", ".js": "//", ".mjs": "//", ".idl": "//"}


def live_lines(path: str, text: str, patterns) -> list:
    name = Path(path).name
    marker = "#" if name.startswith("Dockerfile") else COMMENT.get(
        Path(path).suffix)
    hits = []
    in_block = False
    for n, line in enumerate(text.splitlines(), 1):
        code = line
        if marker == "//":
            if in_block:
                if "*/" not in code:
                    continue
                code, in_block = code.split("*/", 1)[1], False
            if "/*" in code:
                head, _, rest = code.partition("/*")
                in_block = "*/" not in rest
                code = head + (rest.split("*/", 1)[1] if not in_block else "")
            if code.lstrip().startswith("*"):
                continue
        if marker:
            code = code.split(marker, 1)[0] if marker == "//" \
                else ("" if code.lstrip().startswith("#") else code)
        if any(p.search(code) for p in patterns):
            hits.append(f"{path}:{n}: {line.strip()[:80]}")
    return hits


LINK = re.compile(r"\]\(([^)\s]+)\)")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
CODE_SPAN = re.compile(r"(`+)(?!`).+?(?<!`)\1", re.S)


def live_links(path: str, text: str, patterns) -> list:
    """Link targets only: one quoted in code renders as text, not a link."""
    text = CODE_SPAN.sub("", FENCE.sub("", text))
    return [f"{path}: links to {m.group(1)}"
            for m in LINK.finditer(text)
            if any(p.search(m.group(1)) for p in patterns)]


def scan(files, patterns) -> dict:
    found = {"imports": [], "references": []}
    for f in files:
        path = REPO / f
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        suffix = path.suffix
        if suffix == ".py":
            try:
                found["imports"] += old_imports(f, text)
                found["references"] += live_python(f, text, patterns)
            except SyntaxError:
                found["references"].append(f"{f}: does not parse")
        elif suffix == ".md":
            found["references"] += live_links(f, text, patterns)
        elif suffix == ".json":
            found["references"] += [
                f"{f}: {s[:80]!r}" for s in _json_strings(text)
                if any(p.search(s) for p in patterns)]
        elif suffix in COMMENT or path.name.startswith("Dockerfile"):
            found["references"] += live_lines(f, text, patterns)
    return found


def _json_strings(text: str):
    try:
        stack = [json.loads(text)]
    except ValueError:
        return
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            stack += list(v.keys()) + list(v.values())
        elif isinstance(v, list):
            stack += v


# --- the checks --------------------------------------------------------------

class TheRepositoryIsOneEra(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.minor = era_minor()
        cls.patterns = retired_patterns(cls.minor)
        cls.files = tracked()
        cls.found = scan(cls.files, cls.patterns)

    def test_the_scan_saw_the_repository(self):
        """A gate that reads nothing passes everything."""
        py = [f for f in self.files if f.endswith(".py")]
        self.assertGreater(len(py), 150, f"only {len(py)} Python files seen")
        self.assertIn("spatialdds_demo/model_service.py", self.files)
        self.assertIn("Dockerfile", self.files)

    def test_no_pre_era_import(self):
        self.assertEqual(self.found["imports"], [])

    def test_no_live_pre_era_reference(self):
        self.assertEqual(self.found["references"], [])

    def test_one_bundle_and_one_manifest_set(self):
        self.assertEqual(
            sorted(p.name for p in (REPO / "manifests").iterdir() if p.is_dir()),
            [f"v1.{self.minor}"])

    def test_every_generated_module_is_the_era(self):
        mid = re.compile(r'^MODULE_ID\s*=\s*"([^"]+)"', re.M)
        seen = 0
        for f in self.files:
            if not f.endswith(".py") or "/_" not in f:
                continue
            for m in mid.finditer((REPO / f).read_text()):
                seen += 1
                major_minor = m.group(1).split("/")[-1]
                with self.subTest(file=f, module_id=m.group(1)):
                    self.assertTrue(major_minor == f"1.{self.minor}"
                                    or major_minor.startswith("0."))
        self.assertGreater(seen, 10, "found almost no MODULE_IDs to check")

    def test_the_old_package_does_not_import(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(REPO)
        result = subprocess.run(
            [sys.executable, "-c", f"import {OLD_PACKAGE}"], cwd=REPO, env=env,
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0,
                            f"{OLD_PACKAGE} imports from the repository root")
        self.assertIn("ModuleNotFoundError", result.stderr)


class TheGateCanFail(unittest.TestCase):
    """Each property, shown failing on a planted instance of what it forbids."""

    def setUp(self):
        self.patterns = retired_patterns(era_minor())

    def test_an_import_is_caught(self):
        for src in (f"from {OLD_PACKAGE}.spatial.core import GeoPose\n",
                    f"import {OLD_PACKAGE}.builtin\n"):
            self.assertTrue(old_imports("planted.py", src), src)

    def test_the_era_package_is_not(self):
        self.assertFalse(old_imports(
            "ok.py", f"from {OLD_PACKAGE}18.spatial.core import GeoPose\n"))

    def test_a_string_import_is_caught_and_a_docstring_is_not(self):
        src = (f'"""Was built from {OLD_PACKAGE} and idl/v1.7."""\n'
               f'mod = __import__("{OLD_PACKAGE}.spatial.core")\n')
        hits = live_python("planted.py", src, self.patterns)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn(":2:", hits[0])

    def test_a_copy_line_is_caught_and_a_comment_is_not(self):
        text = (f"# the {OLD_PACKAGE} tree went on 2026-10-21\n"
                f"COPY {OLD_PACKAGE} ./{OLD_PACKAGE}\n")
        hits = live_lines("Dockerfile", text, self.patterns)
        self.assertEqual([h.split(":")[1] for h in hits], ["2"])

    def test_a_retired_bundle_path_is_caught(self):
        for line in ("idlc -I idl/v1.7 x.idl", "cp manifests/v1.6/a.json .",
                     "ls idl/demo"):
            self.assertTrue(live_lines("x.sh", line, self.patterns), line)
        for line in ("idlc -I idl/v1.8 x.idl", "ls idl/demo18"):
            self.assertFalse(live_lines("x.sh", line, self.patterns), line)

    def test_a_link_is_caught_and_a_mention_is_not(self):
        text = "Was in `idl/v1.7`. See [the tree](idl/v1.7/core.idl).\n"
        self.assertEqual(len(live_links("x.md", text, self.patterns)), 1)

    def test_a_link_quoted_as_code_is_text(self):
        # Found by the gate on its own census entry, which quotes the link it
        # planted: rendered, that is a code span, not a link.
        text = ("planted `[the old IDL](idl/v1.7/core.idl)` here\n"
                "```\n[x](idl/v1.6/a.idl)\n```\n"
                "but [this](manifests/v1.4/a.json) is one\n")
        hits = live_links("x.md", text, self.patterns)
        self.assertEqual(hits, ["x.md: links to manifests/v1.4/a.json"])

    def test_typescript_comments_are_history(self):
        text = ("// read idl/v1.7 until October\n"
                "/* and idl/v1.6\n   before that */\n"
                "const p = 'idl/v1.7/core.idl';\n")
        hits = live_lines("x.ts", text, self.patterns)
        self.assertEqual([h.split(":")[1] for h in hits], ["4"])


if __name__ == "__main__":
    unittest.main()
