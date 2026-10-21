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

### The RPC routes, individually

| route | verdict | basis |
|---|---|---|
| `/v1/localize` | **MIGRATE** (stays a shim) | per brief; successor is 1.9 entry 5. Speaks 1.8 argeo underneath |
| `/v1/frames` | **RETIRE**, pending check | the page already reads the binding's `frames/frame_transform/v1`; both must be shown to serve the same transforms first |
| `/v1/stream` | census pending | websocket, not catalogue; not named in the brief |
| `/v1/catalog/query` | blocked on Finding 1 | |

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

## 3. The 1.7 generated tree and the compile lane — **RETIRE**, last

Retireable only after §1, §2 and §5 land: 41 consumers today. Two parts are
already dead and can go independently:

* **`oarc_model`** — **RETIRE now.** Zero imports remain; the six files that
  mention it do so in prose only (`model_service.py:5`, `robot_bridge.py:24`,
  four test docstrings). This is the four-step edit `docs/bridge-1.8-census.md`
  recorded at line 317 and left for James: delete `idl/demo/oarc_model.idl`,
  drop it from `spatialdds_idl/__init__.py` and `__all__`, regenerate, correct
  seven prose references.

### FINDING 2 — the trees-agree gate is not about the two eras

The brief expects `tests/test_idl_trees_agree.py` to reduce or retire when one
tree remains. It will not: that gate compares **this repository's
`spatialdds_idl18` against `spatialdds_ros`'s vendored copy** — two copies of
*1.8*, which is a different drift. Retiring the 1.7 tree does not touch it,
and it must survive. `tests/test_single_source_of_truth.py` is also unrelated
(duplicate module-level assignment within `spatialdds_demo`).

No gate currently asserts "one era". Acceptance item 2 therefore needs a
**new** gate, not a reduced one.

---

## 4. The cross-era fixture — **CURATE**

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
| `idl/v1.4` (13 files), `idl/v1.6` (18) | ~~ARCHIVE~~ **RETIRE (delete)** per ruling | no code imports either; every reference is prose (`SPEC_COMPLIANCE.md:324`, `subscriber_rerun.py:487`, a dashboard-test docstring) |
| `manifests/v1.4`, `manifests/v1.6` | ~~ARCHIVE~~ **RETIRE (delete)** per ruling | same; prose references updated instead |
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

**ARCHIVE, labeled:** `docs/SpatialDDS-1.7-full.md`.

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
| `model-stack.spec.ts` "a retired entity…": the duck was seen, then absent milliseconds later, once in three full runs | observed once, mechanism unconfirmed; not changed |
| The generated drift gate had only ever checked the 1.7 tree | now checks all three |
| **No `spatialdds://` URI in the repository was valid** under either era's Appendix F: 42 occurrences, all `.../zone:<z>/<manifest\|service>:<id>`, matching a validator that enforced the same invented shape | rewritten to `<authority>/<zone>/service/<id>`; the validator follows the ABNF; `tests/test_spatial_uris.py` checks the corpus, not just the validator |
| 1.8 says the canonical form of an entity's `content_refs` is a `spatialdds://` manifest URI; the demo uses a private `catalog:<id>` scheme | addressed in the catalogue migration: the page resolves both |
