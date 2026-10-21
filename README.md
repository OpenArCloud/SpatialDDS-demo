# SpatialDDS Demo

![The venue as a live model — robot, ducks and declared water at Littlefield Fountain](docs/images/owm-venue.jpg)

Reference demos for **SpatialDDS 1.8**, running on CycloneDDS. The repo
vendors 1.8's IDL verbatim under [`idl/v1.8`](idl/v1.8) (spec commit
`9c3e1f2`, the one the validating gateway pins), carries its manifest
examples in [`manifests/v1.8`](manifests/v1.8), and ships five runnable demos.

| package | era | generated from | used by |
| --- | --- | --- | --- |
| `spatialdds_idl18` | **1.8**, including provisional `spatial.owm/0.1` | [`idl/v1.8`](idl/v1.8) | every demo: the AR demo and its mock VPS, discovery, the Open World Model, the MCAP / MQTT / ROS 2 bridges, fusion, and both web surfaces |
| `spatialdds_demo_idl18` | **1.8** | [`idl/demo18`](idl/demo18) | the demo-owned types the spec has no struct for — bootstrap and fusion-coverage diagnostics — bound to `spatialdds_idl18` for every spec type |
| `spatialdds_idl` | 1.7, stamped | [`idl/v1.7`](idl/v1.7) | **transitional**: the content catalogue until it moves onto `ContentAnnounce` → manifest, and the interop probe. See [`docs/final-migration-census.md`](docs/final-migration-census.md) |

The 1.7 → 1.8 move is appendable evolution as the spec describes it, and it
was measured rather than assumed: of 146 definitions the two trees share, 138
are byte-identical and the other 8 only *append* members. The interop probe —
still built from the 1.7 tree — discovers and reads the 1.8 demo, and is read
by it, in both directions.

