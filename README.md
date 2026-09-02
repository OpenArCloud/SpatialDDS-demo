# SpatialDDS Demo

![Cesium AR demo](web/screenshot.png)

![Multi-operator fusion canvas dashboard](multi_operator_fusion/screenshot.png)

Reference demos for **SpatialDDS 1.7** — the stamped release, 2026-08-23, tag
`v1.7` — running on CycloneDDS. The repo vendors that release's IDL verbatim
under `idl/v1.7`, mirrors its manifest examples in `manifests/v1.7`, and ships
four runnable demos.

The spec itself lives at [spatialdds.org](https://spatialdds.org), and its
source — the IDL this repo vendors, the prose, and the release tags — at
[OpenArCloud/SpatialDDS-spec](https://github.com/OpenArCloud/SpatialDDS-spec).

## Run one

Both need Docker; the AR demo's web UI also needs Node.

**AR demo** — bootstrap, discovery, localization, catalogue, on a Cesium globe.

```bash
./run_bridge_server_docker.sh          # VPS + catalogue + bridge on :8088
cd web && npm install && npm run dev   # → http://localhost:5173/
```

Turn on **REST Messages** and **DDS Messages**, then click Localize: the REST
panel shows the two HTTP calls the browser makes, the DDS panel the five bus
messages they cause. That pairing is the demo's point — a browser using the
spec's HTTP binding while everything behind it is DDS.

**With a real VPS.** The bundled localizer returns the prior plus jitter. For
real poses, put [OpenVPS](https://github.com/OpenArCloud/openvps) on the bus
from its [`spatialdds`](https://github.com/OpenArCloud/openvps/tree/spatialdds)
branch: it announces itself and serves `VpsRequest`, so nothing here changes.
Discovery just finds it instead.
[openvps-deploy](https://github.com/OpenArCloud/openvps-deploy) runs it on AWS,
and [`deploy/aws/README.md`](deploy/aws/README.md#running-against-a-real-openvps)
has the recipe.

The screenshot above is that setup: a real localisation against a LiDAR map of
Littlefield Fountain, UT Austin.

**Multi-operator fusion** — three fleet operators and a 6G base station
sharing detections; a fuser publishing unified tracks. Dashboard on :8088,
Rerun viewer on :9090.

```bash
bash multi_operator_fusion/scripts/download_demo_data.sh   # prints links if missing
bash multi_operator_fusion/run_docker_demo.sh
bash multi_operator_fusion/stop_docker_demo.sh             # when done
```

Tests: `scripts/run_tests.sh` (~20s) or `scripts/run_tests.sh standard`
(~3 min, adds the container suites). AWS: [`deploy/aws/`](deploy/aws/README.md).

> **The pin is the `v1.7` tag, not the spec repo's `main`.** Main is now the
> 1.8 draft, bootstrapped from 1.7 and still carrying unswept `/1.7`
> identifiers. Resyncing from it would mix draft 1.8 IDL into a demo that
> documents itself as 1.7 conformant — and the drift gate would not catch it,
> because generated output always matches whatever is vendored. This demo
> moves to 1.8 only under a deliberate migration brief. Resync instructions
> and the reasoning are in `scripts/generate_types.py`.

**The demos publish spec IDL types on spec-named topics with spec QoS
profiles.** One DDS topic per logical topic, carrying the §3.3.2 type its
announce names; keyed `Announce` on the well-known discovery topic, so a late
joiner gets every live service and a dispose evicts one. JSON exists only at
the edges — WebSocket clients, MQTT payloads, MCAP records.

That is verifiable rather than asserted: [`tests/interop_probe.py`](tests/interop_probe.py)
is a participant built from the generated types, the spec's topic names and the
§3.3.3 profile table, with **no demo transport code**, and it exchanges samples
with the demo in both directions.

## Demos in this repo

| Demo | Path | What it shows |
|---|---|---|
| Multi-operator fusion *(flagship)* | [`multi_operator_fusion/`](multi_operator_fusion/README.md) | Three AV fleet operators and a 6G base station share `Detection3D` observations; a platform fuser publishes unified `FusedTrack`s. Rerun or a browser dashboard. |
| nuScenes → Rerun | [`nuscenes/`](nuscenes/README.md) | nuScenes v1.0-mini as typed samples: ego pose, 6 cameras, LiDAR, 5 radars, 3D annotations. |
| DeepSense 6G → Rerun | [`deepsense/`](deepsense/README.md) | DeepSense Scenario 9 V2I: 60 GHz beam, FMCW radar, camera, GPS, 2D lidar. |
| AR demo | [`ar_demo/`](ar_demo/README.md) | Bootstrap → discovery → coverage query → localization → catalog → anchor, plus a Cesium web UI. Also runs against real [OpenVPS](https://github.com/OpenArCloud/openvps/tree/spatialdds). |
| Benchmarks | [`benchmarks/`](benchmarks/README.md) | Latency, discovery, multi-operator and coverage-query scripts, with plotting. |

New here? Start with multi-operator fusion: it is the one that exercises
discovery, per-type QoS and keyed instances together, and it runs with no
dataset to download.

## World model (prototype, demo-local)

An **Open World Model** layer, prototyped here before anything is proposed for
the spec. `oarc_model` is demo-local and non-normative: no registry row, no
`/1.7` identifiers, no spec type touched. See
[`ar_demo/SPEC_COMPLIANCE.md`](ar_demo/SPEC_COMPLIANCE.md) for its status and
the gaps it has surfaced.

The catalogue says what content *exists* — one `duck.glb`, one checksum, one
URI. The model says what is *there*: entities with identity, pose, type and
relationships, pointing back at catalogue content when they have an asset. One
asset, three ducks. A catalogue row carrying its own pose can place a duck
exactly once, which is the limitation this removes.

```bash
SPATIALDDS_MODEL_LAYER=1 ./run_bridge_server_docker.sh   # off by default
curl localhost:8088/v1/model                             # the whole model
python3 scripts/move_duck.py ent:duck:west 9.0 -12.0     # and watch it move
```

Two topics — `spatialdds/model/entity/v1` and
`spatialdds/model/relationship/v1` — both TRANSIENT_LOCAL, KEEP_LAST(1) per
key. That is what makes late join work: a client opening a tab is handed the
whole model by the middleware, unrequested, with no replay code anywhere.
Measured across processes at 0.14 s.

`GET /v1/model` serves the same view to clients that have no DDS participant.
**The bridge's cache is a mirror, not a store — it holds nothing the bus does
not, because an HTTP client is not a reader.** Anything that would make it the
source of truth for state the bus does not carry is the wrong change.

Entities name their frame by the **UUIDv5 of its fqn**, the same derivation
the catalogue row and the announced frame transform use, so all three name one
frame rather than three that merely look alike.

Nothing switches the client between paths: if `/v1/model` returns entities it
renders from them and the catalogue contributes only the asset each one points
at, suppressed per `content_id` so the two cannot both draw the same duck.
`?catalogpose=1` forces the legacy path for comparison.

## Bridges

Bridges live under [`bridges/`](bridges/) and work with every demo above without
per-demo wiring.

| Bridge | Path | What it does |
|---|---|---|
| Web (HTTP/WebSocket) | [`bridges/web_bridge/`](bridges/web_bridge/README.md) | FastAPI server. REST endpoints for the Cesium demo, plus subscribe-by-pattern `/ws`, `/api/topics`, `/api/stats`. Serves the fusion canvas dashboard at `/`. |
| MCAP record / replay | [`bridges/mcap_bridge/`](bridges/mcap_bridge/README.md) | Discovers lanes from announces and records typed samples to an [MCAP](https://mcap.dev) file with schemas **generated from the IDL**, so a recording is readable without this repo. Replay rebuilds the typed sample. Foxglove-compatible. |
| ROS 2 | [`bridges/ros2_bridge/`](bridges/ros2_bridge/README.md) | Bidirectional. Covers `PoseStamped`, `NavSatFix`, `Imu`, `CompressedImage`, `Detection3DArray`, and `FusedTrackSet` in reverse. The conversion layer is duck-typed, so most of it tests without ROS 2 installed. |
| MQTT | [`bridges/mqtt_bridge/`](bridges/mqtt_bridge/README.md) | Bidirectional, against Mosquitto or AWS IoT Core. MQTT topic = DDS topic. Inbound JSON is built into its announced type before it reaches the bus, so a malformed payload fails at the bridge. |

## Two HTTP servers — which one do I want?

Both serve the spec's HTTP discovery binding, and both answer it from the same
module (`spatialdds_demo/discovery_http.py`), so they cannot drift. They differ
in where their services come from:

| | `bridges/web_bridge/server.py` | `ar_demo/http_binding.py` |
|---|---|---|
| Role | Gateway: the per-deployment process | Conformance harness and reference implementation |
| Services from | The live DDS bus, via a cached announce feed | An in-memory registry fed by `register` |
| Also serves | `/ws` live streams, `/v1/*`, the canvas dashboard | `register` / `list` |
| Port (default) | 8088 | 8080 |
| Needs DDS? | Yes | No |

Use the bridge when you want discovery over a real bus alongside live data. Use
the HTTP binding to exercise the binding's payload shapes on their own, with a
registry you control.

[`scripts/cold_start.sh`](scripts/cold_start.sh) walks the whole path against a
running bridge — bootstrap, search a geohash cell, read the topics off a
returned manifest, subscribe on `/ws` — in curl and one `websocket-client`
call, importing nothing from this repo. Transcript in the
[bridge README](bridges/web_bridge/README.md#cold-start-with-no-spatialdds-client-code).

## Browser UIs

- **AR demo** — 3D Cesium view of VPS coverage, catalog, localisation and anchor
  publication. Served by Vite from [`web/`](web/README.md); it calls the web
  bridge for data. Setup in [`ar_demo/README.md`](ar_demo/README.md#cesium-web-ui).
- **Multi-operator fusion** — 2D top-down canvas served by the web bridge itself
  at `http://localhost:8088/`, with a topic-list debug page at `/debug`. Operator
  egos and trails, detection wireframes, planned trajectories, fused tracks,
  conflict markers, live metrics. Setup in
  [`multi_operator_fusion/README.md`](multi_operator_fusion/README.md#browser-canvas-dashboard).
