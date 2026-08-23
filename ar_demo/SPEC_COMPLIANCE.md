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
| 1.5 — HTTP discovery | `POST /.well-known/spatialdds/search` (plus the `?geohash=` shorthand) on both HTTP servers |
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

## Coverage & frames

- `disco.CoverageElement` with explicit presence flags and CRS on earth-fixed
  bboxes; no `type` field.
- Every discovery payload carries a `coverage_frame_ref` (`FrameRef{uuid,fqn}`,
  plus the 1.6 `coord_convention`, ENU throughout) with optional per-element
  overrides.
- Intersection checks honour the 2D `[west,south,east,north]` ordering.
- `Aabb3` keeps `min_xyz` / `max_xyz` — only `TileMeta`'s loose pair was
  removed. The demo publishes no tilesets, so nothing needed folding.
- The `coverage_window_*` fields are still not emitted; see
  `idl/v1.7/discovery.idl`.

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
