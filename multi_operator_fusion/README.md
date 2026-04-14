# Multi-Operator Fusion Demo

A 6G base station and three autonomous-vehicle fleets from different
operators share real-time spatial observations through SpatialDDS.
The platform fuser combines infrastructure radar sensing with
vehicle-level perception into a unified intersection model that no
single source could build alone.

## Architecture

Six processes share a single CycloneDDS domain:

| Process | Source data | Topic prefix | Sensor mix |
|---|---|---|---|
| operator_a publisher | nuScenes scene | `spatialdds/operator_a/…` | full (cam + lidar + radar) |
| operator_b publisher | nuScenes scene | `spatialdds/operator_b/…` | camera only |
| operator_c publisher | nuScenes scene | `spatialdds/operator_c/…` | lidar + radar |
| infrastructure publisher | DeepSense Scenario 9 | `spatialdds/infrastructure/…` | 60 GHz beam + FMCW radar + camera + lidar |
| fusion service | — | subscribes `+/sensing/detection3d/v1` → publishes `platform/fusion/{track,coverage}/v1` | — |
| Rerun subscriber | — | subscribes everything | renders split-screen + fused view |

Each operator publishes its fleet-internal `Detection3DSet` with
`source_operator` provenance. The fuser runs NN-gated association with
uncertainty-weighted position fusion and emits `FusedTrack`s labelled
with the list of contributing sources.

Design spec lives in the parent repo context; the ported algorithm is
from the SpatialDDS v5 PoC.

## What's in the Rerun viewer

One nuScenes clip (scene-0061, ~20 s of Boston driving) is replayed three
times in parallel by three "operators," each shifted to a different
quadrant of a conceptual intersection. One infrastructure stream
(DeepSense Scenario 9 radar/camera/60 GHz beam) publishes from a fourth
position. Every process publishes on CycloneDDS; nothing shares state
directly.

- `world/operator_a/` (green) — full nuScenes ego, south offset. Camera
  JPEGs, LiDAR_TOP point cloud, 5 radar channels, Detection3D boxes.
- `world/operator_b/` (blue) — east offset, cameras only. Represents a
  "budget fleet" with dashcams.
- `world/operator_c/` (orange) — west offset, lidar + radar, no cameras.
  Logistics-fleet profile.
- `world/infrastructure/` (magenta) — 60 GHz phased-array beam power,
  FMCW radar tensor, camera. One Detection3D per frame at the Tx
  vehicle's GPS-derived ENU position.
- `world/fused/tracks` (white) — the only entities the platform fuser
  emits. Each point is labelled with `class [n=N] op_a,op_b,…` so you
  can see exactly which operators contributed.
- `fusion/metrics` — TextLog with per-tick coverage stats.

## What the fusion service does

Every 500 ms:

1. Collect `Detection3D`s that arrived since the last tick, tagged with
   `source_operator` from the payload.
2. NN-associate each to an existing track within 5 m and 5 m/s.
3. On match, merge: position is a precision-weighted average (1/σ²),
   confidence boost via `1 − ∏(1 − cᵢ)`, `source_operators` accumulates.
4. Two consecutive hits → confirmed; six misses → dropped.
5. Emit `FusedTrack`s + coverage metrics on platform topics.

## Reading the numbers

- `tracks=N` — confirmed unified objects this tick.
- `multi_src=K` — of those, K were observed by **2+ independent sources**
  (operator↔operator or operator↔infrastructure). The headline metric:
  these are objects that no single participant could have produced
  alone.
- `improvement=X.Yx` — platform track count divided by the most prolific
  single operator's count. Quantifies "unified model > sum of parts."

Typical numbers with default 60 m offsets: `tracks≈130–150,
multi_src≈5–10, improvement≈2.5–3.0x`. Low `multi_src` is deliberate —
operators are offset in different compass directions so their coverage
mostly doesn't overlap. To see multi-source rates climb, drop the offsets
(e.g. `INFRA_OFFSET="0 0 0"` plus edits in `run_demo.py`) so everyone
observes the same ground.

## Layout

```
multi_operator_fusion/
├── publisher.py               # per-operator nuScenes publisher (--operator, --offset, --sensor-filter)
├── infrastructure_publisher.py  # DeepSense Scenario 9 publisher, synthesizes Det3D from Tx GPS
├── fusion.py                  # pure algorithm: NN-gated multi-source track fusion
├── fusion_service.py          # DDS adapter around fusion.py
├── subscriber_rerun.py        # per-operator + fused-view Rerun visualization
├── routing.py                 # topic parsing + operator color table (shared helpers)
├── run_demo.py                # 6-process launcher
├── test_*.py                  # 6 unit/integration suites, 60 tests
└── README.md
```

## Prerequisites

- Docker (the launcher uses the repo's `cyclonedds-python` image, built on
  first run from the repo `Dockerfile`). Runs with `--network host` and
  expects `host.docker.internal` to resolve — Docker Desktop (macOS/Windows)
  works out of the box; on Linux Docker CE pass
  `--add-host=host.docker.internal:host-gateway` or set
  `RERUN_CONNECT_HOST` to your host's LAN IP.
- The Rerun CLI on the host (only if `SPAWN_VIEWER=1`):
  `pip install rerun-sdk` (or `cargo install rerun-cli`).
- Ports **9876** (Rerun gRPC) and **9090** (Rerun web viewer) free.

## Quick Start

```bash
# 1. Get the ~100 MB demo subset
bash multi_operator_fusion/scripts/download_demo_data.sh
# (Maintainers: to regenerate from full datasets, see make_demo_subset.py)

# 2. Run the demo (spawns viewer on http://127.0.0.1:9090)
bash multi_operator_fusion/run_docker_demo.sh

# 3. When done, clean up ports
bash multi_operator_fusion/stop_docker_demo.sh
```

