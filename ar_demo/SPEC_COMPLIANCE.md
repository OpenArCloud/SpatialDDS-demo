# SpatialDDS v1.7 Compliance Notes

**Conformant to:** SpatialDDS 1.7 (2026-08-23, tag `v1.7`) — the stamped
release, not a draft revision. `idl/v1.7/` is vendored verbatim from that tag
and `docs/SpatialDDS-1.7-full.md` is its assembled text.

**Not tracking `main`.** The spec repo's main branch is now the 1.8 draft. This
demo moves to 1.8 under a deliberate migration brief; see
`scripts/generate_types.py` for the pin and the resync command.

## The wire

**Types on the bus, JSON only at the edges.** Every demo flow publishes a
generated `idl/v1.7` type on a spec-named topic with its §3.3.3 QoS profile.
Nothing serialises a payload into a string any more; the JSON envelope that
used to carry everything on one unkeyed topic is deleted.

| Spec requirement | Status |
|---|---|
| §3.3.2 typed topics | **Met.** One DDS topic per logical topic, carrying the registered type the announce names |
| §3.3.3 QoS profiles | **Met.** `spatialdds_demo/qos_profiles.py` transcribes the table; every endpoint is created through `typed_transport`, which refuses an unregistered profile name |
| §2.6 keys and instance lifecycle | **Met.** `Announce` is `@key service_id`, so a late joiner gets the current announce of every live service and a dispose evicts exactly one |
| C.5 departure | **Met.** Instance disposed (MUST) and `Depart` published (SHOULD) — the demo does both, because it bridges to MQTT and WebSocket where DDS instance state does not cross |

### Evidence, not assertion

`tests/interop_probe.py` is a participant built from three things — the
types generated from `idl/v1.7`, the spec's topic names, and the §3.3.3
profile table — and from **no demo transport code at all**. If it can talk
to the demo, the spec is what the two have in common.

Both directions pass (`tests/test_interop.py`):

* the probe reads the demo's announces off the well-known keyed topic,
  follows one announce's own `TopicMeta` to a data lane, and reads from it;
* the demo discovers the probe, resolves the type it announces, opens a
  reader, reads its `GeoPose` samples and sees it depart.

The probe also reports any announced type name it cannot resolve. It reports
none: a spec-only implementation can consume every lane this demo publishes.

### What is still not implemented

Honestly, and the distinction matters — these are now *testable* rather than
merely absent:

* **DDS Security binding.** Not configured. Per-topic access control is
  expressible now that there are per-topic endpoints, which it was not
  before; nobody has written the permissions files.
* **XCDR2 cross-vendor.** Untested. The demo is CycloneDDS on both ends. The
  types are XTypes-annotated `@extensibility(APPENDABLE)` and the wire is
  real CDR, so a second vendor is a matter of running one, not of changing
  anything here.
* **Large-blob transport at scale.** `spatial::core::BlobChunk` works and is
  verified moving 200 KiB in 4 chunks with per-chunk CRC, but nothing has
  pushed a real point cloud through it at rate.

## What changed from v1.6

1.7 is a hard cutover, not a compatible bump. The pre-adoption instability
clause (§3.1) lets a MINOR revision break the wire format, and 1.7 does. There
is no dual-version support here and no deprecation path: where the old and new
shapes conflict, only the 1.7 shape is accepted.

### Unified module versioning

1.6's selective per-profile minor bumps are gone. All modules version together
with the spec, so every `MODULE_ID` and `schema_version` here is
`spatial.<profile>/1.7`. The per-profile version table that used to live in this
document is obsolete; there is nothing left to track per profile.

The dual identifier syntax is also retired. `spatial.<profile>/MAJOR.MINOR` is
the only form; `name@MAJOR.MINOR` (e.g. `core@1.6`) is rejected, including in
manifest `profile` strings, which must now match `spatial.manifest/1.<minor>`
with `<minor>` ≥ 7.

### Breaking changes exercised here

| Change | Effect in this demo |
|---|---|
| `CoverageResponse` returns `sequence<ServiceSummary>` | Both responders (bus + HTTP binding) emit compact rows; clients rank on the summary, then resolve `manifest_uri` or read the retained `Announce` |
| `CoverageQuery.expr` removed (and Appendix F.X) | `filter` is the only query form; a body carrying `expr` is rejected, not ignored |
| `CoverageElement.type` removed | Geometry kind derived from `has_bbox` / `has_aabb` / `global` |
| `GeoPose.frame_kind` / `frame_ref` removed | Orientation is *defined* as the local ENU tangent frame at the encoded position |
| `Capabilities.features` is `sequence<string>` | `[{"name": "blob.crc32"}]` → `["blob.crc32"]` |
| `ProfileSupport.preferred` removed; `name` carries the module family | `{"name": "spatial.core", "major": 1, "min_minor": 7, "max_minor": 7}` |
| `Time.sec` widened to int64 | No change needed — JSON integers carry int64, and nothing here clamps to 32 bits |

