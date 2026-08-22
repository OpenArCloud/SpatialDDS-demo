# nuScenes → SpatialDDS → Rerun

Real autonomous-driving data from nuScenes over a SpatialDDS bus, rendered in
Rerun. Publisher and subscriber are separate processes, so the DDS decoupling is
real rather than simulated.

Payloads cover ego pose, camera frames, LiDAR, radar and 3D annotations; Rerun
renders them with stock archetypes (`Transform3D`, `EncodedImage`, `Points3D`,
`Boxes3D`, `GeoPoints`).

## Dataset

Expected dataroot: `nuscenes/data/v1.0-mini`

## Install

```bash
pip install -r nuscenes/requirements.txt
```

## Run

```bash
python nuscenes/run_demo.py --dataroot nuscenes/data/v1.0-mini --scene scene-0061 --spawn-viewer
```

Single-script Docker launcher (starts Rerun + runs Python in `cyclonedds-python`):

```bash
bash nuscenes/run_docker_demo.sh
```

Stop script (cleanly stops launcher-managed Rerun service and default listener ports):

```bash
bash nuscenes/stop_docker_demo.sh
```

Common overrides:

```bash
MAX_SAMPLES=2 SCENE=scene-0061 bash nuscenes/run_docker_demo.sh
SPAWN_VIEWER=0 MAX_SAMPLES=2 bash nuscenes/run_docker_demo.sh
RERUN_GRPC_PORT=9976 RERUN_WEB_PORT=9190 bash nuscenes/run_docker_demo.sh
RERUN_CONNECT_HOST=host.docker.internal bash nuscenes/run_docker_demo.sh
```

When `SPAWN_VIEWER=1`, launcher starts:
- gRPC proxy on host port `9876`
- web viewer on `http://127.0.0.1:9090?url=rerun%2Bhttp%3A%2F%2Flocalhost%3A9876%2Fproxy`
- container subscriber connects to `rerun+http://host.docker.internal:9876/proxy` by default

If viewer is blank/generic, check for port conflicts first:

```bash
lsof -iTCP:9876 -sTCP:LISTEN -n -P
lsof -iTCP:9090 -sTCP:LISTEN -n -P
```

Publisher options (standalone):

```bash
python nuscenes/publisher.py --dataroot nuscenes/data/v1.0-mini --scene scene-0061 --rate-hz 2.0
```

Subscriber options (standalone):

```bash
python nuscenes/subscriber_rerun.py --dataroot nuscenes/data/v1.0-mini --spawn-viewer
```

By default, subscriber runs headless (`spawn=False`) so it also works in containers/CI.

Dataset files under `nuscenes/data/` are gitignored and not redistributed;
nuScenes requires accepting its own terms of use.