The spec lives at [spatialdds.org](https://spatialdds.org); its source is
[OpenArCloud/SpatialDDS-spec](https://github.com/OpenArCloud/SpatialDDS-spec).

## The two ISMAR demos

Both need Docker. The web UI also needs Node.

### 1 · SpatialDDS + OpenVPS

A browser localizes against a real visual positioning service it discovered on
the bus — no configured endpoint, no per-service client code.

```bash
./run_bridge_server_docker.sh          # VPS + catalogue + bridge on :8088
cd web && npm install && npm run dev   # → http://localhost:5173/
```

Turn on **REST Messages** and **DDS Messages**, then click **Localize**. The
REST panel shows the three calls the browser makes — discovery search, localize,
model snapshot — and the DDS panel the five bus messages they cause.

The bundled localizer returns the prior plus jitter. For poses computed from
pixels, put a real [OpenVPS](https://github.com/OpenArCloud/openvps) on the bus
— nothing here changes, discovery simply finds it instead.
**[Full recipe →](docs/ismar-openvps.md)**

### 2 · Open World Model

The venue as a live model rather than a list of files: entities with identity,
pose, type, basis and lifecycle. A robot plans against the venue's own
declarations; ducks follow the water; every claim says how it was arrived at.
Migrated to SpatialDDS 1.8's `spatial.owm/0.1`, which carries no relationship
type -- the containment hierarchy this demo used to publish is the one thing
the migration cost, and [`docs/owm-1.8-census.md`](docs/owm-1.8-census.md)
says so member by member.

```bash
SPATIALDDS_MODEL_LAYER=1 SPATIALDDS_POND_WATCH=1 ./run_bridge_server_docker.sh
scripts/run_robot_tier.sh              # second terminal: nav2 plans the routes
cd web && VITE_SPATIALDDS_BINDING_URL=http://localhost:5173 \
          VITE_SPATIALDDS_BINDING_TOKEN=bridge-session npm run dev
```

State and live come over the 1.8 web binding on `:8089`, which the first
command starts alongside the bridge. The two variables point the client at
this dev server's proxy so `Send Robot` can carry its credential same-origin —
[`docs/ismar-owm.md`](docs/ismar-owm.md) explains why, and plain
`npm run dev` still shows the venue live without them.

The screenshot above is that view. **[Full recipe →](docs/ismar-owm.md)** —
including the ROS 2 robot tier, which is what makes the keep-out real.

## Demos

| Demo | Path | What it shows |
|---|---|---|
| **SpatialDDS + OpenVPS** | [`ar_demo/`](ar_demo/README.md) | Bootstrap → discovery → coverage query → localization → catalog → anchor, on a Cesium globe. Runs against real [OpenVPS](https://github.com/OpenArCloud/openvps). |
| **Open World Model** | [`docs/world-model.md`](docs/world-model.md) | Entities, basis and lifecycle on latched topics; a ROS 2 robot planning against declared keep-outs. `spatial.owm/0.1`, SpatialDDS 1.8. |
| Multi-operator fusion | [`multi_operator_fusion/`](multi_operator_fusion/README.md) | Three AV fleet operators and a 6G base station share `Detection3D`; a platform fuser publishes unified `FusedTrack`s. |
| nuScenes → Rerun | [`nuscenes/`](nuscenes/README.md) | nuScenes v1.0-mini as typed samples: ego pose, 6 cameras, LiDAR, 5 radars, 3D annotations. |
| DeepSense 6G → Rerun | [`deepsense/`](deepsense/README.md) | DeepSense Scenario 9 V2I: 60 GHz beam, FMCW radar, camera, GPS, 2D lidar. |
| Benchmarks | [`benchmarks/`](benchmarks/README.md) | Latency, discovery, multi-operator and coverage-query scripts, with plotting. |

## Bridges

Under [`bridges/`](bridges/), and they work with every demo above without
per-demo wiring.

| Bridge | Path | What it does |
|---|---|---|
| Web (HTTP/WebSocket) | [`bridges/web_bridge/`](bridges/web_bridge/README.md) | FastAPI gateway. REST for the Cesium demo, subscribe-by-pattern `/ws`, `/api/topics`, and the fusion dashboard at `/`. |
| MCAP record / replay | [`bridges/mcap_bridge/`](bridges/mcap_bridge/README.md) | Records typed samples with schemas **generated from the IDL**, so a recording is readable without this repo. Foxglove-compatible. |
| ROS 2 | [`bridges/ros2_bridge/`](bridges/ros2_bridge/README.md) | Bidirectional: `PoseStamped`, `NavSatFix`, `Imu`, `CompressedImage`, `Detection3DArray`, and `FusedTrackSet` in reverse. |
| MQTT | [`bridges/mqtt_bridge/`](bridges/mqtt_bridge/README.md) | Bidirectional, against Mosquitto or AWS IoT Core. Inbound JSON is built into its announced type before it reaches the bus. |

There are **two** HTTP servers — a gateway and a conformance harness — and they
are not interchangeable: [which one do I want?](docs/servers-and-uis.md)

## Adapters

| Adapter | Repo | What it does |
|---|---|---|
| Intel SceneScape sidecar | [spatialdds-scenescape](https://github.com/OpenArCloud/spatialdds-scenescape) | Publishes a stock SceneScape deployment's scene analytics as typed SpatialDDS samples: tracks, detections, polygon zones, crossing lines, events, and a georeference. Lives in its own repo because it tracks the whole 1.8 draft, while this repo stays on stamped 1.7 outside the world-model lane. |

## Conformance, in one line

**The demos publish spec IDL types on spec-named topics with spec QoS
profiles.** JSON exists only at the edges — WebSocket clients, MQTT payloads,
MCAP records.

That is verifiable rather than asserted:
[`tests/interop_probe.py`](tests/interop_probe.py) is a participant built from
the generated types, the spec's topic names and the §3.3.3 profile table, with
**no demo transport code**, and it exchanges samples with the demo in both
directions.

> **The 1.7 pin is the `v1.7` tag, not the spec repo's `main`.** Resyncing
> `idl/v1.7` from main would mix draft IDL into the lanes that document
> themselves as 1.7 conformant, and the drift gate would not catch it —
> generated output always matches whatever is vendored, so it can only prove
> the two agree, never that the right thing was vendored.
>
> The 1.8 bundle is vendored *separately* for that reason rather than by
> upgrading the pin: `idl/v1.8` generates its own package, and the two trees
> never share a module. `generate_types.py` takes `--spec-idl` to say which
> bundle it is building, and the annotation gate checks both trees and fails
> if it finds fewer than two. Reasoning and resync instructions are in
> [`scripts/generate_types.py`](scripts/generate_types.py).

## Everything else

- **Tests** — `scripts/run_tests.sh` (~20 s) or `scripts/run_tests.sh standard`
  (~3 min, adds the container suites).
- **AWS** — [`deploy/aws/`](deploy/aws/README.md).
- **Contributing, and the lessons this repo has paid for** —
  [`CONTRIBUTING.md`](CONTRIBUTING.md).
- **The spec text, vendored** — [`docs/SpatialDDS-1.7-full.md`](docs/SpatialDDS-1.7-full.md).
