# SpatialDDS Demo

Reference demos for the SpatialDDS 1.7 draft spec (https://spatialdds.org), running
on CycloneDDS. The repo bundles the upstream IDL under `idl/v1.7`, mirrors the
manifest examples in `manifests/v1.7`, and ships four runnable demos that build on
top of a shared `spatialdds/envelope/v1` transport.

1.7 broke the wire format (the spec allows this pre-adoption), so there are no
compatibility shims here: `CoverageResponse` returns compact `ServiceSummary`
rows, `CoverageQuery.expr` and `CoverageElement.type` are gone, `GeoPose` lost
`frame_kind`/`frame_ref`, and every module versions together as
`spatial.<profile>/1.7`. `idl/v1.4`, `idl/v1.6` and the matching `manifests/`
directories are kept for reference only; nothing loads them. Details in
[`ar_demo/SPEC_COMPLIANCE.md`](ar_demo/SPEC_COMPLIANCE.md).

![Multi-operator fusion canvas dashboard](multi_operator_fusion/screenshot.png)

![Cesium web demo](web/screenshot.png)

## Demos in this repo

| Demo | Path | What it shows |
|---|---|---|
| Multi-operator fusion *(flagship)* | [`multi_operator_fusion/`](multi_operator_fusion/README.md) | Three AV fleet operators and a 6G base station share `Detection3D` observations; a platform fuser publishes unified `FusedTrack`s. Rerun or a browser dashboard. |
| nuScenes → Rerun | [`nuscenes/`](nuscenes/README.md) | nuScenes v1.0-mini over DDS envelopes: ego pose, 6 cameras, LiDAR, 5 radars, 3D annotations. |
| DeepSense 6G → Rerun | [`deepsense/`](deepsense/README.md) | DeepSense Scenario 9 V2I: 60 GHz beam, FMCW radar, camera, GPS, 2D lidar. |
| AR demo | [`ar_demo/`](ar_demo/README.md) | Bootstrap → discovery → coverage query → localization → catalog → anchor, plus a Cesium web UI. |
| Benchmarks | [`benchmarks/`](benchmarks/README.md) | Latency, discovery, multi-operator and coverage-query scripts, with plotting. |

New here? Start with multi-operator fusion: it exercises the whole envelope
transport against real datasets.

## Bridges

Bridges live under [`bridges/`](bridges/) and work with every demo above without
per-demo wiring.

| Bridge | Path | What it does |
|---|---|---|
| Web (HTTP/WebSocket) | [`bridges/web_bridge/`](bridges/web_bridge/README.md) | FastAPI server. REST endpoints for the Cesium demo, plus subscribe-by-pattern `/ws`, `/api/topics`, `/api/stats`. Serves the fusion canvas dashboard at `/`. |
| MCAP record / replay | [`bridges/mcap_bridge/`](bridges/mcap_bridge/README.md) | Records `spatialdds/envelope/v1` to an [MCAP](https://mcap.dev) file and replays it. Lossless, Foxglove-compatible. |
| ROS 2 | [`bridges/ros2_bridge/`](bridges/ros2_bridge/README.md) | Bidirectional. Covers `PoseStamped`, `NavSatFix`, `Imu`, `CompressedImage`, `Detection3DArray`, and `FusedTrackSet` in reverse. The conversion layer is duck-typed, so most of it tests without ROS 2 installed. |
| MQTT | [`bridges/mqtt_bridge/`](bridges/mqtt_bridge/README.md) | Bidirectional, against Mosquitto or AWS IoT Core. MQTT topic = `logical_topic`, same JSON payload. QoS and retain are inferred from the topic suffix. |

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

## Browser UIs

- **AR demo** — 3D Cesium view of VPS coverage, catalog, localisation and anchor
  publication. Served by Vite from [`web/`](web/README.md); it calls the web
  bridge for data. Setup in [`ar_demo/README.md`](ar_demo/README.md#cesium-web-ui).
- **Multi-operator fusion** — 2D top-down canvas served by the web bridge itself
  at `http://localhost:8088/`, with a topic-list debug page at `/debug`. Operator
  egos and trails, detection wireframes, planned trajectories, fused tracks,
  conflict markers, live metrics. Setup in
  [`multi_operator_fusion/README.md`](multi_operator_fusion/README.md#browser-canvas-dashboard).
