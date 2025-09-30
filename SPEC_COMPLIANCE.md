# SpatialDDS v1.3 Specification Compliance

**Status:** ✅ FULLY COMPLIANT
**Date:** 2025-09-30
**Version:** 1.3 Final

---

## Summary of Spec-Compliant Changes

All feedback from spec review has been implemented. The implementation now strictly follows the v1.3 specification.

### 1. ✅ Earth-Fixed Bbox: 2D Format

**Issue:** Was sending 6 values `[west, south, min_h, east, north, max_h]`
**Spec:** Earth-fixed bbox must be 2D `[west, south, east, north]` in degrees

**Fixed:**
```json
// Before (INCORRECT - 6 values with height)
"bbox": [-122.52, 37.7, 0, -122.35, 37.85, 200]

// After (CORRECT - 2D degrees only)
"bbox": [-122.52, 37.70, -122.35, 37.85]
```

**Changes:**
- Updated `create_coverage_bbox_earth_fixed()` to accept only 4 parameters: `west, south, east, north`
- Added validation to enforce 4-value bbox for earth-fixed frame
- Updated all test code to use 2D format
- Local frames can still use 6-value 3D bboxes

### 2. ✅ Earth-Fixed Poses: GeoPose Format

**Issue:** Was using `{x,y,z}` meters with `frame: "earth-fixed"` (inconsistent)
**Spec:** Earth-fixed must use GeoPose format `{lat, lon, h, q_wxyz}` in degrees and meters

**Fixed:**
```json
// Before (INCORRECT - meters with earth-fixed)
"estimated_pose": {
  "position": { "x": -0.534, "y": 2.800, "z": 4.692 },
  "orientation": { "x": 0.055, "y": -0.029, "z": -0.643, "w": 0.921 },
  "orientation_wxyz": [0.8186, 0.0491, -0.0260, -0.5716],
  "frame": "earth-fixed"
}

// After (CORRECT - GeoPose with lat/lon/h)
"estimated_geopose": {
  "lat": 37.7792502,
  "lon": -122.4240220,
  "h": 38.18,
  "q_wxyz": [0.9734, 0.0401, -0.0137, -0.2252]
},
"pose_frame": "earth-fixed"
```

**Changes:**
- VPS response now returns `estimated_geopose` instead of `estimated_pose`
- GeoPose includes: `lat` (degrees), `lon` (degrees), `h` (meters), `q_wxyz`
- Added `pose_frame` field for explicit frame declaration
- Anchor updates use same GeoPose format
- Removed xyz position format for earth-fixed

### 3. ✅ Quaternion: Single Canonical Format

**Issue:** Was duplicating quaternion in both `{x,y,z,w}` and `[w,x,y,z]` formats
**Spec:** Keep only `q_wxyz` in `[w,x,y,z]` order (GeoPose order)

**Fixed:**
```json
// Before (INCORRECT - duplicate formats)
"orientation": { "x": 0.055, "y": -0.029, "z": -0.643, "w": 0.921 },
"orientation_wxyz": [0.8186, 0.0491, -0.0260, -0.5716]

// After (CORRECT - single canonical format)
"q_wxyz": [0.9734, 0.0401, -0.0137, -0.2252]
```

**Changes:**
- Removed `orientation` dict format
- Use only `q_wxyz` array in [w,x,y,z] order
- All quaternions automatically normalized to unit-norm
- Consistent across GeoPose, anchors, and all poses

### 4. ✅ Discovery Response: Clear Structure

**Issue:** Wrapper used generic `results[]` field name
**Spec:** Use `announces[]` to clearly indicate ContentAnnounce objects

**Fixed:**
```json
// Before (UNCLEAR)
"CONTENT_QUERY_RESPONSE": {
  "results": [ { ...announce... } ],
  "count": 1
}

// After (CLEAR)
"CONTENT_QUERY_RESULT": {
  "announces": [ { ...announce... } ],
  "count": 1
}
```

