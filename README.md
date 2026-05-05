# SpatialDDS Demo

Reference demos for the SpatialDDS 1.6 draft spec (https://spatialdds.org), running
on CycloneDDS. The repo bundles the upstream IDL under `idl/v1.6`, mirrors the
manifest examples in `manifests/v1.6`, and ships four runnable demos that build on
top of a shared `spatialdds/envelope/v1` transport.

![Multi-operator fusion canvas dashboard](multi_operator_fusion/screenshot.png)

![Cesium web demo](web/screenshot.png)

## Demos in this repo

| Demo | Path | What it shows |
|---|---|---|
| **Multi-operator fusion** *(flagship)* | [`multi_operator_fusion/`](multi_operator_fusion/README.md) | Three AV fleet operators + one 6G base station share `Detection3D` observations. A platform fuser does NN-gated track fusion and publishes unified `FusedTrack`s. Rerun renders per-operator split-screen + fused view. |
| **nuScenes → SpatialDDS → Rerun** | [`nuscenes/`](nuscenes/README.md) | Publishes nuScenes v1.0-mini AV data (ego pose, 6 cameras, LiDAR, 5 radars, 3D annotations) over DDS envelopes; visualizes in Rerun. |
| **DeepSense 6G → SpatialDDS → Rerun** | [`deepsense/`](deepsense/README.md) | Publishes DeepSense 6G Scenario 9 V2I data (60 GHz phased-array beam, FMCW radar, camera, GPS, 2D lidar) over DDS envelopes. |
| **Core v1.6 protocol demo** | [`core_demo/`](core_demo/README.md) | Bootstrap → discovery → coverage query → localization → catalog → anchor flow. Includes a Cesium web UI backed by an HTTP-to-DDS bridge ([`bridges/web_bridge/`](bridges/web_bridge/README.md)). |
| **Benchmarks** | [`benchmarks/`](benchmarks/README.md) | Latency, discovery, multi-operator, and coverage-query benchmark scripts + plotting. |

If you're new here, start with **multi-operator fusion** — it exercises the full
envelope transport with real datasets.

## Bridges

Protocol bridges live under [`bridges/`](bridges/) and work with every demo above
without per-demo wiring:

| Bridge | Path | What it does |
|---|---|---|
| **Web (HTTP/WebSocket)** | [`bridges/web_bridge/`](bridges/web_bridge/README.md) | FastAPI server with two surfaces: legacy REST endpoints (`/v1/localize`, `/v1/catalog/query`, `/v1/stream`) for the Cesium demo, plus a generic subscribe-based protocol (`/ws`, `/api/topics`, `/api/stats`) so any browser app can listen to or publish envelopes by topic pattern with optional rate limits. Ships a 2D top-down canvas dashboard at `/` and a topic-list debug page at `/debug`. |
| **MCAP record / replay** | [`bridges/mcap_bridge/`](bridges/mcap_bridge/README.md) | Records `spatialdds/envelope/v1` traffic to an [MCAP](https://mcap.dev) file and replays it back onto a CycloneDDS domain. Lossless (RELIABLE+KEEP_ALL), Foxglove-compatible, no per-demo wiring. |
| **ROS 2** | [`bridges/ros2_bridge/`](bridges/ros2_bridge/README.md) | Bidirectional bridge between SpatialDDS topics and ROS 2 topics. v0 covers `PoseStamped`, `NavSatFix`, `Imu`, `CompressedImage`, `Detection3DArray`, plus `FusedTrackSet` reverse. Conversion layer is duck-typed so 80% is testable without ROS 2 installed. |
| **MQTT** | [`bridges/mqtt_bridge/`](bridges/mqtt_bridge/README.md) | Bidirectional bridge between SpatialDDS and MQTT (local Mosquitto or AWS IoT Core). MQTT topic = SpatialDDS `logical_topic`, payload = same JSON. QoS/retain inferred from topic suffix (meta = retained, frames = best-effort, decisions = at-least-once). Loop prevention via `_bridge_id` + non-overlapping inbound/outbound filters. |

## Two HTTP servers — which one do I want?

| | `core_demo/http_binding.py` | `bridges/web_bridge/server.py` |
|---|---|---|
| Purpose | Spec-compliance REST wrapper that mirrors the discovery payload shapes | HTTP-to-DDS bridge that the Cesium / canvas dashboard talks to |
| Port (default) | 8080 | 8088 |
| Used by | `core_demo/run_all_tests.sh`, the [core demo README](core_demo/README.md) | `run_bridge_server_docker.sh`, `web/`, `bridges/web_bridge/static/` |
| Run with DDS? | No (in-process registration) | Yes (publishes/subscribes envelopes) |

They are not interchangeable. Use the bridge for the web/canvas demos; use the HTTP
binding when you just want to exercise the registration/search payload shapes.

## Web demo (DDS bridge + Cesium)

Create `web/.env.local` with required values:
```bash
VITE_CESIUM_ION_TOKEN=your_token
VITE_CESIUM_ION_ASSET_ID=your_asset_id
VITE_SPATIALDDS_BRIDGE_URL=http://localhost:8088
```

Run the DDS-backed bridge in Docker, then start the web UI on the host:

```bash
# Start VPS + catalog + bridge (Docker)
./run_bridge_server_docker.sh

# Verify bridge is reachable
curl http://localhost:8088/health

# Start web UI (host)
cd web
npm install
npm run dev
```

Logs are written to `bridges/web_bridge/logs/` — `vps_server_<ts>.log`,
`catalog_server_<ts>.log`, `bridge_server_<ts>.log`.

Stop the bridge when done:
```bash
./stop_bridge_server_docker.sh
```

The same bridge also serves the 2D canvas intersection dashboard at
`http://localhost:8088/` once any of the streaming demos (e.g.
[multi-operator fusion](multi_operator_fusion/README.md) or its
local docker-compose stack at [`deploy/aws/docker-compose.local.yaml`](deploy/aws/docker-compose.local.yaml))
are running.
