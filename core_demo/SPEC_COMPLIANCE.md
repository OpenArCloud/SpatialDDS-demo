# SpatialDDS v1.6 Compliance Notes

**Status:** ✅ Aligned with v1.6 draft profiles
**Date:** 2026-04-30

## What changed from v1.5

Per the upstream v1.6 CHANGELOG, this is a deliberately backward-compatible release with selective per-profile minor bumps — only profiles whose IDL changed move to `/1.6`:

| Profile | Version | Notes |
|---|---|---|
| `spatial.core` | bumped to **1.6** | Adds `PlannedTrajectory`, `PlannedWaypoint`, `EntityBinding`, `ComponentRef` (not exercised by this demo) |
| `spatial.discovery` | bumped to **1.6** | Adds `CoverageElement.has_coverage_window` + `coverage_window_start`/`end` (optional, not exercised) |
| `spatial.sensing.common` | bumped to **1.6** | Adds `COV_ROT3`, `COV_POSE6_TWIST6`, `Mat12x12` (demo uses `COV_NONE` only) |
| `spatial.types` | bumped to **1.6** | |
| `spatial.anchors`, `spatial.sensing.{vision,lidar,rad}`, `spatial.argeo`, `spatial.events`, `spatial.mapping`, `spatial.semantics`, `spatial.slam_frontend`, `spatial.vio` | stay at **1.5** | No IDL change |

Also tightened in 1.6 (no code-breaking shape changes):
- Spatial privacy, enum serialization, time semantics, bbox ordering, schema-stability signaling
- `CoverageQuery.expr` deprecation **scheduled for removal in 2.0** (still legal in 1.6)
- Topic version stability: `/v1` follows profile MAJOR, not MINOR — so `spatialdds/envelope/v1` stays `v1`

## Coverage & Frames
- Uses `disco.CoverageElement` with explicit presence flags (`has_bbox` / `has_aabb`) and CRS on earth-fixed bboxes.
- All discovery payloads carry a `coverage_frame_ref` (`FrameRef{uuid,fqn}`) with optional per-element overrides.
- Intersection checks honor the 2D `[west,south,east,north]` bbox rule for earth-fixed frames.
- The 1.6 `coverage_window_*` fields are not currently emitted; see `idl/v1.6/discovery.idl`.

## Time & Quaternions
- All timestamps use `builtin::Time { sec, nanosec }`.
- GeoPose and PoseSE3 quaternions follow the 1.6 GeoPose order `[x,y,z,w]` and are normalized before use.

## Discovery Flow
- Service discovery uses `Announce` + `CoverageQuery`/`CoverageResponse` with capabilities (`ProfileSupport` ranges) and typed topics (`TopicMeta` with `type/version/qos_profile`).
- Capability advertisements declare `core@1.6`, `discovery@1.6`, plus `sensing.vision@1.5` and `anchors@1.5` per the per-profile rule.
- HTTP binding mirrors the same shapes for registration and search.

## Sensing & Localization Demo
- Mock localization response uses `argeo.NodeGeo` with `poses[]` + optional `geopose` per 1.6 IDL.
- Sensor payloads reference vision and SLAM frontend structures (FrameRefs, BlobRefs, KeyframeFeatures) at their `1.5` versions (these profiles did not bump in 1.6).

## Manifests
- Bundled manifests under `manifests/v1.6/` are demo-flavored (`spatial.manifest@1.6`) used by the demo's parser. Upstream `vps_manifest.json` examples use a different schema (`schema_version: spatial.core/1.6`); see the SpatialDDS-spec repo for those.

## Validation
- `spatialdds_validation.py` enforces FrameRef, Time, coverage presence flags, CRS rules, and unit quaternions.
- Helpers include deterministic `FrameRef` creation, GeoPose samples, and bbox intersection checks for discovery filtering.
