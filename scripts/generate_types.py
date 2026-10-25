#!/usr/bin/env python3
"""
Generate Python bindings for the bundled SpatialDDS IDL.

Runs ``idlc -l py`` over the vendored spec bundle (``idl/v1.8/``) and, for
the demo's own package, over ``idl/demo18/`` (demo-owned structs for flows
the spec has no type for), applies the fixes idlc's Python backend needs, and
writes an importable package.

Spec provenance
---------------

``idl/v1.8/`` is vendored verbatim from **SpatialDDS-spec commit
``9c3e1f2``** (the 1.8 revision that added ``TopicMeta.durability``). Resync
with::

    git -C ../SpatialDDS-spec archive 9c3e1f2 idl/v1.8 | tar -x -C .

The 1.8 pin is a **commit, not a tag**, which breaks this repository's own
convention and does so deliberately: the spec carries no ``v1.8`` tag, and
``9c3e1f2`` is the commit the validating toolchain was generated at --
``spatialdds-web``'s vendored artifacts pin the same one. A demo whose types
came from a different commit than the gateway that validates them would be
comparing two type systems and calling it conformance.

Generated into ``spatialdds_idl18/``, spec types only -- the default::

    python3 scripts/generate_types.py

The demo-owned types for 1.8 (``idl/demo18``) are generated into their own
package, ``spatialdds_demo_idl18``, which binds to ``spatialdds_idl18`` for
every spec type instead of carrying a copy::

    python3 scripts/generate_types.py --spec-idl idl/v1.8 \
        --demo-idl idl/demo18 --bind-to spatialdds_idl18 \
        --out spatialdds_demo_idl18

They cannot go inside ``spatialdds_idl18``: that tree must stay
byte-identical to the copy ``spatialdds_ros`` vendors, which
``tests/test_idl_trees_agree.py`` enforces file by file. ``--bind-to`` also
checks that the spec half it compiled matches the checked-in tree before
keeping anything, so the demo types cannot be bound to different spec bytes
than the ones they meet at import time.

**Do not track the spec repo's ``main``.** Pin a commit or a tag, and move
it only under a deliberate migration brief, the way this demo moved to each
revision. Main carries unswept draft identifiers; resyncing from it would mix
them in silently, and the drift gate would pass because the generated output
would match whatever was vendored.

**Earlier eras.** ``idl/v1.4``, ``idl/v1.6`` and ``idl/v1.7`` were vendored
here, and ``idl/v1.7`` with ``idl/demo`` generated ``spatialdds_idl/``. All
were retired on 2026-10-21 once nothing imported them; git history is the
archive. The one deliberate exception is the frozen cross-era exhibit,
``tests/cross_era/``, which carries the subset of ``spatialdds_idl`` its 1.7
probe needs and is not regenerated -- see its README.

The generated tree is checked in, so day-to-day work needs neither idlc nor
Docker. ``--check`` re-runs the generator into a temp directory and diffs, so
stale output is a test failure rather than a surprise.

    python3 scripts/generate_types.py            # regenerate in place
    python3 scripts/generate_types.py --check    # fail if checked-in output is stale

idlc is not usually installed on a host; run it in the demo image:

    docker run --rm -v "$PWD:/repo" -w /repo cyclonedds-python \\
        python3 scripts/generate_types.py

Post-processing (all of it here, never by hand-editing generated files):

1. **Bare union discriminators.** idlc emits ``discriminator=CovarianceType``
   as a bare name even when the enum lives in another module, so importing
   ``spatial.core`` dies with ``NameError: name 'CovarianceType' is not
   defined``. Other cross-module references are emitted as quoted forward refs
   and resolve lazily; the discriminator and its ``types.case[[...]]`` labels
   are evaluated when the class is created. We add the missing import.

2. **Package nesting.** idlc emits top-level ``spatial/`` and ``builtin/``
   packages. We nest them under one package so the repo exposes a single
   importable name, rewriting ``import spatial`` to bind the same local name.
   Quoted forward refs ("spatial.common.Mat3x3") resolve against module
   globals, so they keep working; ``typename="spatial.core.CovMatrix"`` is the
   DDS type identity and is deliberately left alone.
"""

