#!/usr/bin/env python3
"""
Every repository path the docs point at exists, pictures included.

Until this file, dead links were found by ad-hoc scans run when someone
remembered to. Those scans read Markdown link syntax and nothing else, so the
README's `<picture>` -- whose `srcset` picks the variant a dark-mode reader
sees -- and the text inside committed diagrams were outside every check:
rename an SVG and the dark variant silently falls back to nothing; move a
directory a diagram names and the diagram goes on naming it.

Four things are read, in every tracked Markdown and SVG file outside the
frozen exhibit:

1. **Markdown link and image targets**, outside code fences and spans: the
   file must exist (relative to the document), and a `#fragment` into a
   Markdown file must be one of its headings as GitHub slugs them.
2. **HTML `src`, `srcset` and `href` attributes** in Markdown: the same.
3. **Alt text** (`alt="..."` and `![...]`): any repository path it names.
4. **SVG text and attributes**, outside XML comments: any repository path.

"A repository path" in 3 and 4 is a slash-separated token whose first segment
is a tracked top-level entry (`docs/`, `bridges/`, ...), so `/v1/localize` and
`spatial.owm/0.1` are not mistaken for one.
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXHIBIT = "tests/cross_era/"

FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
CODE_SPAN = re.compile(r"(`+)(?!`).+?(?<!`)\1", re.S)
MD_LINK = re.compile(r"!?\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_ATTR = re.compile(r"\b(src|href|srcset)\s*=\s*\"([^\"]*)\"", re.I)
ALT_ATTR = re.compile(r"\balt\s*=\s*\"([^\"]*)\"", re.I)
XML_COMMENT = re.compile(r"<!--.*?-->", re.S)
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.I)


def tracked() -> list:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"],
                         cwd=REPO, capture_output=True, text=True,
                         check=True).stdout.split("\n")
    return sorted(f for f in out
                  if f and not f.startswith((EXHIBIT, "directions/"))
                  and (REPO / f).is_file())


def top_level(files) -> set:
    return {f.split("/", 1)[0] for f in files if "/" in f}


def repo_paths(text: str, tops: set) -> list:
    """Slash-separated tokens whose first segment is a top-level entry."""
    out = []
    for m in re.finditer(r"(?<![\w./:@-])((?:[\w.-]+/)+[\w.-]*[\w])", text):
        token = m.group(1)
        if token.split("/", 1)[0] in tops:
            out.append(token)
    return out


def slug(heading: str) -> str:
    """GitHub's heading anchor: link text kept, punctuation dropped."""
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    h = re.sub(r"<[^>]+>", "", h).strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors(md: Path) -> set:
    text = FENCE.sub("", md.read_text(errors="ignore"))
    seen: dict = {}
    out = set()
    for h in re.findall(r"^#{1,6}\s+(.*?)\s*#*\s*$", text, re.M):
        base = slug(h)
        n = seen.get(base, 0)
        out.add(base if n == 0 else f"{base}-{n}")
        seen[base] = n + 1
    out.update(re.findall(r"<a\s+(?:id|name)=\"([^\"]+)\"", text))
    return out


def check_target(doc: str, target: str) -> str | None:
    """None if the target resolves; otherwise what is wrong with it."""
    if not target or EXTERNAL.match(target):
        return None
    path, _, frag = target.partition("#")
    path = path.split("?", 1)[0]
    base = (REPO / doc).parent
    dest = (base / path).resolve() if path else (REPO / doc)
    if not dest.exists():
        return f"{doc}: {target} does not exist"
    if frag and dest.suffix == ".md" and frag not in anchors(dest):
        return f"{doc}: {target} has no heading #{frag}"
    return None


def check_named_path(doc: str, token: str) -> str | None:
    for root in (REPO, (REPO / doc).parent):
        if (root / token.rstrip("/")).exists():
            return None
    return f"{doc}: names {token}, which does not exist"


def scan_markdown(doc: str, text: str, tops: set) -> list:
    text = CODE_SPAN.sub("", FENCE.sub("", text))
    problems = []
    for m in MD_LINK.finditer(text):
        problems.append(check_target(doc, m.group(2)))
        if m.group(0).startswith("!"):
            problems += [check_named_path(doc, t) for t in repo_paths(m.group(1), tops)]
    for attr, value in HTML_ATTR.findall(text):
        urls = ([c.strip().split()[0] for c in value.split(",") if c.strip()]
                if attr.lower() == "srcset" else [value])
        problems += [check_target(doc, u) for u in urls]
    for alt in ALT_ATTR.findall(text):
        problems += [check_named_path(doc, t) for t in repo_paths(alt, tops)]
    return [p for p in problems if p]


def scan_svg(doc: str, text: str, tops: set) -> list:
    text = XML_COMMENT.sub("", text)
    problems = [check_named_path(doc, t) for t in repo_paths(text, tops)]
    return [p for p in problems if p]


def scan(files) -> list:
    tops = top_level(files)
    problems = []
    for f in files:
        if f.endswith(".md"):
            problems += scan_markdown(f, (REPO / f).read_text(errors="ignore"), tops)
        elif f.endswith(".svg"):
            problems += scan_svg(f, (REPO / f).read_text(errors="ignore"), tops)
    return problems


class EveryDocPathResolves(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = tracked()

    def test_the_scan_saw_the_docs(self):
        """A gate that reads nothing passes everything."""
        self.assertIn("README.md", self.files)
        self.assertIn("docs/images/ecosystem-dark.svg", self.files)
        self.assertGreater(sum(f.endswith(".md") for f in self.files), 30)

    def test_the_readme_picture_is_read(self):
        text = (REPO / "README.md").read_text()
        targets = [v for a, v in HTML_ATTR.findall(text) if a == "srcset"]
        self.assertIn("docs/images/ecosystem-dark.svg", targets)

    def test_nothing_dead(self):
        problems = scan(self.files)
        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))


class TheGateCanFail(unittest.TestCase):
    """Each reading, shown failing on a planted instance."""

    def setUp(self):
        self.tops = top_level(tracked())

    def test_a_dead_srcset_is_caught(self):
        text = '<picture><source srcset="docs/images/nope-dark.svg"></picture>\n'
        self.assertEqual(len(scan_markdown("README.md", text, self.tops)), 1)

    def test_a_dead_anchor_is_caught_and_a_live_one_is_not(self):
        text = ("[a](docs/ismar-openvps.md#a--local-with-the-bundled-localizer) "
                "[b](docs/ismar-openvps.md#no-such-heading)\n")
        hits = scan_markdown("README.md", text, self.tops)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("no-such-heading", hits[0])

    def test_a_path_in_alt_text_is_checked(self):
        text = '<img alt="from bridges/no_such_bridge" src="docs/images/ecosystem-light.svg">\n'
        self.assertEqual(len(scan_markdown("README.md", text, self.tops)), 1)

    def test_an_svg_label_is_checked_and_its_comment_is_not(self):
        text = ('<svg><!-- once bridges/old_bridge -->'
                '<text>bridges/gone_bridge</text><text>/v1/localize</text>'
                '<text>bridges/web_bridge</text></svg>')
        hits = scan_svg("docs/images/x.svg", text, self.tops)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("gone_bridge", hits[0])

    def test_code_is_text(self):
        text = "`[x](docs/nope.md)`\n```\n![y](docs/nope.png)\n```\n"
        self.assertEqual(scan_markdown("README.md", text, self.tops), [])


if __name__ == "__main__":
    unittest.main()