Also in 1.7, but not exercised here: compound `@key` on `core::Node`/`Edge`
and `mapping::Edge`, `TileMeta`'s single `Aabb3 aabb` in place of
`min_xyz`/`max_xyz`/`lod`, and the removal of `BlobChunk.last`. This demo
publishes no `Node`, `Edge` or `TileMeta`, so those keys are unexercised —
not unavailable. `Announce`'s key *is* exercised, and is what discovery
depends on.

## Discovery flow

- `Announce` + `CoverageQuery` → `CoverageResponse`, with capabilities
  (`ProfileSupport` ranges) and typed topics (`TopicMeta` carrying
  `type` / `version` / `qos_profile`, all mandatory in 1.7).
- Capability advertisements declare `spatial.core/1.7`,
  `spatial.discovery/1.7`, `spatial.sensing.vision/1.7`, `spatial.anchors/1.7`.
- The demo-local `register` / `list` endpoints still traffic in full
  announces: they are registration *inputs*, not `CoverageResponse`.

### The two discovery bindings differ, by design

The DDS and HTTP discovery bindings return different row shapes. That is
intentional:

| | DDS binding (on-bus) | HTTP binding (`/.well-known/spatialdds/search`) |
|---|---|---|
| Row shape | compact `ServiceSummary` | full service manifest (§8.2.3) |
| Envelope | `query_id` + `results` + `next_page_token` | `results` + `next_page_token` |
| Why | the client already holds the service's retained `Announce`, so detail is a local lookup by `service_id` | the client has no bus, so one round trip must carry everything |

A bus client correlates a response to its query with `query_id`, because
several queries may be in flight on a shared reply path. HTTP correlates
request and response itself, so the HTTP envelope carries no `query_id`.

A `ServiceSummary` row is `service_id`, `kind`, `name`, `manifest_uri`,
`coverage`, `coverage_frame_ref`, `stamp` and `ttl_sec`, and carries no `caps`,
`topics` or `transforms`. `validate_service_summary()` rejects rows that do.
Pagination is unchanged on both bindings.

The HTTP binding returns registered manifests as-is. Registered announces are
projected into a manifest: fields the announce provides are carried across, and
optional manifest fields it cannot supply are omitted rather than invented.
Multi-element coverage follows the spec's own manifest idiom, with the canonical
`frame_ref` plus the primary element inlined and an `elements` array when there
is more than one element, or when the primary carries a per-element frame
override that must not be hoisted onto the canonical frame.

### Registered typed topics and QoS profiles

1.7 registered the types and QoS profiles this demo had been using informally.
`spatialdds/vps/query/v1` is `vps_query` / `VPS_REQ`; `spatialdds/vps/result/v1`
is `geopose` / `VPS_RESP` (it was the unregistered `node_geo` before).
`spatialdds_demo/topics.py` carries the full §3.3.2 and §3.3.3 registries.

Anchor deltas are `anchor_delta` / `ANCHOR_DELTA`, both registered. They used
to be deployment-specific extensions (`oarc.anchor_delta`) because 1.7 named
neither; 1.7's findings-batch-2 revision added both, along with twelve other
registry rows and four other QoS profiles that this demo had been working
around. See "Extensions" below for what is left.

Demo topic *names* are unchanged: 1.7 exempts well-known and profile-defined
topics from the application-topic pattern, and reply topics are consumer-chosen.

## Well-known paths

1.7 consolidates to a single RFC 8615 registration,
`/.well-known/spatialdds/{bootstrap,resolver,search}`:

- `search` — the HTTP discovery binding (`ar_demo/http_binding.py`).
- `bootstrap` — new here. Serves a 1.7 bootstrap manifest built from the same
  site table `spatialdds_bootstrap_server.py` uses on the bus, so the HTTPS and
  DDS paths agree. Auth is the optional `auth_hint` string; the `auth` object
  and its `method` enum are gone. `auth_hint` is omitted unless configured,
  rather than advertising a placeholder.
- `resolver` — not served by this binding (it has no manifests of its own to
  resolve), but `spatialdds_demo/manifest_resolver.py` *consumes* it: resolution
  follows §7.5.1 order — cache → advertised resolver → HTTPS fallback → failure.