from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The bundle and the demo IDL are module-level because every helper below reads
# them, and `--spec-idl` / `--demo-idl` rebind them before anything runs. The
# demo IDL is compiled only with `--bind-to`: a spec tree is spec types only.
SPEC_IDL = REPO / "idl" / "v1.8"
# Where `SPEC_IDL` came from, as the resync command above spells it. Constants
# so a gate can read the pin rather than retype it: `tests/test_topic_registry.py`
# reads the spec's topic registry at this same commit from this same checkout.
SPEC_COMMIT = "9c3e1f2"
SPEC_CHECKOUT = REPO.parent / "SpatialDDS-spec"
DEMO_IDL = REPO / "idl" / "demo18"
DEFAULT_OUT = REPO / "spatialdds_idl18"

# Top-level packages idlc emits are discovered from its output rather than
# hardcoded, so adding an IDL module (e.g. to idl/demo18/) needs no change here.

HEADER = """# Generated by scripts/generate_types.py — do not edit.
# Regenerate with:  docker run --rm -v "$PWD:/repo" -w /repo cyclonedds-python \\
#                       python3 scripts/generate_types.py
"""


def find_idlc() -> str | None:
    idlc = os.environ.get("IDLC_PATH") or shutil.which("idlc")
    if idlc:
        return idlc
    for candidate in ("/usr/local/bin/idlc", "/opt/homebrew/bin/idlc"):
        if os.path.exists(candidate):
            return candidate
    return None


# Appendix E provisional profiles. They now live in `provisional/` rather
# than `examples/`, which is the distinction that matters: `examples/` holds
# informative design sketches (agent, neural), `provisional/` holds profiles
# with registered 3.3.2 type names that a deployment can build against.
# Globbed rather than named, so a new provisional profile is picked up.
PROVISIONAL_DIR = "provisional"


def idl_files() -> list[Path]:
    files = sorted(SPEC_IDL.glob("*.idl"))
    provisional = SPEC_IDL / PROVISIONAL_DIR
    if provisional.is_dir():
        files += sorted(provisional.glob("*.idl"))
    if DEMO_IDL.is_dir():
        files += sorted(DEMO_IDL.glob("*.idl"))
    if not files:
        raise SystemExit(f"no IDL found under {SPEC_IDL} or {DEMO_IDL}")
    return files


