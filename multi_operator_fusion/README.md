# Multi-Operator Fusion Demo

Three AV fleet operators + one 6G base station share `Detection3D`
observations on a SpatialDDS bus. A platform fuser does NN-gated track
fusion and publishes unified `FusedTrack`s + coverage metrics. Two
visualisations are bundled — a Rerun viewer for offline drill-down and
a browser canvas dashboard for the live "look at the intersection" view.

![Canvas dashboard](screenshot.png)

## Architecture

| Process | Source | Topic prefix |
|---|---|---|
| operator_a / b / c publisher | nuScenes scene (different sensor mixes) | `spatialdds/operator_{a,b,c}/…` |
| infrastructure publisher | DeepSense Scenario 9 (60 GHz beam + radar + cam + lidar) | `spatialdds/infrastructure/…` |
| fusion service | — | subscribes `*/sensing/detection3d/v1`, publishes `platform/fusion/{track,coverage}/v1` |
| Rerun subscriber / web bridge | — | subscribes the lot |

Every payload carries `source_operator` at the top level — that's the
interoperability contract. The fuser runs NN-association (5 m / 5 m·s⁻¹
gate), uncertainty-weighted position fusion (1/σ²), confidence boost
via `1 − ∏(1 − cᵢ)`, and 2-hits-confirms / 6-misses-drops lifecycle.

## Quick start

```bash
# 1. Check / point at datasets (prints download links if missing)
bash multi_operator_fusion/scripts/download_demo_data.sh

# 2. Run the demo with the Rerun viewer (spawns at http://127.0.0.1:9090)
bash multi_operator_fusion/run_docker_demo.sh

# 3. Clean up when done
bash multi_operator_fusion/stop_docker_demo.sh
```

The `cyclonedds-python` Docker image builds automatically on first run
(~2 minutes). Datasets are not redistributed — nuScenes and DeepSense 6G
each require accepting their own terms of use; step 1 prints the URLs.

## Browser canvas dashboard

A 2D top-down dashboard at `http://localhost:8088/` (legacy topic-list
debug page at `/debug`) — colour-coded operator dots, detection
wireframes, dashed planned trajectories, white fused-track diamonds,
pulsing red conflict markers, live metrics bar.

The dashboard is served by the [web bridge](../bridges/web_bridge/README.md);
the easiest way to bring everything up is the local docker-compose stack
that ships with the AWS deploy:

```bash
docker compose -f deploy/aws/docker-compose.local.yaml --profile demo up -d
# → http://localhost:8088/

docker compose -f deploy/aws/docker-compose.local.yaml down
```

## Reading the numbers (Rerun's `fusion/metrics` text log + dashboard bar)

- `tracks=N` — confirmed unified objects this tick.
- `multi-src=K/N (X%)` — K of N tracks are observed by **2+ independent
  sources** (operator↔operator or operator↔infrastructure). The
  headline metric: these are objects no single participant could
  produce alone.
- `vs best AV` — `n_total / max(per-operator track count, excluding
  infrastructure)`. Captures fusion's value over the best single AV
  operator. Legacy `coverage_improvement` is `n_total / best_single`
  including infrastructure (often 1.0× because the BS sees everything).

## Run variants (`run_docker_demo.sh` env vars)

| Var | Default | Effect |
|---|---|---|
| `MAX_SAMPLES` | 20 | AV publisher frame cap (2 Hz) |
| `INFRA_MAX_SAMPLES` | 5×`MAX_SAMPLES` | Infra cap matched to AV runtime |
| `SPAWN_VIEWER` | 1 | 0 = headless, no browser |
| `USE_EXTERNAL_VIEWER` | 0 | 1 = connect to a pre-running Rerun viewer |
| `SKIP_INFRA` | 0 | 1 = AV-only fusion (no DeepSense publisher) |
| `INFRA_OFFSET` | `-30 30 0` | BS placement in conceptual intersection (m) |
| `NUSCENES_DATAROOT` / `DEEPSENSE_DATAROOT` | autodetected | Override dataset paths (must be inside the repo for the bind-mount) |

Without Docker (cyclonedds + nuscenes-devkit + rerun-sdk on PYTHONPATH):

```bash
python multi_operator_fusion/run_demo.py \
  --nuscenes-dataroot multi_operator_fusion/data/nuscenes_scene \
  --deepsense-dataroot multi_operator_fusion/data/deepsense_seq \
  --max-samples 20 --spawn-viewer
```

## Tests

All run without DDS, Rerun, nuScenes, or DeepSense data installed:

```bash
python -m pytest multi_operator_fusion/ -q
```

## Wire topics (cheat sheet)

```
# Per-operator (each publisher → fuser + viewer)
spatialdds/{operator}/sensing/detection3d/v1   NUSC_DET3D_SET | INFRA_DET3D_SET
spatialdds/{operator}/ego/pose/v1              NUSC_EGO_POSE
spatialdds/{operator}/plan/{agent}/trajectory/v1   PlannedTrajectory
spatialdds/{operator}/discovery/announce/v1    Announce

# Platform (fuser → viewer)
spatialdds/platform/fusion/track/v1            NUSC_FUSED_TRACK_SET
spatialdds/platform/fusion/coverage/v1         NUSC_FUSION_COVERAGE
spatialdds/platform/events/trajectory_conflict/v1   SpatialEvent
spatialdds/platform/entity/binding/v1          EntityBinding
```

Infrastructure also adds `rf_beam`, `rad/.../tensor`,
`vision/.../frame`, `lidar/.../frame`, and `geo/unit[12]/pose`.

## Troubleshooting

- **Rerun viewer loads but is blank** — version mismatch between the
  container's `rerun-sdk` (pinned in `requirements.txt`, currently
  `0.23.1`) and the host `rerun` CLI. Match exactly with
  `pip install rerun-sdk==<pinned>` or
  `cargo install rerun-cli --version <pinned>`. On Linux, also
  `RERUN_CONNECT_HOST=<your-LAN-ip>` if `host.docker.internal` doesn't
  resolve.
- **Canvas dashboard at :8088 has only fusion topics** — happens after
  bouncing the web bridge mid-run; bounce the publisher too so DDS
  rediscovery is clean.
- **`gRPC port 9876 already in use`** — `bash
  multi_operator_fusion/stop_docker_demo.sh`, or set
  `RERUN_GRPC_PORT=<other>`.
