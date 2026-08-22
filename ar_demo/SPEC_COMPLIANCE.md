# SpatialDDS v1.7 Compliance Notes

**Status:** ✅ Aligned with v1.7 draft profiles
**Date:** 2026-08-22

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

Also in 1.7, but not exercised here: compound `@key` on `core::Node`/`Edge` and
`mapping::Edge`, `TileMeta`'s single `Aabb3 aabb` in place of
`min_xyz`/`max_xyz`/`lod`, and the removal of `BlobChunk.last`. The envelope
transport ships JSON payloads rather than spec-typed DDS instances, so nothing
here relies on keyed-instance semantics.

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

Anchor deltas have no registered type or QoS profile in 1.7, so
`spatialdds/anchors/<zone>/delta/v1` uses deployment-specific extensions —
type `oarc.anchor_delta`, QoS profile `ANCHOR_DELTA` — following the
`myorg.depth_frame` / `DEPTH_LIVE` naming pattern §3.3.2 recommends. Force-fitting
them onto `map_event` / `MAP_META` would have misrepresented what they are.

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