Because the three names are reserved, direct manifest fetches moved down a
level to `https://{authority}/.well-known/spatialdds/manifests/{path}.json`,
where a manifest path can no longer shadow `bootstrap`/`resolver`/`search`.

`register` and `list` remain demo-local extensions alongside the reserved names.

## Layer 1.5 conformance

The spec's three discovery layers map onto this repo as follows:

| Layer | Where |
|---|---|
| 1 — bootstrap | `GET /.well-known/spatialdds/bootstrap` on both HTTP servers, and the bus bootstrap topic pair |
| 1.5 — HTTP discovery | `POST /.well-known/spatialdds/search` and `GET ...?geohash=` on both HTTP servers |
| 2 — on-bus discovery | `Announce` + `CoverageQuery`/`CoverageResponse` over DDS |

`bridges/web_bridge` is the gateway shape: it answers Layer 1.5 from a live
cache of announces seen on the bus, so a client with no DDS stack can bootstrap,
search, and open a WebSocket stream without SpatialDDS-specific code.
`ar_demo/http_binding.py` answers the same binding over a registry it controls,
which is what makes it useful as a conformance harness. Both call one module,
`spatialdds_demo/discovery_http.py`.

`/.well-known/spatialdds/resolver` is not implemented on either server. The
manifest resolver in `spatialdds_demo/manifest_resolver.py` *consumes* resolver
metadata when an authority publishes it, but neither server publishes its own.

**The GET convenience form.** §3.3.0 specifies it fully — REQUIRED alongside
POST "for interoperability with the Geospatial DNS-SD binding", equivalent to a
POST carrying `{"geohash": "..."}` plus an optional `kind`, identical response
format — so both servers implement it as literally that translation. Nothing
here is guessed, and there is no gap to file.

**Where §3.3.0 and the demo differ, and why.** The HTTP request table is not
the on-bus `CoverageQuery` struct, and three of its details are worth recording
because the spec's own worked examples depend on them:

- `coverage` is marked REQUIRED, yet the binding's minimal example is
  `{"geohash": "9q8yy"}` with no coverage — and the GET form is *defined* as
  equivalent to that body. Read as "coverage or geohash", which is what both
  servers accept.
- `coverage_frame_ref` is absent from the request table and from every example,
  so a conformant client will not send one. Optional here, read as earth-fixed
  when missing.
- The examples write coverage elements without presence flags
  (`{"crs": "EPSG:4326", "bbox": [...]}`). Both servers infer a flag when its
  key is absent, and never over an explicit `false`.

**`service.connection` on a synthesized manifest.** §3.3.0 says clients "MUST
be able to extract `service.connection` from any result and use it to join the
service's DDS domain", while §8.2.3 marks the field OPTIONAL. An `Announce` has
no connection block to carry, so a manifest synthesized from one cannot supply
it, and F.1's rule is to omit rather than invent. Clients here take the domain
and peers from Layer 1 (`bootstrap`) instead, which §7.3 anticipates. Filed
against 1.8; a manifest this deployment *hosts* is returned verbatim and does
carry a connection block.

## Coverage & frames

- `disco.CoverageElement` with explicit presence flags and CRS on earth-fixed
  bboxes; no `type` field.
- Every discovery payload carries a `coverage_frame_ref` (`FrameRef{uuid,fqn}`,
  plus the 1.6 `coord_convention`, ENU throughout) with optional per-element
  overrides.
- Intersection is the full §3.3.4 model: `bbox`, `aabb` and `circle`, unioned
  across an element's geometries and across elements, with `global` matching on
  either side. A circle is approximated by its bounding box, which §3.3.4
  permits, and a geographic circle's metre radius is converted to degrees.
- Elements are compared only within one frame — the element's own `frame_ref`
  when set, `coverage_frame_ref` otherwise, earth-fixed when neither. Resolving
  a transform between frames is a §3.3.4 MAY that is not attempted, so a local
  metric footprint is invisible to a lon/lat query rather than matching it by
  numeric coincidence.
- One implementation, in `spatialdds_demo/discovery_http.py`. The on-bus
  `CoverageQuery` responder and the catalogue server reach it through
  `SpatialDDSValidator.check_coverage_intersection`, so bus matching and HTTP
  matching cannot disagree.
- `Aabb3` keeps `min_xyz` / `max_xyz` — only `TileMeta`'s loose pair was
  removed. The demo publishes no tilesets, so nothing needed folding.
- The `coverage_window_*` fields are still not emitted; see
  `idl/v1.7/discovery.idl`.

## Placing content, and what coverage is not

The catalogue row (`oarc_demo::CatalogEntry`) separates two jobs that an
earlier version of the fountain seed ran together.

