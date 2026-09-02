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

What it separates is asset from instance. A catalogue row is an asset — one
`duck.glb`, one checksum, one URI. Entities are the things in the world, and
several may render from the same row, which a catalogue carrying its own pose
cannot express. Placement moves to the entity; the catalogue keeps the asset.

**A gap this exposed.** `content_refs` uses `catalog:<content_id>` to point at
a catalogue row, and the demo's catalogue has no way to answer it: `CatalogQuery`
filters on coverage and `kind_in` only, so **reference-by-id exists and
lookup-by-id does not**. A client can resolve the reference only if it has
already coverage-queried the right area and cached the result. That is fine for
one row in one plaza and wrong at any scale — the reference would be unusable
by a client that knows the id and not the place. Recorded here rather than
fixed: the catalogue is demo protocol, and adding an id lane is a protocol
decision, not a bug fix.

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
