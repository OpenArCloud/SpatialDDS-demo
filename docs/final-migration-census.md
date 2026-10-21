# The last migration: census of every pre-1.8 artifact

**Taken 2026-10-20, against `dce97cd`.** Four verdicts: **RETIRE** (the
standard absorbed its job), **MIGRATE** (port to 1.8), **CURATE** (frozen
cross-era fixture, deliberately kept), **ARCHIVE** (labeled era artifact, out
of the build).

Three of the brief's working claims did not survive measurement. They are
marked **FINDING** and are the parts that need a ruling before anything is
deleted.


## Ruling, 2026-10-20

The census's one stop is closed. Applied throughout below; where the ruling
changed a verdict the original is kept struck through so the reasoning that
was overruled stays legible.

* **Finding 1 — Option 1, migrate.** Retiring the catalogue for binding search
  would also make content discovery HTTP-only and strand headless DDS
  clients — a second drop the census did not name. **Sharpening:** before any
  demo-owned wire extension, check the manifest. The 1.8-native chain is
  `ContentAnnounce` (thin, latched) → `manifest_uri` → resolver → manifest →
  asset refs, and the manifest is where the spec already provides extension
  room. Only a field the manifest genuinely cannot carry gets a labeled
  demo-owned extension. Residual query ergonomics (`limit`, `page_token`,
  `content_id_in`) file to the spec queue as a 1.9 entry. **Acceptance:** the
  page resolves `catalog:dcef7c31-…` through the migrated path and the
  TurtleBot draws; every field `/v1/catalog/query` returned is reachable
  through the new chain, asserted.
* **Finding 2 — affirmed.** `test_idl_trees_agree.py` survives untouched.
  Acceptance item 2 is a **new** gate, shown failable by a planted 1.7 import.
* **Finding 3 — named subset, derived mechanically**: the probe plus the
  import closure of the types it exercises, computed from its imports rather
  than hand-picked. Fixture directory: probe + driver, the closed subset, a
  checksum manifest, a README stating the claim. Constructed **in the same
  commit group as the 1.7 tree retirement**, so the repo never has neither.
* **RETIRE list** — `oarc_model`, `/v1/frames` (after the transforms proof),
  the 1.7 tree and compile lane. **`idl/v1.4`, `idl/v1.6` and their
  manifests: delete, not ARCHIVE** — no code imports them and git history is
  the archive. Prose references are updated rather than dead directories
  labeled.
* **Recordings — resolved, no swap.** All five `provenance.json` files read
  `idl_source: idl/v1.8 (vendored from SpatialDDS-spec 9c3e1f2)`, types
  `spatial.owm/0.1`. Nothing in an acceptance chain is 1.7-era.

**Order:** migrations first (camera leg, catalogue, `multi_operator_fusion`
via payloads, localize shim), retirements after their replacements are
proven, docs in the same commit as what they describe.

---

## The shape of the problem, measured

`spatialdds_idl` (1.7) has **41 external consumers** — 19 non-test, 22 test:

| area | files |
|---|---|
| `bridges/` | 9 |
| `tests/` | 8 |
| `multi_operator_fusion/` | 6 |
| `spatialdds_demo/` | 6 |
| `ar_demo/` | 5 |
| `deepsense/` | 4 |
| `nuscenes/` | 2 |
| `benchmarks/` | 1 |

`spatialdds_demo` itself straddles the two eras, and the split is clean:

| on 1.8 | on 1.7 |
|---|---|
| `model_service`, `robot_bridge`, `pond_watch`, `duck_mover` | `topic_types` (12 refs), `service_bus` (3), `discovery_bus` (2), `blob`, `json_mapping`, `stream` |

---

## 1. The catalogue and the bridge's RPC routes

### FINDING 1 — the registry does not absorb the catalogue's job

The working claim was that catalogue queries become search and resolve. Field
by field, they do not. 1.8's `CoverageQuery`/`CoverageResponse` is **service**
discovery: `CoverageResponse.results` is `sequence<ServiceSummary,256>`, and
`CoverageFilter` filters on `type_in` / `qos_profile_in` / `module_id_in` —
topic and service attributes, not content.

The content type is `ContentAnnounce`, which is a **latched announce, not a
request/reply**, and it is not a superset:

