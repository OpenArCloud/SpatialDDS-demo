#!/usr/bin/env python3
"""
The cross-era exhibit is frozen, and its 1.7 types are exactly what it needs.

`tests/cross_era/` is the one place a second era is kept on purpose: a 1.7
participant, built from nothing but 1.7 generated types, the spec's topic
names and the §3.3.3 profile table, driven against this 1.8 demo. See its
README for the claim.

Three properties, each checked here rather than trusted:

1. **Frozen.** Every file in the directory is listed in `MANIFEST.sha256`
   with its digest, nothing is unlisted, and the manifest's own digest is
   pinned below -- so changing the exhibit takes two deliberate edits, one of
   them outside it.
2. **Closed.** Every 1.7 type the probe names, and every type those types
   reference, is defined in the subset. A missing module would surface as an
   ImportError on a lane that may not run; this finds it statically.
3. **Minimal.** Nothing in the subset is unreachable from the probe. The
   ruling was "the import closure of the types it exercises, computed from its
   imports rather than hand-picked" -- a subset with extra modules is a
   hand-picked one that happens to be generous.

The derivation is `type_closure()`, a static walk: the probe's
`spatialdds_idl` imports (including the two provisional modules it imports by
string), then every `spatialdds_idl.<module>.<Type>` an annotation names and
every import a generated module makes. Package `__init__` files are not
followed, because idlc's eagerly import every sibling and would make the
closure the whole tree by construction. `build()` copied each reached module
byte for byte from the 1.7 tree before it was retired, and wrote each
`__init__` filtered to the children that were kept -- the only bytes in the
subset that differ from what the generator produced.

    python3 tests/test_cross_era_fixture.py --build <a 1.7 spatialdds_idl tree>
"""
from __future__ import annotations

import ast
import hashlib
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "cross_era"
PROBE = FIXTURE / "interop_probe.py"
#: The one directory in the exhibit that is a package. Its name is the 1.7
#: package's, and this module takes it from there rather than spelling it:
#: tests/test_one_era.py forbids that name in code outside the exhibit, and
#: this file is not exempt.
SUBSET = next(p for p in FIXTURE.iterdir() if (p / "__init__.py").is_file())
MANIFEST = FIXTURE / "MANIFEST.sha256"
PACKAGE = SUBSET.name

#: sha256 of MANIFEST.sha256. Pinned outside the exhibit so that editing a
#: frozen file and re-running the manifest is not, by itself, enough.
MANIFEST_SHA256 = "c7c206d7e78814a4b181108f5ae20343a8f9d57fa908ab22987af028c068de36"

DOTTED = re.compile(r"^%s(\.[A-Za-z_]\w*)+$" % re.escape(PACKAGE))


# --- the derivation ----------------------------------------------------------

def _modules(tree: Path) -> dict:
    """{dotted package: {impl module name: set of names it defines}}."""
    out: dict = {}
    for path in sorted(tree.rglob("_*.py")):
        if path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        package = ".".join((PACKAGE,) + path.parent.relative_to(tree).parts)
        defined = set()
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        out.setdefault(package, {})[f"{package}.{path.stem}"] = defined
    return out


def _resolve(modules: dict, dotted: str):
    """
    Where a dotted reference lands: ``("package", name)`` or ``("impl", name)``.

    A package reference -- `from spatialdds_idl import spatial`, which every
    generated module makes to bind the root its annotations spell -- needs the
    package to exist, not every module in it.
    """
    if dotted in modules or dotted == PACKAGE:
        return ("package", dotted)
    for impls in modules.values():
        if dotted in impls:
            return ("impl", dotted)
    package, _, name = dotted.rpartition(".")
    for impls in modules.values():
        if name in impls.get(package, ()):     # a name in a named module
            return ("impl", package)
    for impl, defined in modules.get(package, {}).items():
        if name in defined:
            return ("impl", impl)
    if f"{package}.{name}" in modules or any(
            p.startswith(f"{package}.{name}.") for p in modules):
        return ("package", f"{package}.{name}")
    raise LookupError(f"{dotted} is not defined in the tree")


def _references(source: str, here: str) -> set:
    """Every spatialdds_idl name a module imports or an annotation spells."""
    refs = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = here.rsplit(".", node.level)[0]
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            if module.split(".")[0] == PACKAGE:
                refs |= {f"{module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            refs |= {a.name for a in node.names
                     if a.name.split(".")[0] == PACKAGE and "." in a.name}
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and DOTTED.match(node.value):
            refs.add(node.value)
        elif isinstance(node, ast.Tuple) and len(node.elts) == 2 \
                and isinstance(node.elts[0], ast.Constant) \
                and isinstance(node.elts[1], ast.Dict) \
                and DOTTED.match(str(node.elts[0].value)):
            # The probe's `__import__(module, fromlist=...)` table.
            refs |= {f"{node.elts[0].value}.{v.value}"
                     for v in node.elts[1].values if isinstance(v, ast.Constant)}
    return refs


def type_closure(tree: Path, probe_source: str) -> tuple:
    """
    (implementation modules, packages) reachable from the probe, dotted.

    Packages are every ancestor of a reached module plus any package a
    reached module or the probe names directly.
    """
    modules = _modules(tree)
    files = {impl: tree.joinpath(*impl.split(".")[1:]).with_suffix(".py")
             for impls in modules.values() for impl in impls}
    reached: set = set()
    packages: set = set()
    pending = set()

    def take(refs):
        for ref in refs:
            kind, name = _resolve(modules, ref)
            (packages if kind == "package" else pending).add(name)

    take(_references(probe_source, "__main__"))
    while pending:
        impl = pending.pop()
        if impl in reached:
            continue
        reached.add(impl)
        take(_references(files[impl].read_text(),
                         impl.rpartition(".")[0] + ".x"))
    for name in reached | set(packages):
        parts = name.split(".")
        for i in range(1, len(parts) if name in reached else len(parts) + 1):
            packages.add(".".join(parts[:i]))
    return reached, packages