**Changes:**
- Renamed message type: `CONTENT_QUERY_RESPONSE` → `CONTENT_QUERY_RESULT`
- Renamed field: `results` → `announces`
- Clearly indicates array contains ContentAnnounce objects
- Matches HTTP response shape from spec

---

## Current Output Examples

### Earth-Fixed Coverage (2D Bbox)
```json
{
  "coverage": {
    "elements": [{
      "type": "bbox",
      "frame": "earth-fixed",
      "crs": "EPSG:4979",
      "bbox": [-122.52, 37.70, -122.35, 37.85]
    }]
  }
}
```

### GeoPose (Earth-Fixed Frame)
```json
{
  "estimated_geopose": {
    "lat": 37.7792502,
    "lon": -122.4240220,
    "h": 38.18,
    "q_wxyz": [0.9734, 0.0401, -0.0137, -0.2252]
  },
  "pose_frame": "earth-fixed"
}
```

### Anchor with GeoPose
```json
{
  "anchor_id": "eb5b58f2-e248-44ca-891b-10c1fab5d63f",
  "self_uri": "spatialdds://vps.example.com/zone:sf-downtown/anchor:eb5b58f2-e248-44ca-891b-10c1fab5d63f",
  "rtype": "anchor",
  "anchor_type": "visual_landmark",
  "geopose": {
    "lat": 37.77064671,
    "lon": -122.42034097,
    "h": 2.57,
    "q_wxyz": [0.7262, 0.0396, -0.0235, 0.6859]
  },
  "pose_frame": "earth-fixed"
}
```

### ContentQuery Result
```json
{
  "query_id": "a5520b56-2113-4b68-bd22-82aa9055500f",
  "announces": [
    {
      "content_id": "6f142cfb-064f-4380-a8df-ed96e367bc86",
      "self_uri": "spatialdds://vps.example.com/zone:sf-downtown/service:...",
      "rtype": "service",
      "coverage": { "elements": [...] }
    }
  ],
  "count": 1,
  "timestamp": 1759212318184
}
```

---

## Validation

### Automated Validation
All formats are validated automatically via `spatialdds_validation.py`:

- ✅ Earth-fixed bbox must have exactly 4 values
- ✅ Quaternions validated for unit-norm
- ✅ URIs validated against pattern
- ✅ Coverage elements validated for required fields

### Test Results
```bash
$ python3 spatialdds_test.py

✅ Localization successful!
   GeoPose: lat=37.7792502°, lon=-122.4240220°, h=38.18m
   Quaternion: [0.9734, 0.0401, -0.0137, -0.2252]
   Confidence: 0.853
   Accuracy: 0.051m
   Features: 10 points
```

### Message Summary
```
Total Messages: 6
Message Types:
  CONTENT_ANNOUNCE       1 messages,   1166 bytes  ✅
  CONTENT_QUERY          1 messages,    491 bytes  ✅
  CONTENT_QUERY_RESULT   1 messages,   1275 bytes  ✅
  VPS_REQUEST            1 messages,   1417 bytes  ✅
  VPS_RESPONSE           1 messages,   2400 bytes  ✅
  ANCHOR_UPDATE          1 messages,    772 bytes  ✅
```

---

## Files Updated

1. **spatialdds_validation.py**
   - Updated `create_coverage_bbox_earth_fixed()` to 2D format
   - Added earth-fixed bbox validation (must be 4 values)
   - Updated intersection detection for 2D format

2. **spatialdds_test.py**
   - Changed to GeoPose format for earth-fixed poses
   - Removed duplicate orientation, kept only q_wxyz
   - Renamed discovery response to CONTENT_QUERY_RESULT
   - Renamed `results` to `announces`
   - Updated all coverage calls to 2D format

3. **http_binding.py**
   - Renamed `results` to `announces` in response

---

## Compliance Checklist