| `CatalogEntry` (1.7) | `ContentAnnounce` (1.8) | |
|---|---|---|
| `content_id` | `content_id` (@key) | same |
| `name` | `title` | renamed |
| `kind` ("mesh","poi","overlay") | `class_id` / `tags` | approximate |
| `coverage` (4) | `coverage` (16) | widened |
| `frame_ref` | `coverage_frame_ref` | same role |
| `href` (any URL) | `manifest_uri` (**MUST** be `spatialdds://`) | narrowed |
| `formats` `seq<string,8>` | — | **absent** |
| `has_pose` / `pose` | `transforms` `seq<Transform,8>` | different shape |
| `has_asset` / `asset` (`AssetRef`) | — | **absent** |
| — | `provider_id`, `summary`, `available_from`/`until`, `ttl_sec` | 1.8 gains |

And on the query side `limit`, `page_token` and `CatalogFilter.content_id_in`
have no 1.8 equivalent.

**Why this is load-bearing rather than academic.** `web/src/app.ts` resolves
`catalog:<content_id>` through `/v1/catalog/query`, falling back to asking the
catalogue directly on a miss. That is the path by which the robot's
`catalog:dcef7c31-…` becomes a drawn TurtleBot — the thing verified working
earlier today. Retiring the catalogue without a named replacement for
*content-id → renderable asset* is a silent capability drop in the audience
path, which is exactly what the brief forbids.

**Ruling needed.** Three options, in my order of preference:

1. **MIGRATE the catalogue onto 1.8 types** — `ContentAnnounce` on a latched
   lane plus the binding's `search`, with `formats`/`asset`/`pose` carried as
   a demo-owned extension where 1.8 has no field. Keeps the page's resolution
   path, costs one more demo-owned type, and the gaps become a spec-queue
   entry with a real origin.
2. **RETIRE and move resolution to the binding**, if the binding can serve
   content-id → asset. Not yet verified that it can; that verification is a
   prerequisite, not an assumption.
3. **CURATE the catalogue** as part of the frozen 1.7 exhibit and give the
   page a 1.8-native path. Largest change to the audience path.

**What landed (Option 1, sharpened).** The manifest was checked first, as
ruled, and it carries more than the table above credits: `assets[]` holds a
hashed `AssetRef`, and placement rides `ContentAnnounce.transforms` natively
— one transform from the item's own frame (`<frame>/content/<rid>`) into the
frame it sits in, plus that frame's anchor. So no demo wire type was added.

- **Bus:** the catalogue publishes one latched `ContentAnnounce` per row on
  `spatialdds/discovery/content/v1` (`discovery_bus.ContentAnnouncePublisher`;
  readers use `ContentInventory`, which honours disposal and §3.3 staleness).
  The headless AR client reads it directly — no HTTP.
- **Manifests:** each announce's `manifest_uri`
  (Appendix F, `rtype` `content` — e.g.
  `spatialdds://catalog.example.com/demo/content/dcef7c31-1d5f-54aa-9344-899912f9d34a`) names a
  `spatial.manifest/1.8` content manifest the catalogue serves from its own
  §7.5.2 resolver; the bridge fronts it at `/.well-known/spatialdds/resolver`
  and `/v1/content/resolve`.
- **Extension, only where the manifest cannot carry it:** `"oarc.catalog"`
  (top-level, which §8.1 permits) holds an unhashed `href`, non-media-type
  `formats`, and the frame of an unplaced row. Counts and reasons in
  `ar_demo/SPEC_COMPLIANCE.md`; filed as `owm-1.8-spec-queue.md` §7–8.
- **Query ergonomics** (`limit`, `page_token`, `content_id_in`) and HTTP's
  lack of content enumeration: filed as §6, a 1.9 entry. The demo keeps them
  as a reader-side filter behind `POST /v1/content/query`.
- **Retired:** `oarc_demo::CatalogQuery` / `CatalogResponse` / `CatalogEntry`
  (dropped from the 1.8 demo IDL), `service_bus.CatalogService` /
  `CatalogClient`, the `oarc.catalog_*` registry extensions, and
  `/v1/catalog/query`.
