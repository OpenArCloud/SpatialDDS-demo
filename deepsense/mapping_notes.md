# DeepSense Scenario 9 Mapping Notes

This demo maps the real DeepSense Scenario 9 files on disk to SpatialDDS-like typed payloads.

## Observed dataset format

- beam power: text file with 64 scalar rows
- radar tensor: MATLAB `.mat`, key `data`, shape `(4, 256, 128)`, `complex64`
- lidar scan: MATLAB `.mat`, key `data`, shape `(N, 2)`, `float64`
- GPS: text files with `lat` then `lon`
- 2D bbox labels: YOLO-style text files under `resources/annotations/bbox`

## Mapping

- beam vector -> `RfBeamFrame.power`
- `argmax(power)` -> `best_beam_idx`
- `max(power) < threshold` -> blockage heuristic
- radar cube -> `RadTensorFrame` blob reference, plus derived heatmaps in subscriber
- camera jpeg path -> `VisionFrame` blob
- unit1/unit2 GPS -> `GeoPose`
- bbox txt -> `Detection2DSet`

## Notes

- implementation uses the same working DDS envelope + Rerun launch pattern as `nuscenes/`