def run_idlc(idlc: str, out_dir: Path) -> None:
    """idlc's Python backend ignores -o and writes into cwd, so we chdir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    includes = ["-I", str(SPEC_IDL), "-I", str(SPEC_IDL / PROVISIONAL_DIR)]
    if DEMO_IDL.is_dir():
        includes += ["-I", str(DEMO_IDL)]
    for path in idl_files():
        result = subprocess.run(
            [idlc, "-l", "py", *includes, str(path)],
            cwd=out_dir, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"idlc failed on {path.name}:\n{result.stdout}\n{result.stderr}"
            )


def _defined_names(source: str) -> set[str]:
    return set(re.findall(r"^class (\w+)", source, re.M))


def _index_definitions(root: Path, package: str) -> dict[str, str]:
    """Map each generated class name to the importable module that defines it."""
    index: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        module = ".".join((package, *parts))
        for name in _defined_names(path.read_text()):
            index.setdefault(name, module)
    return index


def fix_union_discriminators(root: Path, package: str) -> list[str]:
    """
    Import union discriminators that idlc emitted as unresolvable bare names.

    Runs after nesting, so the imports it writes use the final package path.
    """
    index = _index_definitions(root, package)
    fixed: list[str] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text()
        names = set(re.findall(r"discriminator=(\w+)", source))
        missing = [n for n in names if n not in _defined_names(source)]
        if not missing:
            continue
        imports = []
        for name in sorted(missing):
            module = index.get(name)
            if module is None:
                raise SystemExit(
                    f"{path}: union discriminator {name!r} is undefined and no "
                    "generated module defines it — cannot patch, report instead"
                )
            imports.append(f"from {module} import {name}")
            fixed.append(f"{path.relative_to(root)}: {name} <- {module}")
        # Place the imports after the generated import block, before first use.
        anchor = "import cyclonedds.idl.types as types\n"
        if anchor not in source:
            raise SystemExit(f"{path}: unexpected generated layout, cannot patch")
        source = source.replace(
            anchor,
            anchor + "\n# generate_types.py: idlc emits this discriminator as a bare name\n"
            + "\n".join(imports) + "\n",
            1,
        )
        path.write_text(source)
    return fixed


def bind_root_package(root: Path, package: str) -> list[str]:
    """Bind the root package name every dotted annotation already uses.

    idlc annotates a nested member with a fully-qualified *string* --- e.g.
    ``cov_pos: 'spatialdds_idl18.spatial.common.Mat3x3'`` --- while importing
    only ``from spatialdds_idl18 import spatial``. So the root name in those
    annotations is unbound, and ``typing.get_type_hints()`` on any generated
    dataclass raises ``NameError``. CycloneDDS never calls it, which is why
    every demo worked and the defect sat in both trees since the 1.7 cutover
    --- but ``get_type_hints`` is how dataclass-based tooling introspects a
    type, so schema generators, validators and API frameworks could not read
    these bindings at all.

    Reported as entry 3 of ``docs/owm-1.8-spec-queue.md`` on 2026-10-17 and
    fixed here, which is the only place it can be fixed: the annotations are
    idlc's output and the import block is this script's to patch.

    One import per file, and only where such an annotation actually appears,
    so a file without one is left byte-identical and the diff is exactly the
    set of files that carried the defect.
    """
    bound: list[str] = []
    marker = f"'{package}."
    for path in sorted(root.rglob("*.py")):
        source = path.read_text()
        if marker not in source:
            continue
        if re.search(rf"^import {re.escape(package)}$", source, re.M):
            continue
        anchor = re.search(rf"^from {re.escape(package)} import .*$",
                           source, re.M)
        if anchor is None:
            raise SystemExit(
                f"{path}: carries {marker}… annotations and has no "
                f"`from {package} import …` line to anchor the root import to "
                f"— the generated layout changed, so report rather than guess")
        source = (source[:anchor.start()]
                  + "# generate_types.py: bind the root name the dotted\n"
                  + "# annotations below already use, so get_type_hints works\n"
                  + f"import {package}\n"
                  + source[anchor.start():])
        path.write_text(source)
        bound.append(str(path.relative_to(root)))
    if bound:
        print(f"  bound the root package in {len(bound)} file(s), so "
              f"get_type_hints resolves their annotations")
    return bound


def fix_python_keyword_fields(root: Path) -> dict[str, dict[str, str]]:
    """
    Rename fields whose IDL name is a Python keyword, preserving the wire name.

    The spec legitimately uses ``global`` (CoverageElement, sensing common) and
    ``from`` (Transform, ValidityWindow) as field names.

    idlc >= 11 handles this itself, emitting ``_global`` / ``_from``; we only
    record the mapping. idlc 0.10.5 emits them verbatim, so the module is a
    SyntaxError and cannot be imported at all — and that version also lacks
    ``annotate.member_name``, so it cannot be repaired faithfully. See the
    toolchain note in the module docstring.

    The fix is the escape cyclonedds provides: rename the Python attribute to
    ``<name>_`` and restore the declared name with
    ``@annotate.member_name("<name>_", "<name>")``. Wire identity is preserved —
    these structs carry ``@annotate.autoid("sequential")``, so member IDs come
    from declaration order rather than from names, and member_name puts the
    spec's name back in the type metadata.

    Returns {typename: {python_name: wire_name}} so JSON-facing code can emit
    spec field names.
    """
    import keyword

    aliases: dict[str, dict[str, str]] = {}
    field_re = re.compile(r"^(    )(\w+)(\s*:\s.*)$")
    class_re = re.compile(r'^class (\w+)\(.*typename="([^"]+)"')

    for path in sorted(root.rglob("*.py")):
        original = path.read_text()
        out: list[str] = []
        typename: str | None = None
        renamed: dict[str, str] = {}

        for line in original.splitlines():
            m = class_re.match(line)
            if m:
                typename = m.group(2)
                out.append(line)
                continue
            f = field_re.match(line)
            if f and typename and f.group(2).startswith("_") and keyword.iskeyword(f.group(2)[1:]):
                # idlc >= 11 already dodges Python keywords by prefixing an
                # underscore. Nothing to rewrite; just record the mapping so
                # JSON-facing code can emit the spec's field name.
                aliases.setdefault(typename, {})[f.group(2)] = f.group(2)[1:]
                out.append(line)
                continue
            if f and keyword.iskeyword(f.group(2)) and typename:
                name = f.group(2)
                out.append(f"{f.group(1)}{name}_{f.group(3)}")
                # cyclonedds applies field annotations inside the class body,
                # immediately after the field they describe.
                out.append(
                    f'{f.group(1)}annotate.member_name("{name}_", "{name}")'
                )
                renamed[f"{name}_"] = name
                aliases.setdefault(typename, {})[f"{name}_"] = name
                continue
            out.append(line)

        new_source = "\n".join(out) + "\n"
        if new_source != original:
            path.write_text(new_source)
    return aliases


def write_field_aliases(out_dir: Path, aliases: dict[str, dict[str, str]]) -> None:
    """Record the keyword renames so JSON-facing code can use spec names."""
    lines = [
        HEADER,
        '"""Python attribute -> IDL field name, for fields renamed to dodge',
        'Python keywords. See fix_python_keyword_fields in the generator."""',
        "",
        "FIELD_ALIASES = {",
    ]
    for typename in sorted(aliases):
        lines.append(f'    "{typename}": {{')
        for py, wire in sorted(aliases[typename].items()):
            lines.append(f'        "{py}": "{wire}",')
        lines.append("    },")
    lines += ["}", "", "",
              "def wire_name(typename: str, attr: str) -> str:",
              '    """The IDL field name for a generated attribute."""',
              "    return FIELD_ALIASES.get(typename, {}).get(attr, attr)", ""]
    (out_dir / "_field_aliases.py").write_text("\n".join(lines))


