# DeepSense 6G -> SpatialDDS -> Rerun Demo

Demonstrates SpatialDDS transporting DeepSense 6G Scenario 9 V2I beam-management data and visualizing it in Rerun.

## Data Location

Current expected dataroot:

`nuscenes/scenario9_dev`

The dataset is ignored by git via `.gitignore`.

## Run

```bash
bash deepsense/run_docker_demo.sh
```

Open:

`http://127.0.0.1:9090?url=rerun%2Bhttp%3A%2F%2Flocalhost%3A9876%2Fproxy`

Useful overrides:

```bash
MAX_SAMPLES=5 bash deepsense/run_docker_demo.sh
SEQUENCE=2 MAX_SAMPLES=10 bash deepsense/run_docker_demo.sh
SPAWN_VIEWER=0 MAX_SAMPLES=2 bash deepsense/run_docker_demo.sh
RERUN_GRPC_PORT=9976 RERUN_WEB_PORT=9190 bash deepsense/run_docker_demo.sh
```

Stop:

```bash
bash deepsense/stop_docker_demo.sh
```

## What It Visualizes

- beam-power polar plot and best beam status
- radar range-angle and range-Doppler heatmaps from raw FMCW tensor
- base station camera image with 2D bounding boxes
- top-down 2D lidar scan
- unit1/unit2 GPS points

## SpatialDDS Coverage

- provisional `sensing.rf_beam`
- `sensing.rad` tensor path
- `sensing.vision`
- `core` geopose path
- `semantics` detection2d path