- [x] Earth-fixed bbox is 2D `[west, south, east, north]` in degrees
- [x] Earth-fixed poses use GeoPose `{lat, lon, h, q_wxyz}`
- [x] Single canonical quaternion format `q_wxyz` [w,x,y,z]
- [x] Discovery response uses `announces[]` field
- [x] All URIs follow `spatialdds://` format
- [x] Coverage elements include `frame` and `crs`
- [x] Quaternions are unit-normalized
- [x] Message types clearly named
- [x] HTTP binding matches DDS semantics
- [x] All tests passing

---

## References

- [SpatialDDS v1.3 Specification](https://github.com/OpenArCloud/SpatialDDS-spec/blob/main/SpatialDDS-1.3-full.md)
- [GeoPose Standard](https://www.ogc.org/standards/geopose)
- [EPSG:4979 CRS](https://epsg.io/4979) - WGS 84 with ellipsoidal height

---

---

## Round 2 Refinements (2025-09-30)

### 5. ✅ Slim Announce Payload: `bounds` Field

**Issue:** Using full `coverage` block with `elements` array in ContentAnnounce
**Spec:** Use slim `bounds` field (single CoverageElement) to match HTTP example

**Fixed:**
```json
// Before (VERBOSE - full coverage block)
"coverage": {
  "elements": [{
    "type": "bbox",
    "frame": "earth-fixed",
    "crs": "EPSG:4979",
    "bbox": [-122.52, 37.70, -122.35, 37.85]
  }]
}

// After (SLIM - single CoverageElement)
"bounds": {
  "type": "bbox",
  "frame": "earth-fixed",
  "crs": "EPSG:4979",
  "bbox": [-122.52, 37.70, -122.35, 37.85]
}
```

**Changes:**
- ContentAnnounce uses `bounds` (single CoverageElement) instead of `coverage` (array wrapper)
- Reduces payload size by ~50 bytes per announce
- Matches v1.3 spec's HTTP example format
- HTTP binding accepts both `bounds` (preferred) and `coverage` (legacy)

### 6. ✅ Canonical Identifiers: URI Over UUID

**Issue:** Duplicate identifiers: `content_id` UUID and `self_uri`
**Spec:** Treat `self_uri` as canonical, mark UUIDs as legacy

**Fixed:**
```json
// Before (DUPLICATE IDs)
"content_id": "7d2d...f053",
"self_uri": "spatialdds://vps.example.com/zone:sf-downtown/service:7d2d...f053",
"service_id": "7d2d...f053"

// After (URI CANONICAL, UUID LEGACY)
"_legacy_id": "7d2d...f053",
"self_uri": "spatialdds://vps.example.com/zone:sf-downtown/service:7d2d...f053"
```

**Changes:**
- All UUID fields renamed to `_legacy_*` prefix
- `self_uri` is the canonical identifier
- Affected fields:
  - `content_id` → `_legacy_id`
  - `service_id` → `_legacy_service_id`
  - `anchor_id` → `_legacy_anchor_id`
  - `client_id` → `_legacy_client_id`
  - `created_by` → `_legacy_created_by`
- HTTP binding uses `self_uri` for deduplication

### 7. ✅ Timestamp Readability: ISO8601 Mirror

**Issue:** Only epoch milliseconds (hard to read in logs)
**Spec:** Add ISO8601 mirror field for human readability

**Fixed:**
```json
// Before (ONLY EPOCH MS)
"timestamp": 1759241314245

// After (BOTH FORMATS)
"timestamp": 1759241314245,
"stamp": "2025-09-30T14:08:34.245648Z"
```

**Changes:**
- Added `stamp` field in ISO8601 format (`YYYY-MM-DDTHH:MM:SS.ffffffZ`)
- Uses timezone-aware UTC timestamps (`datetime.now(timezone.utc)`)
- Applied to all messages:
  - ContentAnnounce: `stamp`
  - ContentQuery/Result: `stamp`
  - VPS Request/Response: `stamp`
  - Anchor Update: `created_stamp`, `last_seen_stamp`
- Fixed Python deprecation warnings for `datetime.utcnow()`

### 8. ✅ Provider ID Redundancy: Documented

**Issue:** `provider_id` duplicates authority part of `self_uri`

**Analysis:**
```json
"provider_id": "vps.example.com",  // Duplicates authority
"self_uri": "spatialdds://vps.example.com/zone:sf-downtown/service:..."
                          ^^^^^^^^^^^^^^^^ (same value)
```

**Decision:**
- Kept `provider_id` for routing/filtering convenience
- Added inline comment documenting redundancy
- Can be removed in future if not used

---

## Updated Output Examples

### Slim ContentAnnounce (v1.3 Refined)
```json
{
  "_legacy_id": "ea5d10de-1ff2-414d-b041-6633c25031a6",
  "self_uri": "spatialdds://vps.example.com/zone:sf-downtown/service:ea5d10de-...",
  "provider_id": "vps.example.com",
  "rtype": "service",
  "title": "MockVPS-c25031a6",
  "bounds": {
    "type": "bbox",
    "frame": "earth-fixed",
    "crs": "EPSG:4979",
    "bbox": [-122.52, 37.7, -122.35, 37.85]
  },
  "timestamp": 1759241314245,
  "stamp": "2025-09-30T14:08:34.245648Z"
}
```

### VPS Response with Timestamps
```json
{
  "request_id": "7b00498c-4473-4355-8c00-2c5d9298739c",
  "request_uri": "spatialdds://client.example.com/zone:sf-client/request:...",
  "_legacy_service_id": "d2035a7b-a268-4087-87a0-ef9fbaf93d40",
  "service_uri": "spatialdds://vps.example.com/zone:sf-downtown/service:...",
  "timestamp": 1759241336648,
  "stamp": "2025-09-30T14:08:56.648372Z",
  "estimated_geopose": {
    "lat": 37.783813,
    "lon": -122.426771,
    "h": 91.967,
    "q_wxyz": [0.9965, -0.0833, -0.0060, 0.0076]
  },
  "pose_frame": "earth-fixed"
}
```

### Anchor with Legacy IDs
```json
{
  "_legacy_anchor_id": "169b124c-383e-4b5b-9f50-038a01bf1ee2",
  "self_uri": "spatialdds://vps.example.com/zone:sf-downtown/anchor:169b124c-...",
  "rtype": "anchor",
  "geopose": {
    "lat": 37.776963,
    "lon": -122.410052,
    "h": 60.282,
    "q_wxyz": [0.9272, 0.0662, -0.0513, -0.3651]
  },
  "pose_frame": "earth-fixed",
  "metadata": {
    "_legacy_created_by": "b7fc2e20-acd6-48d4-a486-8cbe8efe6104",
    "created_by_uri": "spatialdds://client.example.com/zone:sf-client/client:..."
  },
  "created_timestamp": 1759241349092,
  "created_stamp": "2025-09-30T14:09:09.092106Z",
  "last_seen_timestamp": 1759241349092,
  "last_seen_stamp": "2025-09-30T14:09:09.092106Z"
}
```

---

---

## Round 3 Final Refinements (2025-09-30)

### 9. ✅ Query Volume: Single CoverageElement

**Issue:** ContentQuery `volume` using array wrapper `{elements: [...]}`
**Spec:** Query should carry single CoverageElement under `volume`

**Fixed:**
```json
// Before (ARRAY WRAPPER)
"volume": {
  "elements": [{
    "type": "bbox",
    "frame": "earth-fixed",
    "crs": "EPSG:4979",
    "bbox": [-122.45, 37.75, -122.40, 37.80]
  }]
}

// After (SINGLE ELEMENT)
"volume": {
  "type": "bbox",
  "frame": "earth-fixed",
  "crs": "EPSG:4979",
  "bbox": [-122.45, 37.75, -122.40, 37.80]
}
```

**Changes:**
- ContentQuery `volume` is now single CoverageElement (not array)
- Matches spec/examples exactly
- HTTP binding accepts both formats (legacy compatibility)
- Reduced payload: 531 → 515 bytes (-16 bytes, -3%)

### 10. ✅ Removed Redundant `pose_frame`

**Issue:** `pose_frame: "earth-fixed"` is redundant when using GeoPose
**Spec:** GeoPose inherently implies earth-fixed frame

**Fixed:**
```json
// Before (REDUNDANT)
"estimated_geopose": {
  "lat": 37.7792502,
  "lon": -122.4240220,
  "h": 38.18,
  "q_wxyz": [0.9734, 0.0401, -0.0137, -0.2252]
},
"pose_frame": "earth-fixed"  // ← redundant

// After (IMPLICIT)
"estimated_geopose": {
  "lat": 37.7792502,
  "lon": -122.4240220,
  "h": 38.18,
  "q_wxyz": [0.9734, 0.0401, -0.0137, -0.2252]
}
// GeoPose implies earth-fixed frame
```

**Changes:**
- Removed `pose_frame` field from VPS responses and anchors
- GeoPose format inherently implies earth-fixed coordinate system
- Reduced payloads:
  - VPS Response: 2147 → 2119 bytes (-28 bytes)
  - Anchor: 886 → 857 bytes (-29 bytes)

### 11. ✅ Omit Empty Arrays

**Issue:** Including empty arrays like `transforms: []` wastes bytes
**Spec:** Keep payloads slim by omitting empty/unused fields

**Fixed:**
```json
// Before (EMPTY ARRAY)
"bounds": { ... },
"transforms": [],  // ← empty, wastes bytes
"available_from": 1759241314245

// After (OMITTED)
"bounds": { ... },
"available_from": 1759241314245
```

**Changes:**
- Omit `transforms` when empty (not applicable for this service)
- General principle: omit empty arrays/objects for minimal payloads
- Reduced ContentAnnounce: 1188 → 1170 bytes (-18 bytes)

---

## Final Payload Sizes (v1.3 Optimized)

| Message Type | Round 1 | Round 2 | Round 3 | Savings |
|-------------|---------|---------|---------|---------|
| CONTENT_ANNOUNCE | 1166 | 1188 | **1170** | -3.4% |
| CONTENT_QUERY | - | 531 | **515** | -3.0% |
| CONTENT_QUERY_RESULT | 1275 | 1337 | **1319** | +3.4% |
| VPS_REQUEST | 1417 | 1450 | **1454** | +2.6% |
| VPS_RESPONSE | 2400 | 2147 | **2119** | -11.7% |
| ANCHOR_UPDATE | 772 | 886 | **857** | +11.0% |
| **Total** | **7030** | **7539** | **7434** | +5.7% |

**Round 3 savings vs Round 2:** -105 bytes (-1.4%)

Note: Some increases are due to added features (_legacy fields, stamp, URI canonicalization), but overall format is more efficient and spec-compliant.

---

## Final Compliance Summary

### All 11 Refinements Implemented ✅

1. ✅ Earth-fixed bbox: 2D `[west, south, east, north]`
2. ✅ Earth-fixed poses: GeoPose `{lat, lon, h, q_wxyz}`
3. ✅ Quaternion: Single `q_wxyz` format
4. ✅ Discovery response: `announces[]` not `results[]`
5. ✅ Announce payload: `bounds` not `coverage`
6. ✅ Canonical IDs: URIs with `_legacy_*` for UUIDs
7. ✅ Timestamps: ISO8601 `stamp` mirror
8. ✅ Provider ID: Documented redundancy
9. ✅ Query volume: Single CoverageElement
10. ✅ Removed `pose_frame` (GeoPose implies earth-fixed)
11. ✅ Omit empty arrays for slim payloads

---

**Compliance Verified:** 2025-09-30
**Reviewer:** Spec feedback from OpenAR Cloud (3 rounds)
**Status:** ✅ All issues resolved, fully spec-compliant with optimized minimal payloads