def nest_packages(staging: Path, out_dir: Path, package: str) -> None:
    """Move idlc's top-level packages under one package and fix their imports."""
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = sorted(
        d.name for d in staging.iterdir()
        if d.is_dir() and (d / "__init__.py").exists()
    )
    if not roots:
        raise SystemExit(f"idlc produced no packages in {staging}")

    for root in roots:
        shutil.move(str(staging / root), str(out_dir / root))

    import_re = re.compile(r"^import (%s)$" % "|".join(roots), re.M)
    # Quoted forward refs ('spatial.common.Mat3x3'). cyclonedds resolves these
    # with import_module() on the absolute path, NOT against module globals, so
    # nesting means they have to be rewritten too. Single quotes distinguish
    # them from typename="spatial.core.CovMatrix", which is the DDS type
    # identity and must survive untouched.
    ref_re = re.compile(r"'(%s)\." % "|".join(roots))
    for path in sorted(out_dir.rglob("*.py")):
        source = path.read_text()
        patched = import_re.sub(rf"from {package} import \1", source)
        patched = ref_re.sub(rf"'{package}.\1.", patched)
        if patched != source:
            path.write_text(patched)

    (out_dir / "__init__.py").write_text(
        HEADER
        + '"""Generated SpatialDDS 1.7 types. See scripts/generate_types.py."""\n\n'
        + "".join(f"from . import {r}\n" for r in roots)
        + "\n__all__ = [%s]\n" % ", ".join(f'"{r}"' for r in roots)
    )


