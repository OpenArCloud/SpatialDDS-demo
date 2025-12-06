# SpatialDDS v1.4 Compliance Notes

**Status:** ✅ Aligned with v1.4 draft profiles  
**Date:** 2025-10-05  

## Coverage & Frames
- Uses `disco.CoverageElement` with explicit presence flags (`has_bbox` / `has_aabb`) and CRS on earth-fixed bboxes.
- All discovery payloads carry a `coverage_frame_ref` (`FrameRef{uuid,fqn}`) with optional per-element overrides.
- Intersection checks honor the 2D `[west,south,east,north]` bbox rule for earth-fixed frames.

## Time & Quaternions
- All timestamps use `builtin::Time { sec, nanosec }`.
- GeoPose and PoseSE3 quaternions follow the 1.4 GeoPose order `[x,y,z,w]` and are normalized before use.

## Discovery Flow
- Service discovery now uses `Announce` + `CoverageQuery`/`CoverageResponse` with capabilities (`ProfileSupport` ranges) and typed topics (`TopicMeta` with `type/version/qos_profile`).
- HTTP binding mirrors the same shapes for registration and search.

## Sensing & Localization Demo
- Mock localization response uses `core.GeoPose` wrapped in `argeo.NodeGeo` to stay within defined 1.4 types.
- Sensor payloads reference 1.4 vision and SLAM frontend structures (FrameRefs, BlobRefs, KeyframeFeatures).

## Manifests
- Bundled manifests under `manifests/v1.4/` match the upstream spec (VPS, anchors, mapping, content experience) and are used in tests/HTTP examples.

## Validation
- `spatialdds_validation.py` enforces FrameRef, Time, coverage presence flags, CRS rules, and unit quaternions.
- Helpers include deterministic `FrameRef` creation, GeoPose samples, and bbox intersection checks for discovery filtering.
