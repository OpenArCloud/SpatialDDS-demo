# nuScenes -> SpatialDDS Mapping Notes

This demo maps nuScenes mini split to SpatialDDS-style typed payloads and publishes over DDS.

## Conformance Highlights

- Quaternion ordering: nuScenes `(w,x,y,z)` -> SpatialDDS `(x,y,z,w)`.
- 3D box size ordering: nuScenes `(w,l,h)` -> SpatialDDS `(width,height,depth)` as `(w,h,l)`.
- Radar dynamic/velocity fields map to `RadDetection` (`dyn_prop`, `vx_compensated`, `vy_compensated`, `rcs_dbsm`).
- Camera calibration maps to `CamIntrinsics` + `StreamMeta.T_bus_sensor`.

## Topics

- `spatialdds/nuscenes/vision/<cam>/frame/v1`
- `spatialdds/nuscenes/lidar/LIDAR_TOP/frame/v1`
- `spatialdds/nuscenes/rad/<radar>/frame/v1`
- `spatialdds/nuscenes/semantics/det3d/v1`
- `spatialdds/nuscenes/ego/pose/v1`

## Types (§3.3.2 registry names, and their QoS profiles)

Each is announced in `TopicMeta` and is what a consumer resolves to open a
reader. The lane table lives in `nuscenes/publisher.py`.

| Topic | Type | QoS |
|---|---|---|
| `ego/pose/v1` | `framed_pose` | `POSE_RT` |
| `geo/ego/pose/v1` | `geopose` | `POSE_RT` |
| `vision/<cam>/meta/v1` | `video_meta` | `SENSOR_META` |
| `vision/<cam>/frame/v1` | `video_frame` | `VIDEO_LIVE` |
| `lidar/LIDAR_TOP/meta/v1` | `lidar_meta` | `SENSOR_META` |
| `lidar/LIDAR_TOP/frame/v1` | `lidar_frame` | `LIDAR_RT` |
| `rad/<radar>/frame/v1` | `radar_detection` | `RADAR_RT` |
| `semantics/det3d/v1` | `detection3d` | `DET_RT` |

The ego lane is two topics: a `FramedPose` (local metric) and a `GeoPose`
(geographic) are different types, so a consumer that wants only the
geographic pose subscribes to only that. They used to be bundled in one
message with a `frame_seq` — a shape no spec type has.

## Blob Strategy

For images/LiDAR, payloads carry `BlobRef.blob_id` containing nuScenes relative file paths.
Subscriber resolves these against `--dataroot` and logs to Rerun.
