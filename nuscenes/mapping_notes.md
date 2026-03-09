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

## Message Types (envelope `msg_type`)

- `NUSC_EGO_POSE`
- `NUSC_VISION_META`
- `NUSC_VISION_FRAME`
- `NUSC_LIDAR_META`
- `NUSC_LIDAR_FRAME`
- `NUSC_RAD_DET_SET`
- `NUSC_DET3D_SET`

## Blob Strategy

For images/LiDAR, payloads carry `BlobRef.blob_id` containing nuScenes relative file paths.
Subscriber resolves these against `--dataroot` and logs to Rerun.