- **Asserted:** `tests/fixtures/catalogue-1.7-baseline.json` freezes what the
  1.7 query returned for all 34 rows, captured from `a18bd99` before anything
  changed. `tests/test_catalogue_chain.py` rebuilds every row from the
  announce and manifest alone and compares every key;
  `web/tests/content-chain.spec.ts` asserts the page's reconstruction against
  the same capture, including that the placed row lands where `/v1/frames`
  put it (since the route's retirement, its frozen capture). Both shown failable with planted faults.

### The RPC routes, individually

| route | verdict | basis |
|---|---|---|
| `/v1/localize` | **MIGRATE** (stays a shim) | per brief; successor is 1.9 entry 5. Speaks 1.8 argeo underneath |
| `/v1/frames` | **RETIRED** | shown equivalent first; see "The frames route" below |
| `/v1/stream` | census pending | websocket, not catalogue; not named in the brief |
| `/v1/catalog/query` | **RETIRED** (Finding 1, Option 1) | replaced by `POST /v1/content/query` (the inventory, filtered) and the §7.5 resolver; see "What landed" below |

### The frames route — what was measured before it went

`GET /v1/frames` was a view over cached service announces: the union of their
`transforms`, keyed by local frame. Its only source was the catalogue
server's announce, whose transforms come from `SPATIALDDS_FRAME_ANCHORS` — so
the anchors files the repository ships are the whole of what it could serve:
one frame, `map/ut-littlefield-fountain`.

- **Captured** live on 2026-10-21 against `bcea0c4`, full stack plus the
  robot tier, beside its two replacements read in the same minute:
  `tests/fixtures/frames-route-baseline.json`. Frozen.
- **Compared** by role, not by name (`from` is `child_ref`, `to` is
  `parent_ref`): the binding's frames lane and the placed item's
  `ContentAnnounce` anchor serve the route's frame with the same identity
  (UUIDv5 of the fqn, both ends) and a bit-identical pose.
- **One difference, measured and pinned, not a disagreement:** the scene's
  `child_ref` declares its scale (`SCALE_DECLARED`, 1 m/unit); the
  catalogue's says `SCALE_UNKNOWN`. Nothing that read the route read scale.
- **Asserted** by `tests/test_frames_route_retired.py`: the capture covers
  every shipped anchor; the code that publishes each replacement produces the
  captured transform; against a running stack the lane and the inventory
  serve it and the route answers 404. `web/tests/content-chain.spec.ts` now
  places the 1.7 row through the captured route answer and the 1.8 documents
  through the announce alone. Failable: an inverted, moved, missing or
  renamed lane, an unanchored inventory and a rotated anchor are each caught;
  moving the captured frame 100 m fails both the Python and the page halves;
  planting a second anchored frame fails the coverage check.
- **The page** reads the scene's lane for the model, and merges the content
  anchors under it on Discover, where the scene-vs-catalogue disagreement log
  now lives. The bootstrap's fallback to the catalogue's claim is gone: the
  binding that serves the entities serves the lane beside them, so a stack
  with one and not the other no longer exists. `bridgeDiscover`'s fallback to
  the route could not have answered a frame the announces left out — both
  came from the same anchors — and now uses the frames the page holds.
- `tests/test_georeference.py`'s two-route check compares the scene's lane
  with the content anchor instead of the route.

---

## 2. The camera leg — **MIGRATE**

`ar_demo/spatialdds_demo_server.py` imports `spatialdds_idl.spatial.argeo`'s
`VpsRequest`/`VpsResponse`; 1.8's `argeo.idl` carries both. The structural
trap is the one the OpenVPS migration documented, and it is real here:

| | `FrameRef` fields |
|---|---|
| 1.7 | `uuid`, `fqn`, `has_coord_convention`, `coord_convention` |
| 1.8 | …plus `has_scale`, `scale_status`, `meters_per_unit`, `display_unit` |

4 → 8. Any positional construction silently misaligns. Consumers to port:
`ar_demo/spatialdds_demo_{server,client}.py`, `spatialdds_demo/service_bus.py`,
`bridges/web_bridge/server.py`, `benchmarks/bench_latency.py`,
`tests/test_vps_blob_path.py`, `web/src/spatialdds_bridge.ts`.

**What it restores:** the local camera acceptance deferred to AWS becomes
runnable again against a 1.8 mock.

---

## 3. The 1.7 generated tree and the compile lane — **RETIRED**

Retireable only after §1, §2 and §5 landed: 41 consumers at the census. Two
parts were already dead and went independently, `oarc_model` and `/v1/frames`
(§1). The rest went on 2026-10-21, in the same change that built the §4
exhibit, so the repository never had neither:

* **Deleted:** `spatialdds_idl/` (the 1.7 generated tree), `idl/v1.7`,
  `idl/demo` (the 1.7 demo IDL), `idl/v1.4`, `idl/v1.6`, `manifests/v1.4`,
  `manifests/v1.6` — 155 tracked files.
* **The last two consumers**, measured before deleting: `tests/interop_probe.py`
  (now the exhibit) and `tests/test_generated_types.py`, whose round-trip,
  key and wire-name checks now run against `spatialdds_idl18` and
  `spatialdds_demo_idl18`, with the era read from the tree's own `MODULE_ID`.
* **The compile lane:** `scripts/generate_types.py`'s defaults moved to
  `idl/v1.8` → `spatialdds_idl18`, spec types only; `--no-demo` is gone
  because an unbound run is spec-only and demo types come only through
  `--bind-to`. Its `HEADER` is unchanged — it is written into every generated
  file, and `spatialdds_idl18` must stay byte-identical to `spatialdds_ros`'s
  copy. Drift gate green on both remaining trees in the demo image.
* `Dockerfile`'s transitional `COPY spatialdds_idl` is gone.

* **`oarc_model`** — **RETIRED.** Zero imports remained; the six files that
  mentioned it did so in prose only (`model_service.py:5`, `robot_bridge.py:24`,
  four test docstrings). Done as `docs/bridge-1.8-census.md` recorded at line
  317: `idl/demo/oarc_model.idl` and `spatialdds_idl/oarc_model/` deleted, the
  tree regenerated in the demo image (the only other change it made was
  `spatialdds_idl/__init__.py` losing the import and its `__all__` entry), the
  drift check green. Prose that described the prototype as present now
  describes it as the history of `spatial.owm/0.1`; prose that already did
  (`model_service.py`, `world-model.md`, the evidence file, the censuses) is
  unchanged.

### FINDING 2 — the trees-agree gate is not about the two eras

The brief expects `tests/test_idl_trees_agree.py` to reduce or retire when one
tree remains. It will not: that gate compares **this repository's
`spatialdds_idl18` against `spatialdds_ros`'s vendored copy** — two copies of
*1.8*, which is a different drift. Retiring the 1.7 tree does not touch it,
and it must survive. `tests/test_single_source_of_truth.py` is also unrelated
(duplicate module-level assignment within `spatialdds_demo`).

No gate currently asserts "one era". Acceptance item 2 therefore needs a
**new** gate, not a reduced one.

**Built: `tests/test_one_era.py`.** The era is the one bundle under `idl/`,
and anything versioned below it is pre-era. Outside `tests/cross_era/` (and
the gate itself, which has to name what it forbids):

1. no Python file imports the 1.7 package, statically or by string;
2. one `idl/v*` bundle and one `manifests/v*` set, both the era, and every
   generated `MODULE_ID` the era (or a provisional `0.x`);
3. no **live** pre-era reference: Python string literals other than
   docstrings, non-comment lines of shell, Dockerfile, YAML, JSON,
   TypeScript and IDL, and Markdown link targets. A comment, a docstring or a
   census that says what happened is history and stays; a command, a `COPY`,
   an import or a link that points at what is gone is a defect;
4. the 1.7 package does not import from the repository root at all, which
   also catches one installed in the environment, where no static check can
   see.

`tests/test_cross_era_fixture.py` is not exempt: it takes the package name
from the exhibit's directory instead of spelling it.

**Shown failable before it counted**, each plant in a real file, each removed
and the gate green again after:

| planted | result |
|---|---|
| `from spatialdds_idl.spatial.core import GeoPose` at `spatialdds_demo/model_service.py:32` | `test_no_pre_era_import` fails: `spatialdds_demo/model_service.py:32: imports spatialdds_idl.spatial.core` |
| `COPY spatialdds_idl ./spatialdds_idl` appended to `Dockerfile` | `test_no_live_pre_era_reference` fails at `Dockerfile:45` |
| a link `[the old IDL](idl/v1.7/core.idl)` appended to `README.md` | `test_no_live_pre_era_reference` fails: `README.md: links to idl/v1.7/core.idl` |
| an empty `spatialdds_idl/__init__.py` at the root | `test_the_old_package_does_not_import` fails |

The first two attempts at the import plant changed nothing: BSD `sed` has no
`0,/re/` address, and the replacement anchor the second attempt used was not
in the file. The gate's green on those runs proved only that. The third
attempt checked that the line was there before running.

And against `bcea0c4`, the tree before any of this: every check errors at
"expected one spec bundle under idl/, found v1.4, v1.6, v1.7, v1.8". Scanning
with the era forced to 1.8 finds 67 pre-era imports (the tree itself plus
exactly the census's last two consumers, `tests/interop_probe.py` and
`tests/test_generated_types.py`) and 581 live references.

**And it caught this entry.** The table above quotes the planted link in a
code span, and the gate's first cut matched link syntax anywhere, so it
failed on its own census. Rendered, a code span is text, not a link: the
gate now strips code spans and fenced blocks first, pinned by a case that
holds one quoted link, one fenced and one real, and catches only the real
one. The README plant was re-run after the change and still fails.

---

## 4. The cross-era fixture — **CURATED**: `tests/cross_era/`

**Built** as ruled — probe, driver, the mechanically derived subset, a
checksum manifest and a README stating the claim. The README carries the
derivation; in brief:

* **The subset is the closure, derived:** 19 of the tree's 29 generated
  modules, by a static walk from the probe's imports through every import and
  every dotted type name a reached module spells. Package `__init__`s are not
  followed, since idlc's import every sibling and would make the closure the
  whole tree. The 19 are byte-identical to `bcea0c4`; three `__init__`s are
  filtered to the kept children. A type-level walk (field types only) finds
  18, missing `zone_kind_enum`, which `_events.py` names in a module-level
  typedef. The module-level closure is the right one for files kept whole.
* **The probe is byte-identical** to `tests/interop_probe.py` at `bcea0c4`
  (sha256 `619422b0…`).
* **Isolated by the import system:** the driver runs the probe with its own
  directory as its only path, so the 1.7 package is the subset and
  `spatialdds_demo` does not import. Checked in the image: `spatial.core/1.7`,
  4-member `FrameRef`, `No module named 'spatialdds_demo'`.
* **Frozen:** `MANIFEST.sha256` lists all 45 files; its digest is pinned in
  `tests/test_cross_era_fixture.py`, which also re-derives the closure over the
  subset each run — closed and minimal both asserted. Failable: a one-byte
  change, a stray file, a dangling reference and an unreached module are each
  caught.
* **Green in the image** with the 1.7 tree gone: both directions, plus the
  deadline test, which was split out to `tests/test_deadline_qos.py` so the
  exhibit does not freeze a test of 1.8 behaviour.
* **The driver is `interop_driver.py`, not `test_*.py`:** pytest's default
  import mode prepends a collected test's directory to `sys.path`, which would
  have made the 1.7 subset importable for the rest of the host suite. Its two
  tests were skips on the host anyway (they need DDS); they run in their
  Docker lane, now "cross-era interop probe".

The census as written before the ruling:

### FINDING 3 — the exhibit does not exist yet; this migration creates it

`tests/interop_probe.py` is built from `spatialdds_idl` and driven by
`tests/test_interop.py` against the demo. Today that is **1.7 against 1.7** —
an independent-implementation proof, not a cross-era one. It *becomes* the
1.7-client-against-1.8-service exhibit only once the demo is 1.8.

So the curated fixture must be constructed deliberately, and the census names
what it contains: `tests/interop_probe.py` frozen with a checksum, the subset
of `spatialdds_idl` it imports (not the whole 97-file tree), the §3.3.3
profile table it was written against, and `tests/test_interop.py` as its
driver — isolated in one directory so nothing else accommodates a second era.

---

## 5. Older eras, recordings, datasets

| subject | verdict | basis |
|---|---|---|
| `idl/v1.4` (13 files), `idl/v1.6` (18) | ~~ARCHIVE~~ **RETIRED (deleted)** per ruling | no code imported either. The prose at `subscriber_rerun.py:487` and the dashboard-test docstring name the spec *revision* that added a type, not the directory, and stay; `SPEC_COMPLIANCE.md`'s now says the bundles were deleted |
| `manifests/v1.4`, `manifests/v1.6` | ~~ARCHIVE~~ **RETIRED (deleted)** per ruling | same; the URI gate's exemption for them went with them |
| `multi_operator_fusion` runtime | **MIGRATE** via `spatialdds_demo` | `fusion_service.py` → `spatialdds_types.py` → `spatialdds_demo.payloads`; no direct generated-tree import. Only its 6 **tests** import 1.7 |
| `recordings/owm-littlefield.*`, `robot-littlefield-on-library.*` | **keep** (already 1.8) | in acceptance chains |
| `recordings/bridge-littlefield.*`, `dispose-without-tombstone.*` | **keep** (1.8) | provenance reads `idl/v1.8`, spec `9c3e1f2`, `spatial.owm/0.1` |
| image/pose datasets (`nuscenes`, `deepsense`, `deploy/aws/maps`) | **keep** | era-agnostic |

---

## 6. Docs

**MIGRATE:** `README.md` (the era table at lines 5–22 documents the two-tree
split as deliberate — it becomes one era plus one exhibit), `CONTRIBUTING.md`,
`ar_demo/README.md`, `ar_demo/DOCKER_GUIDE.md`, `ar_demo/SPEC_COMPLIANCE.md`,
`benchmarks/README.md`, `bridges/web_bridge/README.md`,
`multi_operator_fusion/README.md`, `deploy/aws/README.md`.

~~**ARCHIVE, labeled:** `docs/SpatialDDS-1.7-full.md`.~~ **Deleted**, on the
ruling's reasoning for `idl/v1.4`/`v1.6` (git history is the archive). It is
the only call here the ruling did not name. A vendored 1.7 spec text in a 1.8
repository was also the README's one link to "the spec text". That link now
points at SpatialDDS-spec at `9c3e1f2`, the commit `idl/v1.8` is vendored
from. Restoring the file is one `git checkout bcea0c4 -- docs/SpatialDDS-1.7-full.md`.

**Keep as history:** the censuses (`bridge-1.8`, `owm-1.8`, `robot-1.8`,
`venue-georeference`) and `recordings/*.evidence.md` — they describe a
migration and are correct about the era they record.

---

## What I need ruled

1. **Finding 1** — which of the three catalogue options, since the registry
   does not cover content resolution and the browser depends on it.
2. **The RETIRE list**, item by item: `oarc_model` (ready now), `/v1/frames`
   (after the equivalence check), the 1.7 tree (last), `idl/v1.4` + `idl/v1.6`
   as ARCHIVE rather than delete.
3. **Finding 3's fixture contents** — the subset listed in §4, or the whole
   1.7 tree frozen.

---

## Found during the migration, not in the census

The census was taken against the code; running every lane against the
migrated tree found these, and each is either fixed in the commit that found
it or filed with its measurement.

| finding | status |
|---|---|
| `ar_demo/spatialdds.idl` — an aggregator `#include`-ing every `idl/v1.7` file, which the census missed | migrated to `idl/v1.8`; idlc exits 0 |
| `manifests/v1.7` — two demo-authored manifests (not vendored copies) with no verdict | moved to `manifests/v1.8`, their era claims with them |
| **Era claims carried as data**: 65 literals in 16 files (`schema_version`, `preferred_profiles`, `ProfileSupport` minors, manifest `profile`, bootstrap schema) | moved; derived from generated `MODULE_ID`s or gated against them by `tests/test_era_claims.py` |
| 1.8's §3.3.2 registered `crossing_line` and `model_entity`; the validator knew neither | registered |
| `model_entity`'s row names `spatialdds/<scene>/model/entity/v0`; the reference gateway at the same pin serves `world/entity/v1` | filed, `owm-1.8-spec-queue.md` §4 |
| `model_entity` has no named QoS profile; implementations announce `MODEL_LATCHED`, which no §3.3.3 table contains | filed, §5 |
| The "IDL compile + protocol" lane ran the image's baked copies; the image was built 2026-08-23 | lane now bind-mounts the working tree |
| `Dockerfile`'s COPY set never included a generated tree | fixed; the guide's commands verified against a fresh build |
| The local base image is amd64-only, so `cyclonedds-python:latest` (arm64) cannot be rebuilt from this `Dockerfile` on this machine | filed here; not fixed in this batch |
| `spatialdds_idl18/__init__.py` says "Generated SpatialDDS **1.7** types" — the generator hard-codes the phrase | filed: fixing it changes the tree's bytes, and trees-agree requires them identical to `spatialdds_ros`'s (read-only here) |
| `web/tests/venue-distance.spec.ts` chose its subject by insertion order | fixed: every venue entity is probed |
| …and its orbit assertion has never been able to fail (drill-pick finds nothing at orbital range even with scoping removed) | filed in the spec itself; the near half is genuine |
| `model-stack.spec.ts` "a retired entity…": the duck was seen, then absent milliseconds later, once in three full runs | **measured and fixed** (two mechanisms, both pre-existing, both made likelier by the catalogue migration's longer asset lookup): (1) the page's autostart checked "already localized?" *before* a two-second VPS discovery, so a Localize press landing meanwhile was followed by a second localize, whose `clearEntities()` blanked the venue for its redraw — after the page looked ready; it now asks again after the await — whether a localization *succeeded*, since a failed one holds the prior in `currentPose` and the second attempt is what rescues a mock-VPS decline (a first cut that checked `currentPose` stranded the page that way, caught by the next full run). (2) a bootstrap requested after a clear was collapsed onto one that began before it, so what the clear removed was never redrawn; requests after a clear now get a run of their own. A/B against `a18bd99` on the same stack: the mover test went 5/5 at baseline, 2/5 migrated, 5/5 migrated with the fix. Full Playwright suite after both: 83 passed, 6 skipped, in three consecutive runs. A first A/B was confounded — the baseline worktree lacked the ignored `web/.env.local`, so only one side streamed photorealistic tiles, which alone took the file from 2.6 to 4.4 min |
| The generated drift gate had only ever checked the 1.7 tree | now checks all three |
| **No `spatialdds://` URI in the repository was valid** under either era's Appendix F: 42 occurrences, all `.../zone:<z>/<manifest\|service>:<id>`, matching a validator that enforced the same invented shape | rewritten to `<authority>/<zone>/service/<id>`; the validator follows the ABNF; `tests/test_spatial_uris.py` checks the corpus, not just the validator |
| 1.8 says the canonical form of an entity's `content_refs` is a `spatialdds://` manifest URI; the demo uses a private `catalog:<id>` scheme | **not changed**: the catalogue migration gave every item a manifest URI, but the robot and the duck's model service still publish `catalog:<id>`, which the page resolves by id through the migrated chain. Recorded in `ar_demo/SPEC_COMPLIANCE.md` |
| The spec's own v1.8 example content manifest claimed `spatial.core/1.7` | **already fixed upstream** (`7b558f3`); measured at origin `5ff8027` before filing, so not filed |
| SPEC_COMPLIANCE's extensions table listed seven `oarc.*` names and said five; the registry held three | table brought to the registry; `tests/test_topic_discovery.py` now holds both |
| The first `ContentAnnounce` builder stamped each item with its content's update time, so under §3.3's `2 × ttl_sec` rule a January edit announced itself expired | stamp is publication time; update time is the manifest's; pinned by `test_the_announce_stamp_is_its_publication_time` |
| `bench_coverage_query.py` timed the retired catalogue query, against its own mock server | now times a late-joining reader receiving the whole inventory; pre- and post-migration results are not comparable, and the plotter refuses the old column |
| `web/tests/model-layer.spec.ts` placed every model entity through a hand-typed venue frame labelled "as measured"; its quaternion `w` differed from the announced one in the ninth digit (`…3448856` vs `…3490789`) | **measured, not inferred:** `a41e6de`'s own projection rerun on its own anchor gives the announced value, so the copy was a transcription slip, never a measurement. The spec now reads the frozen `/v1/frames` capture. Harmless at venue scale (~1e-7 m over 20 m), and exactly the retyped-number class |
| `spatialdds_validation` rejected `spatial.core/1.7` with "expected `spatial.<profile>/1.7`" — its pattern had moved to 1.8 and its four messages had not | one `ERA` constant now builds the pattern, `MANIFEST_MIN_MINOR` and every message; the era gate asserts the constant is the generated era and that a rejection names it (shown failable by restoring the old message) |
| `ar_demo/http_binding.py`'s root document said `"version": "1.7.0"`; no era gate looked, because it is not shaped like a module identifier | derived from `SpatialDDSValidator.ERA` |
| The static gate's generated-tree discovery matched names starting `spatialdds_idl`, so `spatialdds_demo_idl18` was never introspected | trees are found by the generator's own mark; both resolve. It also required "at least two" trees, which the 1.7 retirement would have satisfied only by luck |
| `ar_demo/README.md`, `SPEC_COMPLIANCE.md`, `topic_types.py`, `deepsense/*`, `web/tests/fixtures/README.md`, `idl/demo18/oarc_demo.idl`, `analysis/scenescape/…/mapping-table.md` pointed at `idl/v1.7`, `idl/demo` or `spatialdds_idl` | repointed to `idl/v1.8` or dated as history; the mapping table's line citations are kept and say to follow them at the spec's `v1.7` tag |