- **Coverage answers "would I find this here?"** It is a search key. The row's
  `coverage` is an earth-fixed bbox in degrees, and that is all it is for.
- **`pose` answers "where does it sit, and which way does it face?"** It is
  translation in metres plus a quaternion, in the frame `frame_ref` names.

Conflating them fails quietly. The first fountain seed carried the duck's
altitude in a `CoverageElement.aabb`, whose consumers read all three axes as
metres in the declared frame; the element inherited an earth-fixed frame, so
the field held longitude, latitude and ellipsoidal height. Nothing rejects
that. An intersection test against a real query is then wrong in proportion to
the distance from null island — the same failure family as a frame-scale
error. Coverage has no vertical extent to lend, and the spec has no home for
one; see the gaps note in `directions/`.

**Frames resolve, or a pose is folklore.** A pose in `map/ut-littlefield-fountain`
means nothing to a client that has never heard of that frame. The service
publishing the content announces the frame's transform in
`Announce.transforms` — a `disco::Transform` from the local frame into
earth-fixed, i.e. ECEF metres — and the web bridge serves the union of those at
`GET /v1/frames`. No new topic and no new reader: a frame stops resolving when
its service's announce expires, which is the correct lifetime. Frame identity
is the `FrameRef.uuid`, a UUIDv5 of the fqn, so the row and the transform
cannot drift apart.

**Units.** Nothing in the row states the asset's units, because glTF 2.0 fixes
them: distances are metres. `formats`/`asset.media_type` carrying
`model/gltf-binary` is therefore also the unit declaration. A format that did
not fix its units would need one, and this row could not express it.

**Orientation.** The pose's quaternion is `[x, y, z, w]`, GeoPose order,
rotating the asset into the frame it names. glTF is Y-up and these frames are
Z-up, so a renderer applies the Y-up-to-Z-up conversion first; an identity
quaternion then leaves the asset's axes aligned with the frame's, which for
this duck points its beak east. The row instead carries a quarter turn about
Up (`q = [0, 0, -√½, √½]`) so it faces south, down the Main Mall toward where
visitors arrive — a heading the publisher chose and stated, rather than
whatever a renderer defaults to. Verified by rendering, not by derivation:
the sign of that turn is easy to reason wrong.

**Asset URIs are absolute on the wire.** `AssetRef{uri, media_type, hash}` is
the spec's fetch-plus-integrity contract and the row carries one. A relative
`href` names no base, so it resolves differently for a client that came through
the bridge than for one talking to the catalogue service — the ambiguity the
spec's manifests avoid by being absolute. The authored seed stays relative,
because that is what ports between deployments; the publisher joins it to
`SPATIALDDS_ASSET_BASE` at load. The hash is `sha256:<hex>` over the shipped
bytes, and a test asserts it still matches the file, because a hash that has
drifted is worse than no hash — it claims the bytes were checked.

`href` and `formats` remain for consumers that already read them, and MUST
agree with `asset` when both are present.

## Time & quaternions

- Timestamps are `builtin::Time { sec, nanosec }` with `sec` now int64. JSON
  integers carry that natively and no validator, schema, or JS client here
  bounds `sec` at 2^31.
- GeoPose and PoseSE3 quaternions are `[x,y,z,w]`, normalized before use.

## Sensing & localization demo

- Mock localization responses use `argeo.NodeGeo` with `poses[]` and an
  optional `geopose`.
- Sensor payloads (FrameRefs, BlobRefs, KeyframeFeatures) now carry `/1.7`
  schema versions along with everything else.

## Manifests

- Bundled manifests under `manifests/v1.7/` are demo-flavored
  (`spatial.manifest/1.7`) and are what the demo's parser reads. Upstream
  `vps_manifest.json` examples use the `schema_version: spatial.core/1.7`
  envelope instead; see the SpatialDDS-spec repo for those.
- `manifests/v1.4/` and `manifests/v1.6/`, like `idl/v1.4/` and `idl/v1.6/`, are
  kept for reference only. Nothing loads them.

## Validation

- `spatialdds_validation.py` enforces FrameRef, Time, coverage presence flags,
  CRS rules, unit quaternions, GeoPose shape, and ServiceSummary shape.
- `validate_module_version()` and `validate_manifest_profile()` are hard-cutover
  checks: `/1.5`, `/1.6` and every `@` form are rejected.
- `spatialdds_demo/topics.py::validate_topic_meta()` checks TopicMeta rows
  against the 1.7 registries plus the documented extensions above.

## Extensions: what 1.7 still has no type for

§3.3.2 allows deployment-specific type names and asks that they be
documented. This is that documentation, and it is deliberately short:
everything the demo used to carry an `oarc.*` name for because the *type
system* had a gap now has a registered name. What is left is application
protocol — flows this demo needs and the spec does not describe.

