#!/usr/bin/env python3
"""The consumer side of the SDK adoption, counted from the tracked tree.

Every number in docs/sdk-adoption-census.md comes from here. It walks every
git-tracked `.py` file with `ast` and records, per file:

* which `spatialdds_demo` modules and symbols it uses (`from … import`, and
  `module.attr` on an imported module);
* whether it imports `cyclonedds` itself, outside the helpers;
* whether it imports a generated type tree (`spatialdds_idl18`,
  `spatialdds_demo_idl18`) itself.

Usage:
    python3 tools/sdk_adoption_census.py              # the tables, as Markdown
    python3 tools/sdk_adoption_census.py --json       # the raw inventory
    python3 tools/sdk_adoption_census.py --at REV     # ... of the tree at REV
    python3 tools/sdk_adoption_census.py --write-doc  # into the census doc
    python3 tools/sdk_adoption_census.py --check-doc  # fail if it drifted
    python3 tools/sdk_adoption_census.py --shrink     # before, and now
    python3 tools/sdk_adoption_census.py --check-sdk  # needs spatialdds[dds]:
                                                      # every named equivalent
                                                      # exists in the release

The census doc's tables are a ruled record of the tree at the commit the
census was taken at (`CENSUS_AT`), so `--write-doc` and `--check-doc` read
that commit's tree, not the working one. Against the working tree they could
only ever drift, which is how the check first broke: it met `lanes.py`, a
module the census never saw, and raised. `--shrink` measures the package and
the generated trees the same way at that commit and now.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "spatialdds_demo"
TREES = ("spatialdds_idl18", "spatialdds_demo_idl18")
#: Generated trees that are fixtures, not consumers: the cross-era test's own
#: vendored pre-1.8 bindings.
FIXTURE_TREES = ("tests/cross_era/spatialdds_idl/",)
DOC = ROOT / "docs" / "sdk-adoption-census.md"
#: The commit the census was taken at, and its tables describe.
CENSUS_AT = "f4890e8"
BEGIN, END = "<!-- census:tables -->", "<!-- /census:tables -->"
#: The "after" block: the shrink, measured on the working tree.
SHRINK_BEGIN, SHRINK_END = "<!-- census:shrink -->", "<!-- /census:shrink -->"

#: The verdicts, as ruled data. A module's verdict covers every symbol of it a
#: consumer uses, unless SYMBOLS overrides that symbol. Each REPLACED entry
#: names its equivalent as a dotted attribute of the published `spatialdds`
#: package; `--check-sdk` imports each one, so an equivalent that does not
#: exist in the installed release fails the run rather than reaching the doc.
#: `needs` names the SDK filings (docs/sdk-adoption-census.md, "Filings") the
#: replacement waits on; empty means 0.7.0 as published carries it.
MODULES: dict[str, dict] = {
    "absent": {"v": "STAYS", "why": "absent-value constants; 0.7.0 has none (filing S6)"},
    "blob": {"v": "REPLACED", "sdk": "spatialdds.dds.chunk", "needs": [],
             "why": "chunk / Reassembler / blob_ref; the lane is `p.writer(\"blob_chunk\", stream=...)`"},
    "catalog_semantics": {"v": "STAYS", "why": "the venue catalogue's filter rule"},
    "content_catalogue": {"v": "STAYS", "why": "the venue's seed-to-record mapping; its records build through sd.content_announce/content_manifest where they fit (filing S8)"},
    "dds_transport": {"v": "RETIRED", "why": "the envelope transport; moves into benchmarks/ as the baseline arm"},
    "discovery_bus": {"v": "REPLACED", "sdk": "spatialdds.dds.Participant.announce",
                      "needs": ["S1", "S3"],
                      "why": "p.announce() and p.reader(\"Announce\"/\"ContentAnnounce\"); a ContentAnnounce writer with on_exit=\"withdraw\""},
    "discovery_http": {"v": "STAYS", "why": "HTTP search semantics, not bus plumbing"},
    "duck_mover": {"v": "STAYS", "why": "venue logic; its transport moves"},
    "json_mapping": {"v": "RETIRED", "sdk": "spatialdds.dds.to_json", "needs": [],
                     "why": "one N.1 conversion: sd.dds.to_json / from_json"},
    "keepout": {"v": "STAYS", "why": "robot-tier policy"},
    "manifest_resolver": {"v": "STAYS", "why": "the offline bundled resolution the AR flow runs on; sd.connect().resolve needs a live endpoint"},
    "model_service": {"v": "STAYS", "why": "the venue's world model; its transport moves"},
    "payloads": {"v": "STAYS", "why": "fusion-demo payload shapes"},
    "plaza": {"v": "STAYS", "why": "venue constants"},
    "pond_watch": {"v": "STAYS", "why": "venue program; its transport moves"},
    "qos_profiles": {"v": "RETIRED", "sdk": "spatialdds.dds.registry.profiles", "needs": [],
                     "why": "the 19 registered rows are the generated table's, column for column (measured)"},
    "robot_bridge": {"v": "STAYS", "why": "venue program over spatialdds_ros"},
    "service_bus": {"v": "STAYS", "needs": ["S2"],
                    "why": "the request/reply convention holder until 1.9; its lanes move onto SDK writers once explicit topics exist"},
    "stream": {"v": "REPLACED", "sdk": "spatialdds.dds.Participant.reader",
               "needs": ["S1", "S2", "S3", "S4", "S9"],
               "why": "announce-driven multi-topic endpoints, across scenes"},
    "tempo": {"v": "RETIRED", "sdk": "spatialdds.Recency", "needs": [],
              "why": "sd.Recency: the same methods, constants and defaults"},
    "topic_types": {"v": "RETIRED", "sdk": "spatialdds.dds.registry.registrations", "needs": ["S9"],
                    "why": "the registry at the pin; the oarc.* extensions are declared beside their types"},
    "topics": {"v": "RETIRED", "sdk": "spatialdds.dds.topic_name", "needs": [],
               "why": "names built from the registry"},
    "typed_transport": {"v": "RETIRED", "sdk": "spatialdds.dds.Participant.writer",
                        "needs": ["S1", "S4", "S5"],
                        "why": "p.writer() / p.reader()"},
    # After the census: not a verdict it ruled on, so marked as such. The
    # deployment's own part of the lanes, which the migration consolidated.
    "lanes": {"v": "NEW", "why": "this deployment's own part of the lanes (added after the census)"},
}
SYMBOLS: dict[str, dict] = {
    "dds_transport.require_dds_env": {"v": "RETIRED", "sdk": "spatialdds.dds.DOMAIN_ENV", "needs": [],
                                      "why": "domain= or SPATIALDDS_DDS_DOMAIN; no silent 0"},
    "discovery_bus.validate_announce": {"v": "RETIRED", "sdk": "spatialdds.dds.validate", "needs": [],
                                        "why": "schema validation on write"},
    "discovery_bus._stale": {"v": "RETIRED", "why": "private; dies with the subscriber"},
    "json_mapping.FIELD_ALIASES": {"v": "RETIRED", "why": "the demo dialect's alias table; N.1 has none"},
    "json_mapping._coerce": {"v": "RETIRED", "why": "private to the demo dialect"},
    "json_mapping._resolved_hints": {"v": "RETIRED", "why": "private to the demo dialect"},
    "json_mapping._typename": {"v": "RETIRED", "why": "private to the demo dialect"},
    "json_mapping._unwrap": {"v": "RETIRED", "why": "private to the demo dialect"},
    "payloads.COV_NONE": {"v": "REPLACED", "sdk": "spatialdds.cov_none", "needs": [], "why": "N.1 form"},
    "payloads.frame_ref": {"v": "REPLACED", "sdk": "spatialdds.frame", "needs": [], "why": "N.1 form"},
    "payloads.stamp": {"v": "REPLACED", "sdk": "spatialdds.stamp", "needs": [], "why": "N.1 form (sec as a string)"},
    "payloads.vec": {"v": "STAYS", "why": "a three-float list; nothing to replace"},
    "payloads.SCHEMA_CORE": {"v": "RETIRED", "why": "schema_version strings come from the generated MODULE_IDs"},
    "payloads.SCHEMA_DISCOVERY": {"v": "RETIRED", "why": "as SCHEMA_CORE"},
    "payloads.SCHEMA_EVENTS": {"v": "RETIRED", "why": "as SCHEMA_CORE"},
    "payloads.SCHEMA_SEMANTICS": {"v": "RETIRED", "why": "as SCHEMA_CORE"},
    "payloads.ABSENT_SCALE": {"v": "STAYS", "why": "absent-value constant (filing S6)"},
    "payloads.ABSENT_CROSSING": {"v": "STAYS", "why": "absent-value constant (filing S6)"},
    "payloads.absent_detection3d": {"v": "STAYS", "why": "absent-value builder (filing S6)"},
    "payloads.absent_fused_track_observer": {"v": "STAYS", "why": "absent-value builder (filing S6)"},
    "plaza.enu_to_ecef_pose": {"v": "REPLACED", "sdk": "spatialdds.frames.enu_to_ecef_pose", "needs": [],
                               "why": "held equal by spatialdds-py's native_frames_agree"},
    "plaza.EARTH_FIXED_FQN": {"v": "REPLACED", "sdk": "spatialdds.frames.EARTH_FIXED_FQN", "needs": [], "why": "the same constant"},
    "model_service.venue_frame": {"v": "STAYS", "why": "venue frame; built with sd.frame()"},
    "model_service.earth_fixed_frame": {"v": "REPLACED", "sdk": "spatialdds.earth_fixed", "needs": [], "why": "the same record"},
    "model_service.live_log": {"v": "STAYS", "why": "log throttling"},
    "qos_profiles.MODEL_LATCHED": {"v": "STAYS", "sdk": "spatialdds.dds.Profile", "needs": [],
                                   "why": "a deployment profile, declared once as sd.dds.Profile: §3.3.2 names none for owm (queue 5)"},
    "qos_profiles.MODEL_FAST": {"v": "STAYS", "sdk": "spatialdds.dds.Profile", "needs": [],
                                "why": "as MODEL_LATCHED; no registered row matches (POSE_RT adds a 33 ms deadline)"},
    "qos_profiles.MODEL_COMMAND": {"v": "STAYS", "sdk": "spatialdds.dds.Profile", "needs": [],
                                   "why": "as MODEL_LATCHED; KEEP_LAST(16) matches no row"},
    "service_bus.service_id_base": {"v": "STAYS", "why": "convention helper"},
    "topics.SCENE": {"v": "STAYS", "why": "the venue's scene, passed to sd.participant(scene=)"},
    "topics.TOPIC_SOURCE_SPEC": {"v": "STAYS", "why": "web-bridge provenance label"},
    "topics.TOPIC_SOURCE_ANNOUNCE_PREVIEW": {"v": "STAYS", "why": "web-bridge provenance label"},
    "topics.TOPIC_SOURCE_FALLBACK": {"v": "STAYS", "why": "web-bridge provenance label"},
    "topics.TOPIC_SOURCE_MANIFEST": {"v": "STAYS", "why": "web-bridge provenance label"},
    "topics.TOPIC_SOURCE_REQUEST": {"v": "STAYS", "why": "web-bridge provenance label"},
    "topics.TOPIC_VPS_QUERY_V1": {"v": "STAYS", "needs": ["S2"], "why": "not scene-shaped; the spec names it only in an example (spatialdds-py candidates 9.5)"},
    "topics.TOPIC_VPS_RESULT_V1": {"v": "STAYS", "needs": ["S2"], "why": "as TOPIC_VPS_QUERY_V1"},
    "topics.TOPIC_BOOTSTRAP_QUERY_V1": {"v": "STAYS", "needs": ["S2"], "why": "the oarc.bootstrap_* extension lane"},
    "topics.TOPIC_BOOTSTRAP_RESPONSE_V1": {"v": "STAYS", "needs": ["S2"], "why": "the oarc.bootstrap_* extension lane"},
    "topics.TOPIC_DISCOVERY_RESPONSE": {"v": "STAYS", "needs": ["S2"], "why": "coverage reply topic, named by the asker"},
    "topics.TOPIC_ANCHORS_DELTA": {"v": "STAYS", "needs": ["S2"], "why": "spatialdds/anchors/<set>/delta/v1 has no scene segment"},
    "topics.TOPIC_DDS_ENVELOPE_V1": {"v": "RETIRED", "why": "moves with the envelope baseline"},
    "topics.TOPIC_DISCOVERY_ANNOUNCE_V1": {"v": "REPLACED", "sdk": "spatialdds.dds.ANNOUNCE", "needs": [], "why": "well-known"},
    "topics.TOPIC_DISCOVERY_CONTENT_V1": {"v": "REPLACED", "sdk": "spatialdds.dds.CONTENT", "needs": [], "why": "well-known"},
    "topics.TOPIC_DISCOVERY_DEPART_V1": {"v": "REPLACED", "sdk": "spatialdds.dds.DEPART", "needs": [], "why": "well-known (VOLATILE, as the spec makes it)"},
    "topics.TOPIC_DISCOVERY_QUERY_V1": {"v": "REPLACED", "sdk": "spatialdds.dds.QUERY", "needs": [], "why": "well-known"},
    "topics.TOPIC_FRAME_TRANSFORM_V1": {"v": "REPLACED", "sdk": "spatialdds.dds.topic_name", "needs": [],
                                        "why": "an extension lane: FrameTransform has no §3.3.2 row at v1.8 (queued)"},
    "typed_transport.MultiTopicSubscriber": {"v": "RETIRED", "needs": ["S2", "S9"], "why": "dies with stream"},
    "typed_transport.TypedDictWriter": {"v": "RETIRED", "sdk": "spatialdds.dds.Writer.write", "needs": [],
                                        "why": "Writer.write accepts the N.1 document"},
}


def verdict(mod: str, sym: str) -> dict:
    v = SYMBOLS.get(f"{mod}.{sym}") or MODULES.get(mod)
    if v is None:
        raise SystemExit(f"no verdict for {mod}.{sym}: place it in MODULES or SYMBOLS")
    return v


def check_sdk() -> list[str]:
    """Every named SDK equivalent, imported from the installed release."""
    import importlib
    bad = []
    for name, v in list(MODULES.items()) + list(SYMBOLS.items()):
        dotted = v.get("sdk")
        if not dotted:
            continue
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            try:
                obj = importlib.import_module(".".join(parts[:i]))
            except ImportError:
                continue
            try:
                for a in parts[i:]:
                    obj = getattr(obj, a)
            except AttributeError:
                bad.append(f"{name}: {dotted} does not exist")
            break
        else:
            bad.append(f"{name}: {dotted} does not import")
    return bad


class Tree:
    """The tracked `.py` files of the working tree, or of a git revision."""

    def __init__(self, rev: str | None = None) -> None:
        self.rev = rev

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout

    def files(self, under: str = "") -> list[str]:
        if self.rev is None:
            out = self._git("ls-files", "--", f"{under}*.py" if under else "*.py")
        else:
            out = self._git("ls-tree", "-r", "--name-only", self.rev,
                            "--", under or ".")
        return sorted(f for f in out.split() if f.endswith(".py"))

    def read(self, rel: str) -> str:
        if self.rev is None:
            return (ROOT / rel).read_text()
        return self._git("show", f"{self.rev}:{rel}")


def area(rel: str) -> str:
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else "(root)"


def walk(src: "Tree", rel: str) -> dict:
    text = src.read(rel)
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as e:
        return {"file": rel, "error": str(e)}
    uses: dict[str, set[str]] = defaultdict(set)   # module -> symbols
    aliases: dict[str, str] = {}                     # local name -> module
    cyclone, trees = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            top = n.module.split(".")[0]
            if top == "cyclonedds":
                cyclone.add(n.module)
            if top in TREES:
                trees.add(n.module)
            if n.module == PKG:
                for a in n.names:
                    uses[f"{PKG}.{a.name}"]
                    aliases[a.asname or a.name] = f"{PKG}.{a.name}"
            elif n.module.startswith(PKG + "."):
                for a in n.names:
                    uses[n.module].add(a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                top = a.name.split(".")[0]
                if top == "cyclonedds":
                    cyclone.add(a.name)
                if top in TREES:
                    trees.add(a.name)
                if a.name.startswith(PKG + "."):
                    uses[a.name]
                    aliases[a.asname or a.name] = a.name
    for n in ast.walk(tree):
        if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id in aliases):
            uses[aliases[n.value.id]].add(n.attr)
        elif (isinstance(n, ast.Attribute)
              and isinstance(n.value, ast.Attribute)
              and isinstance(n.value.value, ast.Name)
              and n.value.value.id == PKG):
            uses[f"{PKG}.{n.value.attr}"].add(n.attr)
    return {"file": rel, "area": area(rel),
            "lines": len(text.splitlines()),
            "uses": {m: sorted(s) for m, s in sorted(uses.items())},
            "cyclonedds": sorted(cyclone), "trees": sorted(trees)}


def inventory(src: "Tree" = None) -> dict:
    src = src or Tree()
    files = [walk(src, rel) for rel in src.files()]
    modules = {}
    for rel in src.files(PKG + "/"):
        if rel.count("/") != 1:
            continue
        name = f"{PKG}.{Path(rel).stem}"
        users = [f["file"] for f in files
                 if name in f.get("uses", {}) and f["file"] != rel]
        modules[name] = {"lines": len(src.read(rel).splitlines()),
                         "users": users}
    tree_lines = {t: sum(len(src.read(rel).splitlines())
                         for rel in src.files(t + "/"))
                  for t in TREES}
    return {"files": files, "modules": modules, "tree_lines": tree_lines}


def shrink(now_at: str | None = None) -> str:
    """The package and the generated trees at the census and now (or at
    `now_at`), counted the same way, with every module that went and every
    one that is new."""
    before, now = inventory(Tree(CENSUS_AT)), inventory(Tree(now_at))
    label = now_at or "now"

    def total(inv):
        # Counted as markdown() counts them, `__init__` included, so the
        # "before" is the census's own figure.
        mods = inv["modules"]
        return mods, sum(v["lines"] for v in mods.values())
    (bm, bl), (nm, nl) = total(before), total(now)
    out = [f"`{PKG}/` at {CENSUS_AT}: {len(bm)} modules, {bl} lines",
           f"`{PKG}/` {'at ' + label if now_at else label}: {len(nm)} modules, {nl} lines "
           f"({nl - bl:+d}, {100 * (nl - bl) / bl:+.0f}%)"]
    for t in TREES:
        out.append(f"`{t}`: {before['tree_lines'][t]} -> {now['tree_lines'][t]} lines")
    gone = sorted(set(bm) - set(nm))
    new = sorted(set(nm) - set(bm))
    out.append("gone: " + ", ".join(f"{m.split('.')[1]} ({bm[m]['lines']})"
                                    for m in gone))
    out.append("new: " + (", ".join(f"{m.split('.')[1]} ({nm[m]['lines']})"
                                    for m in new) or "none"))
    return "\n".join(out)


def markdown(inv: dict) -> str:
    out = []
    mods = inv["modules"]
    out.append(f"**{len(mods)} modules, "
               f"{sum(m['lines'] for m in mods.values())} lines** in "
               f"`{PKG}/`; generated trees: "
               + ", ".join(f"`{t}` {n} lines"
                           for t, n in inv["tree_lines"].items()) + ".\n")
    out.append("| module | lines | files using it | verdict | waits on | areas |")
    out.append("| --- | ---: | ---: | --- | --- | --- |")
    for name, m in mods.items():
        short = name.split('.')[1]
        if short == "__init__":
            continue
        v = MODULES.get(short, {"v": "NEW (after the census)"})
        areas = sorted({area(u) for u in m["users"]})
        out.append(f"| `{short}` | {m['lines']} | {len(m['users'])} | "
                   f"{v['v']} | {', '.join(v.get('needs', [])) or '–'} | "
                   f"{', '.join(areas)} |")
    out.append("")
    consumers = [f for f in inv["files"]
                 if not f["file"].startswith((PKG + "/",) + tuple(
                     t + "/" for t in TREES) + FIXTURE_TREES)]
    raw = [f for f in consumers if f.get("cyclonedds")]
    typed = [f for f in consumers if f.get("trees")]
    by = defaultdict(lambda: [0, 0, 0])
    for f in consumers:
        if f.get("uses") or f.get("cyclonedds") or f.get("trees"):
            by[f["area"]][0] += bool(f.get("uses"))
            by[f["area"]][1] += bool(f.get("cyclonedds"))
            by[f["area"]][2] += bool(f.get("trees"))
    out.append(f"**Consumers outside the package and the trees:** "
               f"{sum(1 for f in consumers if f.get('uses'))} files use "
               f"`{PKG}`, {len(raw)} import `cyclonedds` themselves, "
               f"{len(typed)} import a generated tree themselves.\n")
    out.append("| area | use the helpers | import cyclonedds | import a tree |")
    out.append("| --- | ---: | ---: | ---: |")
    for a, (u, c, t) in sorted(by.items()):
        out.append(f"| `{a}` | {u} | {c} | {t} |")
    out.append("")
    users: dict = defaultdict(set)
    for f in inv["files"]:
        for m, syms in f.get("uses", {}).items():
            short = m.split(".")[1]
            if f["file"] == f"{PKG}/{short}.py":
                continue
            for sym in syms or ["(module)"]:
                users[(short, sym)].add(f["file"])
    tally = defaultdict(int)
    rows = []
    for (short, sym), fs in sorted(users.items()):
        v = verdict(short, sym)
        tally[v["v"]] += 1
        rows.append(f"| `{short}.{sym}` | {len(fs)} | {v['v']} | "
                    f"{'`' + v['sdk'] + '`' if v.get('sdk') else '–'} | "
                    f"{', '.join(v.get('needs', [])) or '–'} | {v['why']} |")
    out.append(f"**{len(rows)} symbols used across modules: "
               + ", ".join(f"{k} {n}" for k, n in sorted(tally.items()))
               + ".**\n")
    out.append("| symbol | files | verdict | SDK equivalent | waits on | why |")
    out.append("| --- | ---: | --- | --- | --- | --- |")
    out += rows
    out.append("")
    out.append("**Every call site, by file.** A file's line lists the "
               "helper symbols it uses.\n")
    for f in sorted(inv["files"], key=lambda f: f["file"]):
        if not f.get("uses") or f["file"].startswith(PKG + "/__init__"):
            continue
        syms = [f"{m.split('.')[1]}.{x}" for m, xs in f["uses"].items()
                for x in (xs or ["(module)"])
                if f["file"] != f"{PKG}/{m.split('.')[1]}.py"]
        if syms:
            out.append(f"- `{f['file']}`: " + ", ".join(f"`{x}`" for x in syms))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-doc", action="store_true",
                    help="write the tables between the census doc's markers")
    ap.add_argument("--check-doc", action="store_true",
                    help="fail if the census doc's tables differ from the tree at "
                         "the census, or its shrink block from the working tree")
    ap.add_argument("--at", metavar="REV",
                    help="read the tree at a git revision, not the working one")
    ap.add_argument("--shrink", action="store_true",
                    help="the package and the generated trees at the census "
                         "and now (or at --now)")
    ap.add_argument("--now", metavar="REV",
                    help="with --shrink: measure the after side at REV")
    ap.add_argument("--check-sdk", action="store_true",
                    help="import every named SDK equivalent from the "
                         "installed spatialdds; fail on any that is missing")
    a = ap.parse_args()
    if a.check_sdk:
        import spatialdds
        bad = check_sdk()
        print(f"spatialdds {spatialdds.__version__}: "
              f"{'every named equivalent exists' if not bad else 'MISSING'}")
        for b in bad:
            print("  " + b)
        return 1 if bad else 0
    if a.shrink:
        print(shrink(a.now))
        return 0
    if a.write_doc or a.check_doc:
        inv = inventory(Tree(a.at or CENSUS_AT))
        text = DOC.read_text()
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        want = f"{head}{BEGIN}\n{markdown(inv)}\n{END}{tail}"
        if SHRINK_BEGIN in want:
            head, rest = want.split(SHRINK_BEGIN, 1)
            _, tail = rest.split(SHRINK_END, 1)
            block = "\n".join(f"- {line}" for line in shrink().splitlines())
            want = f"{head}{SHRINK_BEGIN}\n{block}\n{SHRINK_END}{tail}"
        if a.check_doc:
            ok = want == text
            print(f"{DOC.relative_to(ROOT)}: "
                  f"{'tables match the tree' if ok else 'STALE: run --write-doc'}")
            return 0 if ok else 1
        DOC.write_text(want)
        print(f"wrote the tables into {DOC.relative_to(ROOT)}")
        return 0
    inv = inventory(Tree(a.at))
    print(json.dumps(inv, indent=1) if a.json else markdown(inv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