def subset_contents(tree: Path) -> tuple:
    """(implementation modules, packages) present in a tree, dotted."""
    impls = {impl for impls in _modules(tree).values() for impl in impls}
    packages = {".".join((PACKAGE,) + p.parent.relative_to(tree).parts)
                for p in tree.rglob("__init__.py")
                if "__pycache__" not in p.parts}
    return impls, packages


# --- construction (run once, against the 1.7 tree) ---------------------------

_FROM_DOT = re.compile(r"^from \.(\w*) import (.+)$")


def _filter_init(text: str, keep_children: set, keep_impls: set) -> str:
    out, dropped = [], set()
    for line in text.splitlines(keepends=True):
        m = _FROM_DOT.match(line.rstrip("\n"))
        if m:
            child, names = m.group(1), m.group(2)
            if child == "" and names.strip() not in keep_children:
                dropped.add(names.strip())
                continue
            if child and child not in keep_impls:
                dropped |= {n.strip() for n in names.split(",")}
                continue
        out.append(line)
    text = "".join(out)
    for name in dropped:
        text = re.sub(r'(?m)^\t"%s",\n' % re.escape(name), "", text)
        text = re.sub(r'"%s", ' % re.escape(name), "", text)
        text = re.sub(r', "%s"' % re.escape(name), "", text)
    return text


def build(source_tree: Path) -> list:
    reached, packages = type_closure(source_tree, PROBE.read_text())
    written = []
    for package in sorted(packages):
        rel = Path(*package.split(".")[1:])
        children = {p.split(".")[-1] for p in packages
                    if p.rpartition(".")[0] == package}
        impls = {i.split(".")[-1] for i in reached
                 if i.rpartition(".")[0] == package}
        dest = SUBSET / rel
        dest.mkdir(parents=True, exist_ok=True)
        init = (source_tree / rel / "__init__.py").read_text()
        (dest / "__init__.py").write_text(_filter_init(init, children, impls))
        written.append(dest / "__init__.py")
        for impl in sorted(impls):
            src = source_tree / rel / f"{impl}.py"
            (dest / f"{impl}.py").write_bytes(src.read_bytes())
            written.append(dest / f"{impl}.py")
    return written


# --- the manifest ------------------------------------------------------------

def fixture_files() -> list:
    return sorted(
        p for p in FIXTURE.rglob("*")
        if p.is_file() and p != MANIFEST and "__pycache__" not in p.parts
        and p.name != ".DS_Store" and p.suffix != ".pyc")


def manifest_text() -> str:
    return "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  "
        f"{p.relative_to(FIXTURE).as_posix()}\n" for p in fixture_files())


# --- the checks --------------------------------------------------------------

class TheExhibitIsFrozen(unittest.TestCase):
    def test_every_file_is_listed_with_its_digest(self):
        self.assertTrue(MANIFEST.exists(), "the exhibit has no manifest")
        self.assertEqual(manifest_text(), MANIFEST.read_text(),
                         "a file in tests/cross_era/ was added, removed or "
                         "changed without the manifest")

    def test_the_manifest_is_the_pinned_one(self):
        self.assertEqual(hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
                         MANIFEST_SHA256,
                         "MANIFEST.sha256 changed; if that was deliberate, "
                         "pin its new digest here")


class TheSubsetIsTheProbesClosure(unittest.TestCase):
    def test_closed_and_minimal(self):
        have = subset_contents(SUBSET)
        need = type_closure(SUBSET, PROBE.read_text())
        for kind, h, n in zip(("modules", "packages"), have, need):
            with self.subTest(kind=kind):
                self.assertEqual(sorted(n - h), [], "referenced but absent")
                self.assertEqual(sorted(h - n), [], "present but unreached")
        self.assertGreater(len(have[0]), 10, "the walk found almost nothing")

    def test_a_dangling_reference_is_caught(self):
        with self.assertRaises(LookupError):
            type_closure(SUBSET, f"from {PACKAGE}.spatial.mapping "
                                 "import MapMeta\n")

    def test_an_unreached_module_is_caught(self):
        need, _ = type_closure(SUBSET, f"from {PACKAGE}.builtin import Time\n")
        self.assertLess(len(need), len(subset_contents(SUBSET)[0]))

    def test_the_stated_counts_are_the_subsets(self):
        # Prose numbers with a generated source are held to it.
        impls, packages = subset_contents(SUBSET)
        counted = {"modules": len(impls), "packages": len(packages)}
        for doc in (FIXTURE / "README.md", REPO / "README.md"):
            with self.subTest(doc=doc.relative_to(REPO).as_posix()):
                text = doc.read_text()
                m = re.search(r"<!-- exhibit-subset: (.*?) -->", text)
                self.assertIsNotNone(m, "the exhibit-subset marker is gone")
                stated = {k: int(v) for k, v in
                          (kv.split("=") for kv in m.group(1).split())}
                self.assertEqual(stated, counted)
                self.assertIn(f"{counted['modules']} of the 1.7 tree's", text)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--build":
        for path in build(Path(sys.argv[2])):
            print(path.relative_to(REPO))
        sys.exit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "--write-manifest":
        MANIFEST.write_text(manifest_text())
        print(hashlib.sha256(MANIFEST.read_bytes()).hexdigest())
        sys.exit(0)
    unittest.main()