def generate(out_dir: Path, package: str) -> list[str]:
    idlc = find_idlc()
    if not idlc:
        raise SystemExit(
            "idlc not found. Run inside the demo image:\n"
            '  docker run --rm -v "$PWD:/repo" -w /repo cyclonedds-python '
            "python3 scripts/generate_types.py"
        )
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "staging"
        run_idlc(idlc, staging)
        if out_dir.exists():
            shutil.rmtree(out_dir)
        # Nest first: the discriminator imports must name the final package.
        nest_packages(staging, out_dir, package)
        fixed = fix_union_discriminators(out_dir, package)
        # Bind the root name the annotations already use, so
        # `typing.get_type_hints()` resolves them. Entry 3 of the spec queue.
        bind_root_package(out_dir, package)
        aliases = fix_python_keyword_fields(out_dir)
        write_field_aliases(out_dir, aliases)
        for typename in sorted(aliases):
            for py, wire in sorted(aliases[typename].items()):
                fixed.append(f"{typename}: {wire} -> {py} (+member_name)")
    return fixed


def generate_bound(out_dir: Path, package: str, bind_to: str) -> list[str]:
    """Demo modules only, binding every spec type to an existing tree.

    Compiles spec + demo IDL as `bind_to` in staging -- so every dotted
    annotation already names `bind_to` -- then keeps only the modules the
    demo IDL added, and points their own-package imports at `package`.

    **The spec half is checked, not trusted.** Before anything is kept, the
    staged spec subtree has to be byte-identical to the checked-in `bind_to`
    tree. Otherwise the demo types would be bound to spec classes that were
    compiled from different bytes than the ones they will meet at import
    time, which is two type systems presenting as one.
    """
    installed = "." in bind_to
    if installed:
        # An installed package (`spatialdds.dds.types`, the SDK's): there is
        # no checked-in tree to diff, and its generator is not this one, so
        # the spec half is checked by its source instead. The IDL compiled
        # here has to be byte for byte the IDL that package's distribution
        # ships, which is the invariant the tree diff stood in for.
        import importlib
        reference = Path(importlib.import_module(bind_to).__file__).parent
        drift = _idl_diff(SPEC_IDL, sdk_idl_dir())
        if drift:
            raise SystemExit(
                f"the spec IDL compiled here is not the IDL {bind_to} was "
                f"generated from; refusing to bind demo types to it:\n  "
                + "\n  ".join(drift))
    else:
        reference = REPO / bind_to
        if not reference.is_dir():
            raise SystemExit(f"--bind-to {bind_to}: no checked-in tree at "
                             f"{reference}")
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / bind_to
        fixed = generate(staged, bind_to)
        spec_roots = sorted(p.name for p in reference.iterdir()
                            if p.is_dir() and p.name != "__pycache__")
        drift = []
        if not installed:
            for root in spec_roots:
                drift += _tree_diff(reference / root, staged / root)
        if drift:
            raise SystemExit(
                f"the spec half compiled here differs from the checked-in "
                f"{bind_to}; refusing to bind demo types to it:\n  "
                + "\n  ".join(drift))
        demo_roots = sorted(p.name for p in staged.iterdir()
                            if p.is_dir() and p.name not in spec_roots
                            and p.name != "__pycache__")
        if not demo_roots:
            raise SystemExit(f"no demo modules under {DEMO_IDL}")
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        own = re.compile(r"^from %s import (%s)$"
                         % (re.escape(bind_to), "|".join(demo_roots)), re.M)
        for root in demo_roots:
            shutil.copytree(staged / root, out_dir / root,
                            ignore=shutil.ignore_patterns("__pycache__"))
            for path in sorted((out_dir / root).rglob("*.py")):
                text = own.sub(rf"from {package} import \1", path.read_text())
                # The demo module's references to its *own* types still name
                # `bind_to` -- `'spatialdds_idl18.oarc_demo.OperatorTrackCount'`
                # -- a path that stops existing the moment the module is lifted
                # out. CycloneDDS never evaluates these, so nothing on the wire
                # notices; `typing.get_type_hints` raises AttributeError. The
                # root-binding class this generator already fixes once, met
                # again from the other side.
                text = text.replace(f"'{bind_to}.{root}.", f"'{package}.{root}.")
                # ...and the root they now name has to be bound for them to
                # resolve, exactly as bind_root_package does for the spec tree.
                text = text.replace(f"import {bind_to}\n",
                                    f"import {bind_to}\nimport {package}\n", 1)
                path.write_text(text)
        # The demo modules' own keyword renames, so a JSON mapper can find
        # them without importing the spec tree's alias table for them.
        aliases = {}
        alias_src = (staged / "_field_aliases.py").read_text()
        namespace: dict = {}
        exec(compile(alias_src, "_field_aliases.py", "exec"), namespace)
        for typename, fields in namespace["FIELD_ALIASES"].items():
            if typename.split(".")[0] in demo_roots:
                aliases[typename] = fields
        write_field_aliases(out_dir, aliases)
        (out_dir / "__init__.py").write_text(
            HEADER
            + f'"""Demo-owned types, compiled against {spec_label()} '
            f'and bound to `{bind_to}` for every spec type. See '
            f'scripts/generate_types.py."""\n\n'
            f"import {bind_to}  # noqa: F401  -- the root the annotations name\n"
            + "".join(f"from . import {r}\n" for r in demo_roots)
            + "\n__all__ = [%s]\n" % ", ".join(f'"{r}"' for r in demo_roots))
    return fixed