| Extension | Type | Why it exists |
|---|---|---|
| `oarc.fusion_coverage` | `oarc_demo::FusionCoverage` | No type for fusion coverage metrics — a per-operator "no single source could build this" scoreboard, an aggregate diagnostic rather than track content |
| `oarc.catalog_query` | `oarc_demo::CatalogQuery` | No catalogue query/response pair. `ContentAnnounce` advertises content; nothing asks a catalogue what is in an area |
| `oarc.catalog_response` | `oarc_demo::CatalogResponse` | as above |
| `oarc.bootstrap_query` | `oarc_demo::BootstrapQuery` | No bootstrap exchange. A participant is assumed to know its domain id and QoS profile already, which is exactly what a fresh device does not |
| `oarc.bootstrap_response` | `oarc_demo::BootstrapResponse` | as above |
| `oarc.model_entity` | `oarc_model::Entity` | **Demo-local, non-normative.** No world-model layer. The catalogue says what content exists; nothing says what is *there* — entities with identity, type, extent and relationships, pointing at catalogue content when they have an asset |
| `oarc.model_relationship` | `oarc_model::Relationship` | as above — a keyed edge, so it can retire independently of the things it joins |

Five, down from twelve — plus two carrying the world-model prototype, which
is a different kind of entry and is marked as such.

### `oarc_model` — demo-local, candidate for `spatial.model`

`idl/demo/oarc_model.idl` prototypes the Open World Model layer: `Entity` and
`Relationship` on `spatialdds/model/entity/v1` and
`spatialdds/model/relationship/v1`, latched TRANSIENT_LOCAL so a late joiner
gets the whole model without asking. It exists to be argued against a running
demo before anything is proposed for the spec.

**Guard rails, deliberately.** Demo-local module, header comment pointing at
the OWM proposal sketch, no registry row, no `/1.7` identifiers, and no change
to any spec type. Graduation is earned by evidence: informative example first,
provisional only on deployment experience.

**What the two parts surfaced.** One place to cite for all of it. Part 1
recorded; Part 2 either closed or sharpened.

| Finding | Status | Where | Shape of it |
|---|---|---|---|
| Reference-by-id exists, lookup-by-id does not | **closed in Part 2** | below, "A gap this exposed" | `CatalogFilter` gained `content_id_in`, bounded at 16, intersecting with `kind_in` |
| An ephemeral writer's update does not outlive it | **closed in Part 2** | below, "Two writers, one instance" | Writes moved behind a command channel; the owner is the only writer |
| No `ServiceKind` fits a world model | open | below, "Model layer discovery" | The publisher is deliberately silent, so the layer has no discovery story yet |
| `Relationship` is under-specified relative to `Entity` | **new in Part 2** | below, "What an edge cannot say" | No `LifecycleState` and no `basis`: an edge cannot say why it went, or how the claim was arrived at |
| A retirement cascade is a local courtesy, not a rule | **new in Part 2** | below, "What an edge cannot say" | A dangling edge across a federation boundary is valid; one left by a local tool is mess |
| A DECLARED entity audits everything already in the model | **new in Part 3** | below, "Declaring bounds judges what is already there" | Bounds arriving late make existing state right or wrong retroactively; nothing else changed |
| A command channel's type must be keyed | **new in Part 3** | below, "An unkeyed command topic kills its readers" | An exiting client delivers an invalid sample; deserializing the key of an unkeyed type raises inside `take()`, before any user code sees it |
| `test_bridge_http.py` is container-bound | open | `CONTEXT.md`, test state | Writes to `/app/...`, errors on the host, absent from the canonical list. Pre-existing |

The two open `oarc_model` items and the two new ones belong with the
`spatial.model` graduation discussion; the container-bound test is a
housekeeping note so it is not rediscovered.

### Why guarded members are declined rather than defaulted

**Declining is the only reading that cannot be mistaken for obedience.**

`ModelCommand` carries two guarded members -- a pose for `move`, an extent for
`set_extent` -- and a command that arrives without the thing it is meant to
set is refused rather than applied as zeroes. The reason is not tidiness. A
zeroed `Aabb3` is a *well-formed* request to shrink the pond to a point: apply
it and the mover dutifully crowds every duck onto a single coordinate, the bus
is consistent, the client renders it, and the demo looks like it is working.
A zeroed `PoseSE3` is a well-formed request to move a duck to the frame
origin. Neither failure announces itself.

