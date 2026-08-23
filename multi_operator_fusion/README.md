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
| fusion service | — | discovers detection lanes from announces; announces and publishes `platform/fusion/{track,coverage}/v1` |
| Rerun subscriber / web bridge | — | subscribe to every announced lane |

Every process finds the others through discovery: read the announces on
`spatialdds/discovery/announce/v1`, resolve each announced §3.3.2 type, open
a reader. The producer is `source_id` on the sample and the operator is in
the topic name, which is where DDS expects that kind of identity.

The fuser runs NN-association (5 m / 5 m·s⁻¹ gate), uncertainty-weighted
position fusion (1/σ²), confidence boost via `1 − ∏(1 − cᵢ)`, and
2-hits-confirms / 6-misses-drops lifecycle. It gates on velocity, which
`semantics::Detection3D` carries directly.

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

One DDS topic per logical topic, carrying the §3.3.2 type its announce names,
on that type's §3.3.3 QoS profile.

```
# Per-operator (each publisher → fuser + viewer)
spatialdds/{operator}/sensing/detection3d/v1         detection3d         DET_RT
spatialdds/{operator}/ego/pose/v1                    framed_pose         POSE_RT
spatialdds/{operator}/plan/{agent}/trajectory/v1     planned_trajectory  EVENT_RT

# Platform (fuser → viewer)
spatialdds/platform/fusion/track/v1                  fused_track           DET_RT
spatialdds/platform/fusion/coverage/v1               oarc.fusion_coverage  MAP_META
spatialdds/platform/events/trajectory_conflict/v1    spatial_event         EVENT_RT
spatialdds/platform/entity/binding/v1                entity_binding        MAP_META

# Discovery — one well-known topic, keyed on service_id
spatialdds/discovery/announce/v1                     Announce  DISCOVERY_ANNOUNCE
spatialdds/discovery/depart/v1                       Depart    DISCOVERY_ANNOUNCE
```

Infrastructure also adds `rf_beam`, `rad/.../tensor`, `vision/.../frame`,
`lidar/.../frame`, and `geo/unit[12]/pose`. Image and lidar bytes ride
`spatialdds/blob/chunk/v1` as `blob_chunk`, keyed `(blob_id, index)`; a frame
message names its blob by `BlobRef` and never inlines it.

**Nothing hardcodes that list.** Each service announces its lanes with their
type and profile, and consumers open a reader per announced lane — so a
service that starts mid-run is picked up, and one whose type a consumer
cannot resolve is skipped rather than fatal (§3.3.2 treats unregistered names
as extension points).

The two `oarc.*` names are the demo's own: 1.7 has no fused-track type
(`semantics::Tracklet` is feature-level, `vision::Track2D` per-image) and none
for fusion coverage metrics. See
[`ar_demo/SPEC_COMPLIANCE.md`](../ar_demo/SPEC_COMPLIANCE.md#extensions-what-17-still-has-no-type-for).

### Expect drops on the real-time lanes

`DET_RT` and `POSE_RT` are BEST_EFFORT per §3.3.3, so a burst will lose
samples — that is the profile working, not a fault. The reliable lanes
(`EVENT_RT`, `MAP_META`) lose nothing. Every stream used to share one
RELIABLE + KEEP_ALL topic, which is why nothing ever dropped before and why
one slow consumer could stall every publisher; `tests/test_head_of_line.py`
measures both shapes.

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