On first run the `cyclonedds-python` Docker image builds (~2 minutes).

## Run variants

`run_docker_demo.sh` auto-detects the pruned subset under
`multi_operator_fusion/data/`. If it's absent, the launcher falls back to
the repo's existing full-fidelity datasets (`nuscenes/data/v1.0-mini` and
`nuscenes/scenario9_dev`) — so if you already have those in place you
don't need the subset at all.

Explicit override (both paths must be inside the repo tree so Docker can
bind-mount them):

```bash
NUSCENES_DATAROOT=/abs/path/inside/repo/to/nuscenes/v1.0-mini \
DEEPSENSE_DATAROOT=/abs/path/inside/repo/to/scenario9_dev \
MAX_SAMPLES=40 \
bash multi_operator_fusion/run_docker_demo.sh
```

**Subset vs. full fidelity** — the fusion story (tracks, multi-source
count, coverage improvement) is identical either way, because it depends
only on the Detection3D streams which are small. What the full datasets
unlock is the sensor eye-candy: six nuScenes camera feeds per operator,
the DeepSense camera, and the FMCW radar tensor.

Common tweaks (env vars):

| Var | Default | Effect |
|---|---|---|
| `MAX_SAMPLES` | 20 | AV publisher frame cap (2 Hz) |
| `INFRA_MAX_SAMPLES` | 5×`MAX_SAMPLES` | Infra frame cap (10 Hz); matched to AV runtime |
| `SPAWN_VIEWER` | 1 | 0 = headless, no browser |
| `USE_EXTERNAL_VIEWER` | 0 | 1 = connect to a pre-running Rerun viewer instead of spawning one (keep viewer alive across runs) |
| `SKIP_INFRA` | 0 | 1 = AV-only fusion (no DeepSense publisher) |
| `INFRA_OFFSET` | `-30 30 0` | BS placement in conceptual intersection (m) |

Run without Docker (needs cyclonedds + nuscenes-devkit + rerun-sdk in
`$PYTHONPATH`):

```bash
python multi_operator_fusion/run_demo.py \
  --nuscenes-dataroot multi_operator_fusion/data/nuscenes_scene \
  --deepsense-dataroot multi_operator_fusion/data/deepsense_seq \
  --max-samples 20 --spawn-viewer
```

## Troubleshooting

- **Viewer loads but is blank** — the subscriber couldn't reach the
  host-side Rerun. On Linux Docker CE, `host.docker.internal` isn't
  defined by default; export `RERUN_CONNECT_HOST=<your-LAN-ip>` or
  add `--add-host=host.docker.internal:host-gateway` to the docker
  invocation. Verify with
  `docker run --rm --network host cyclonedds-python getent hosts host.docker.internal`.
- **`rerun gRPC port 9876 already in use`** — run
  `bash multi_operator_fusion/stop_docker_demo.sh`, or set
  `RERUN_GRPC_PORT=<other port>`.
- **`infrastructure exited early rc=1`** — check `/tmp/multi_op_pip.log`
  (pip install inside the container) and the demo log. Common causes:
  DeepSense dataroot doesn't contain `scenario9.csv`, or the wrong sequence.
- **`dataroot must be inside repo`** — Docker bind-mounts the repo at
  `/app`; any paths you pass must be inside `REPO_ROOT`. Symlinks are fine.
- **Infra publishes but no radar/beam streams appear in Rerun** — expected
  if you're running the pruned subset, which omits radar cubes by default.
  Rerun will show `rad_tensor unavailable — skipping this stream for the
  rest of the run` once in stderr; Detection3D still flows normally.

## Tests

All tests run without DDS, Rerun, nuScenes, or DeepSense data installed:

```bash
for t in multi_operator_fusion/test_*.py; do python "$t"; done
```

Suites:
- `test_publisher.py` — topic naming, offset application, sensor-filter spec
- `test_fusion.py` — lifecycle, cross-operator merge, gating, uncertainty
  weighting, coverage metrics
- `test_fusion_service.py` — envelope dispatch, detection parsing, publish
  plumbing
- `test_subscriber.py` — operator routing + color mapping
- `test_infrastructure.py` — GPS→ENU conversion, velocity finite-diff,
  payload-shape compat with the fusion parser
- `test_integration.py` — in-process round-trip: operator + infra envelopes
  through fusion → `FusedTrack`/coverage payloads on platform topics

## Wire topics

Per-operator (publishers → fuser + Rerun):

```
spatialdds/{operator}/sensing/detection3d/v1        NUSC_DET3D_SET | INFRA_DET3D_SET
spatialdds/{operator}/ego/pose/v1                   NUSC_EGO_POSE
spatialdds/{operator}/vision/{ch}/frame/v1          NUSC_VISION_{META,FRAME}
spatialdds/{operator}/lidar/{ch}/frame/v1           NUSC_LIDAR_{META,FRAME}
spatialdds/{operator}/rad/{ch}/frame/v1             NUSC_RAD_DET_SET
```

Infrastructure adds: `rf_beam`, `rad/.../tensor`, `vision/.../frame`,
`lidar/.../frame`, `geo/unit[12]/pose`.

Platform (fuser → Rerun):

```
spatialdds/platform/fusion/track/v1     NUSC_FUSED_TRACK_SET
spatialdds/platform/fusion/coverage/v1  NUSC_FUSION_COVERAGE
```

Every payload carries `source_operator` at the top level — that's the
interoperability contract.