The general shape: **when the null value of a field is inside the domain of
valid requests, absence and zero cannot be distinguished after the fact, so
they must be distinguished at the boundary.** A guard flag makes that
possible; declining on a false guard is what makes it useful. Anything that
defaults instead is choosing the one interpretation guaranteed not to raise.

Sibling to the Part 2 rules -- a typo'd basis must not read as a claim about
the venue, silent truncation must not read as "not found", "moved" must not
be printed when you mean "asked". All four are the same instinct: the failure
mode to design against is the one that looks like success.

### Declaring bounds judges what is already there

**Every entity that declares an extent is implicitly an audit of everything
already in the model.** Nothing about the existing state changes; what changes
is whether it can be described as correct.

Met here at rubber-duck scale. `ent:duck:west` had sat at (6.5, -8.0) since
Part 1, and nothing was wrong with it: no entity said where the water was, so
"on the water" was not a claim anything could contradict. The moment the venue
declared a pond at x 9.5..20, y -10..-18, that duck was on the rim -- not
because it moved, but because something finally said where the edge was.

The seed was corrected before the mover existed, deliberately. A mover reading
the pond's bounds would have found the duck outside them and walked it in on
its first tick, and a demo whose opening move is correcting itself is teaching
that the model is unreliable rather than that it is authoritative. The guard
is `test_every_duck_sits_inside_the_declared_pond`, which fails at seed time
rather than at run time.

At larger scale this is the ordinary case rather than a demo curiosity: a
venue that surveys a boundary, a fusion service that publishes a footprint, a
regulator that declares an exclusion zone -- each one retroactively sorts
existing entities into compliant and not. The model has no opinion about which
of the two claims is wrong, and should not: the pose was published in good
faith and so was the boundary. What a consumer needs is enough information to
decide, which is what `basis` is for. Part 3's second pond meets the same
property one level up, where two services disagree about where the water is
and nothing in the model crowns either.

### An unkeyed command topic kills its readers

**A command channel's type must be keyed, or its readers die when its clients
exit.** Found in Part 3, latent since Part 2 introduced the channel; it needed
only the timing of an invalid sample to fire.

`ModelCommand` was deliberately unkeyed. The reasoning was sound and the
consequence was not: a command is an event rather than state, and no instance
here has a lifetime worth naming. But operator tools are short-lived
processes. When one exits, its writer unregisters and the middleware delivers
an **invalid sample** to every reader -- data absent, key blob present -- and
deserializing the key of a type that has none reads a length prefix out of an
empty buffer:

```
struct.error: unpack_from requires a buffer of at least 8 bytes for
unpacking 4 bytes at offset 4 (actual buffer size is 4)
    ... in take_samples -> deserialize_key
```

The model service died there, holding the whole world, because somebody ran
`move_duck.py`. A reader **cannot filter what it cannot decode**: the
exception is raised inside `take()` before any of our code sees the sample, so
no amount of care in the read loop avoids it. The fix has to be at the type.
`@key string command_id` costs nothing -- VOLATILE keeps no per-instance
history -- and a request id is a thing a request should have anyway.

**The companion rule, which the same bug also demonstrates.** The read loop
had its `take()` outside its `try`, so one undecodable sample ended the
process. A reader loop must treat its lane as untrusted traffic: take inside
the try, report the first failure and every hundredth, keep serving. The
bridge's `_StreamPump` and `_ModelPump` had already learned this separately;
the command lane was the third place it was needed and the first place it was
load-bearing, because the thing that died was the authority rather than a
mirror.

Guarded by `tests/test_command_lane.py`, which runs four short-lived writers
past one long-lived reader -- the shape of every operator-tool invocation --
and asserts the reader survives. Removing the key turns it red.

### What an edge cannot say

A design note for R3, and two halves of one problem.

**`Relationship` is under-specified relative to `Entity`, twice over.** An
entity carries `LifecycleState` and `state_reason`, so it can retire and say
why; and `basis`, so a consumer can tell an observation from an assertion. A
relationship carries neither. The IDL comment above it used to claim an edge
"can retire independently of the things it joins" -- it cannot retire at all.
It can only be disposed, which is a claim that it is gone and nothing more.

That asymmetry is visible in the demo: retiring `ent:duck:east` publishes a
tombstone carrying "taken in for the winter", and then silently disposes
`rel:contains:east`. Anyone watching learns why the duck left and nothing
about why the edge did. Whether edges should gain both fields, one, or neither
is a question for the sketch (§11), not something the demo should decide by
adding fields to a type it does not own.