def sdk_idl_dir() -> Path:
    """The spec IDL the installed `spatialdds` ships, and its types came from."""
    from spatialdds.server import pin
    return Path(pin.IDL_DIR)


def spec_label() -> str:
    """Where SPEC_IDL is, said the same way on every machine."""
    try:
        return str(SPEC_IDL.relative_to(REPO))
    except ValueError:
        import spatialdds
        return f"the IDL spatialdds {spatialdds.__version__} ships"


def _idl_diff(a: Path, b: Path) -> list[str]:
    """Every .idl file that differs between two bundles, by relative path."""
    def files(root: Path) -> dict:
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*.idl")}
    fa, fb = files(a), files(b)
    return ([f"only in {a}: {k}" for k in sorted(set(fa) - set(fb))]
            + [f"only in {b}: {k}" for k in sorted(set(fb) - set(fa))]
            + [f"differs: {k}" for k in sorted(set(fa) & set(fb))
               if fa[k] != fb[k]])


def main() -> int:
    global SPEC_IDL, DEMO_IDL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--spec-idl", type=Path, default=SPEC_IDL,
                        help="the vendored spec bundle to compile "
                             "(default idl/v1.8)")
    parser.add_argument("--demo-idl", type=Path, default=DEMO_IDL,
                        help="the demo-owned IDL to compile with --bind-to "
                             "(default idl/demo18)")
    parser.add_argument("--bind-to", metavar="PACKAGE",
                        help="write only the demo modules, binding every spec "
                             "type to this checked-in tree instead of "
                             "carrying a copy. How spatialdds_demo_idl18 is "
                             "made, so spatialdds_idl18 stays byte-identical "
                             "to the copy spatialdds_ros vendors")
    parser.add_argument("--check", action="store_true",
                        help="fail if the checked-in output differs from a fresh run")
    args = parser.parse_args()
    if args.bind_to and "." in args.bind_to and args.spec_idl == SPEC_IDL:
        # Bound to the SDK: its own IDL, unless told otherwise (and then
        # checked against its own IDL all the same).
        args.spec_idl = sdk_idl_dir()
    # Both paths are accepted relative to the repository root, so the
    # command lines in the docstring work from anywhere inside it.
    args.out = args.out if args.out.is_absolute() else (REPO / args.out)
    package = args.out.name
    SPEC_IDL = args.spec_idl if args.spec_idl.is_absolute() \
        else (REPO / args.spec_idl)
    if not SPEC_IDL.is_dir():
        raise SystemExit(f"no spec bundle at {SPEC_IDL}")
    DEMO_IDL = args.demo_idl if args.demo_idl.is_absolute() \
        else (REPO / args.demo_idl)
    if not args.bind_to:
        DEMO_IDL = SPEC_IDL / "__no_demo__"   # a path that does not exist
    print(f"bundle   {spec_label()}"
          f"{'  (spec only)' if not args.bind_to else ''}")

    make = (lambda out, pkg: generate_bound(out, pkg, args.bind_to)) \
        if args.bind_to else generate
    if not args.check:
        fixed = make(args.out, package)
        print(f"generated {args.out.relative_to(REPO)}")
        for line in fixed:
            print(f"  patched discriminator: {line}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / package
        make(fresh, package)

        # Comparing output from two different idlc versions says nothing about
        # whether the checked-in tree is stale — they differ in header and
        # import order regardless. Report that distinctly (exit 2) so callers
        # can tell "wrong toolchain" from "needs regenerating".
        checked_in_v = toolchain_version(args.out) if args.out.exists() else None
        fresh_v = toolchain_version(fresh)
        if checked_in_v and fresh_v and checked_in_v != fresh_v:
            print(
                f"toolchain mismatch: checked-in output was generated by idlc "
                f"{checked_in_v}, local idlc is {fresh_v}.\n"
                f"The canonical toolchain is the demo image (cyclonedds "
                f"{checked_in_v}); drift cannot be judged from here."
            )
            return 2

        diff = _tree_diff(args.out, fresh)
        if diff:
            print("Generated types are stale. Regenerate with:")
            print('  docker run --rm -v "$PWD:/repo" -w /repo cyclonedds-python \\')
            print("      python3 scripts/generate_types.py")
            for line in diff:
                print(f"  {line}")
            return 1
    print("generated types are up to date")
    return 0


TOOLCHAIN_RE = re.compile(r"Cyclone DDS IDL version:\s*(\S+)")


def toolchain_version(root: Path) -> str | None:
    """The idlc version recorded in a generated tree's headers."""
    for path in sorted(root.rglob("*.py")):
        m = TOOLCHAIN_RE.search(path.read_text())
        if m:
            return m.group(1)
    return None


def _tree_diff(a: Path, b: Path) -> list[str]:
    if not a.exists():
        return [f"missing: {a}"]
    def sources(root: Path) -> set:
        # Compare generated source only. __pycache__ appears as soon as
        # anything imports the package and says nothing about generator drift;
        # neither does .DS_Store, which macOS writes into any directory a
        # Finder window has looked at. Opening the folder to read a file
        # reported the checked-in types as stale, which is a false alarm of
        # the worst kind: it accuses the one thing in the repo whose whole job
        # is to be trustworthy.
        return {
            p.relative_to(root) for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
            and p.suffix != ".pyc" and p.name != ".DS_Store"
        }

    out: list[str] = []
    a_files = sources(a)
    b_files = sources(b)
    for only in sorted(a_files - b_files):
        out.append(f"unexpected file: {only}")
    for only in sorted(b_files - a_files):
        out.append(f"missing file: {only}")
    for shared in sorted(a_files & b_files):
        if not filecmp.cmp(a / shared, b / shared, shallow=False):
            out.append(f"differs: {shared}")
    return out


if __name__ == "__main__":
    sys.exit(main())