**And the cascade itself is a courtesy, not a rule.** When an entity retires,
the operator tool disposes every edge incident to it -- in one action, from
the authority that owns them. This is deliberately *not* proposed as protocol
behaviour. A dangling edge pointing at an entity nobody in your federation
publishes is the ordinary federated case and perfectly valid: you may hold an
edge to something you cannot see. An edge left dangling by a tool that had
both ends in its own hands is just mess. The rule the demo follows is
therefore about tools, not about the model: *clean up what you can reach.*

The naive-retirement counter-example makes the distinction concrete. Run from
a writer that owned nothing, it disposed the entity in the bridge's cache and
left `rel:contains:west` pointing at it -- a local dangle produced locally,
which is exactly what the cascade exists to prevent.

What it separates is asset from instance. A catalogue row is an asset — one
`duck.glb`, one checksum, one URI. Entities are the things in the world, and
several may render from the same row, which a catalogue carrying its own pose
cannot express. Placement moves to the entity; the catalogue keeps the asset.

**Model layer discovery: nothing announces it.** The publisher is deliberately
silent, so a client has to know the two topics exist. That is fine on a mocked
demo where the client is written alongside the publisher, and wrong as an end
state — discovery is how everything else on this bus is found.

The cause is not laziness: `ServiceKind` has no value that fits. A world model
is not `SENSING` (it produces no sensor data), not `FUSION` (it fuses no
inputs), and not `CONTENT` as the demo already uses it (that is the catalogue,
which serves assets). `OTHER` would technically pass and would tell a consumer
nothing. Announcing under a wrong kind is worse than not announcing: it puts a
service in everyone's discovery results under a label that misdescribes it,
and existing clients filtering by kind would either miss it or mis-handle it.

So Part 1 stays silent, and the announce-kind question travels with the
`spatial.model` graduation discussion rather than being settled by whichever
enum value was least inconvenient.

### Two writers, one instance

`move_duck.py` writes an update for an entity the publisher also writes, from
a separate short-lived process. Two consequences, both measured:

**A fresh reader sees the seed, not the move.** TRANSIENT_LOCAL durability is
scoped to the writer that published the sample. The mover writes, exits, and
its history goes with it, while the long-lived publisher's original sample
stays latched. So a client connected at the time sees the move, the bridge's
cache sees the move, and a client joining afterwards is handed the original
pose. Tested three times, consistently. That undercuts the late-join
guarantee the moment anything moves.

It also means "where is the duck now" has two answers: the mover, reading the
bus, gets the publisher's seed; `/v1/model`, reading the bridge's cache, gets
the accumulated result. Relative moves computed from one and applied against
the other drift apart -- which is exactly how the live test walked a duck from
(16.5, -10.5) to (40.5, -34.5) over eight runs before it was made absolute.

**An update published shortly after a subscriber attaches is sometimes not
delivered to it** -- roughly one run in three. The socket is open, the client
has logged that it is watching, the writer reports success, and nothing
arrives; a second publish of the same sample always lands. The live test
republishes once and says so when it has to, rather than hiding it.

**Closed in Part 2, both of them.** The publisher owns the state it latches,
and nothing writes to the model topics but the publisher that owns them.
`move_duck.py` and `retire_entity.py` publish a `ModelCommand` on
`spatialdds/model/command/v1` (RELIABLE, VOLATILE, KEEP_LAST(16), unkeyed --
a command is an event, not state) and the service applies it. An operator tool
asks the authority; it does not race it.

Measured after the change: move `ent:duck:west` to (3.00, -19.00), and a fresh
reader and the bridge cache now agree. Before it, they did not -- fresh reader
(6.50, -8.00), cache (3.00, -19.00), one duck with two positions.

Two things this is worth knowing for:

**The bug had been made invisible without being made false.** An earlier fix
had stabilised the flaky live-move test by resetting the venue before each
run. That was correct, and it meant no test ever asked what a reader joining
*afterwards* would see -- which is the only question a writer-scoped-durability
bug answers wrongly. Resetting before each test is precisely how a durability
bug hides from a deterministic suite. The guard added afterwards says so in
its name: `test_a_move_outlives_the_tool_that_asked_for_it`.

**The counter-example is kept runnable**, at
`directions/p2-acceptance/naive_retire.py`. It retires an entity the obvious
way -- read it, write `state = RETIRED`, dispose, exit -- and a fresh reader
gets the duck back. A paragraph explaining why the indirection exists is worth
less than a script that demonstrates the alternative failing.

The second symptom (one update in three not reaching a just-attached
subscriber) is unchanged: it is a discovery race, not a durability one, and
both operator tools now wait for `publication_matched` before writing rather
than publishing into a lane with nobody on it.

**A gap this exposed.** `content_refs` uses `catalog:<content_id>` to point at
a catalogue row, and the demo's catalogue has no way to answer it: `CatalogQuery`
filters on coverage and `kind_in` only, so **reference-by-id exists and
lookup-by-id does not**. A client can resolve the reference only if it has
already coverage-queried the right area and cached the result. That is fine for
one row in one plaza and wrong at any scale — the reference would be unusable
by a client that knows the id and not the place.

**Closed in Part 2.** `CatalogFilter` gained `content_id_in`: bounded at 16
per query, intersecting with `kind_in` rather than overriding it, and answered
without requiring a coverage box, because a client resolving a reference may
not know where the thing is. The client tries its cached results first and
queries by id on a miss, still declining to render when both fail rather than
inventing a URI.

The demo could not previously tell the two paths apart — the duck's catalogue
row and the duck's entity are in the same plaza, so the cached lookup always
hit and reference-by-id was never actually exercised. A `?noassetcache=1`
switch was added to force it, then retired in Part 3: once the model bootstrap
ran before any coverage query there was no cache left to hit, and every
reference resolves by id on every page load. The path is now load-bearing
rather than demonstrable -- the ducks cannot render without it.

Five, down from twelve. The seven that went away, and what replaced them:

| Was | Now | Added by |
|---|---|---|
| `oarc.detection3d_velocity` (`DetectionWithVelocity` wrapping `Detection3D`) | `detection3d` — `Detection3D` has `has_velocity`/`velocity` | 1.7 findings batch 2 |
| `oarc.framed_pose` | `framed_pose` | 1.7 findings batch 2 |
| `oarc.detection2d_set` (demo-local `Detection2D`/`BBox2D`) | `detection2d` — `semantics::Detection2DSet` | 1.7 findings batch 2 |
| `oarc.lidar_frame`, `oarc.lidar_meta`, `oarc.radar_tensor_meta`, `oarc.video_frame_meta`, `oarc.rf_beam_meta`, `oarc.imu_sample`, `oarc.anchor_delta` | the same names without the prefix | 1.7 findings batch 2 |
| `oarc.blob_chunk` (demo-local `BlobChunk` at a 65535 bound) | `spatial::core::BlobChunk` — the spec's bound is 65535 now | 1.7 findings batch 2 |
| VPS request/response (`oarc.vps_response`, and demo `VpsRequest`/`QualityRequirements`/`LocalizeQuality`) | `vps_query` / `vps_response` — `argeo::VpsRequest` / `VpsResponse` / `QualityRequirements` / `VpsStatus`. Query imagery rides `query_blobs` (`BlobRef`); result rides `NodeGeo` | 1.7 batch 3 |
| `oarc.fused_track` (`oarc_demo::FusedTrackSet`) | `fused_track` — `semantics::FusedTrackSet` on `DET_RT` | 1.7 batch 3 |

The VPS and fusion flows are now on spec types end-to-end — request/response,
publisher, bridges and the interop probe all name the registered types. The
fused track's per-operator detection provenance, deliberately excluded from
`semantics::FusedTrack`, is carried by `core::EntityBinding`: the fusion service
publishes one binding per track linking its `track_id` to each contributing
operator's most-recent detection, and a consumer rebuilds the old inline map by
joining `FusedTrackSet` with `EntityBinding` on `track_id`.

Each removed extension was raised as a finding from building this demo; see
`directions/spatialdds-1.7-review-submission.md` for the full list and how
each was resolved.

## Where the spec and DDS still do not line up

Three, all flagged rather than quietly resolved:

* **Ordering is "Ordered" for every profile.** DDS orders per instance by
  default (`DestinationOrder` BY_RECEPTION_TIMESTAMP), so no policy is set;
  the spec's column describes the default rather than requesting
  BY_SOURCE_TIMESTAMP.
* **Deadline is request/offered, and silently so.** A reader requesting
  33 ms does not match a writer offering none — no error, no warning, no
  data. The §3.3.3 table's deadline column is normative for that reason, and
  `tests/test_interop.py::DeadlineIsLoadBearing` pins the behaviour so it
  cannot be rediscovered the hard way.
* **EntityBinding removal is unspecified — filed against 1.8.**
  `core::EntityBinding` is `@key entity_id`, RELIABLE + TRANSIENT_LOCAL,
  update-in-place. The spec says how a binding is created and refreshed and is
  silent on how one is *removed* when the entity it correlates is gone:
  dispose, TTL, and a tombstone sample are all defensible and they are not
  interchangeable to a consumer.

  1.7 is stamped, so this cannot be a 1.7 patch — it is a 1.8 item. The demo
  publishes bindings and lets the reader's KEEP_LAST hold the latest per key;
  it does not invent a removal convention, because inventing one is how two
  implementations end up disagreeing silently.
