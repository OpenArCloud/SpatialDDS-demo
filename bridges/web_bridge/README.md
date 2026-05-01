# SpatialDDS Web Bridge

A FastAPI server that exposes the SpatialDDS bus to browsers and other HTTP/WebSocket
clients. Two generations of endpoints live in the same process:

| Surface | What it is | Who uses it |
|---|---|---|
| **Legacy** (`/v1/...`) | One-shot REST wrappers around localize + catalog, plus a fire-hose WebSocket. | The Cesium web demo under [`web/`](../../web/). Stable. |
| **Generic** (`/ws`, `/api/...`) | Subscribe-based protocol with topic patterns, optional `msg_type` filtering, server-side rate limiting, and browser-to-DDS publishing. | Any browser app that wants to listen to or talk to the SpatialDDS bus without hard-coded topics. |

Both share a single CycloneDDS subscriber on `spatialdds/envelope/v1`, so adding the
generic side doesn't double the DDS load.

## Run

```bash
export SPATIALDDS_TRANSPORT=dds
export CYCLONEDDS_URI=file:///etc/cyclonedds.xml
export SPATIALDDS_DDS_DOMAIN=1
python3 bridges/web_bridge/server.py
```

Default port: 8088. Or via Docker (still recommended for the Cesium demo):

```bash
./run_bridge_server_docker.sh
# stop:
./stop_bridge_server_docker.sh
```

Logs land under `bridges/web_bridge/logs/`.

Environment knobs:

| Var | Default | Effect |
|---|---|---|
| `SPATIALDDS_DDS_DOMAIN` | (required) | CycloneDDS domain id |
| `SPATIALDDS_BRIDGE_ALLOW_PUBLISH` | `1` | Set to `0` to refuse all `publish` messages (read-only mode) |
| `SPATIALDDS_BRIDGE_STATIC_DIR` | `bridges/web_bridge/static` | Directory served at `/static` (debug dashboard lives there) |

## Legacy endpoints (the Cesium demo)

```
GET  /health                  → bridge status + last seen ANNOUNCE
POST /v1/localize             → Phase 3 LOCALIZE_REQUEST one-shot
POST /v1/catalog/query        → Phase 4 CATALOG_QUERY one-shot
WS   /v1/stream               → every received envelope, no filtering
```

`/v1/localize` accepts `{ "prior_geopose": ..., "service_id": ... }` and returns a
LOCALIZE_RESPONSE shape. `/v1/catalog/query` accepts a `geopose`, optional `expr`,
optional `limit`. These remain exactly as the Cesium UI expects.

## Generic protocol (`/ws`)

JSON messages over WebSocket, identified by a `type` field.

### Client → server

```jsonc
// Subscribe (returns a "subscribed" ack)
{
  "type": "subscribe",
  "id": "sub_1",                                          // optional; auto-assigned otherwise
  "pattern": "spatialdds/*/sensing/detection3d/v1",       // glob on logical_topic
  "msg_types": ["Detection3DSet", "ROS2_DETECTION3D_SET"],// optional, AND-ed with pattern
  "max_rate_hz": 5.0                                      // optional server-side throttle
}

// Unsubscribe
{ "type": "unsubscribe", "id": "sub_1" }

// Publish a SpatialDDS envelope back to the DDS bus
{
  "type": "publish",
  "msg_type": "ROS2_FRAMED_POSE",
  "logical_topic": "spatialdds/web_client/ego/pose/v1",
  "payload": { "pose": { "t": {"x": 1, "y": 2, "z": 0}, "q": {"x": 0, "y": 0, "z": 0, "w": 1} } }
}

// Heartbeat / liveness probe
{ "type": "ping" }

// List active topics seen on the bus
{ "type": "list_topics" }
```

### Server → client

```jsonc
// Subscription confirmed
{ "type": "subscribed", "id": "sub_1", "pattern": "...", "status": "ok" }

// A relayed envelope (one per message even if multiple of your subs match)
{
  "type": "data",
  "sub_id": "sub_1",
  "msg_type": "Detection3DSet",
  "logical_topic": "spatialdds/operator_a/sensing/detection3d/v1",
  "timestamp_ns": 1714071012500000000,
  "payload": { ... }                  // SpatialDDS payload JSON, verbatim
}

// Topic discovery
{ "type": "topics", "topics": [ {logical_topic, msg_type, rate_hz, message_count, last_seen_ns}, ... ] }

// Heartbeat reply
{ "type": "pong", "server_time_ns": 1714..., "clients_connected": 3, "messages_dispatched": 1247 }

// Acks
{ "type": "unsubscribed", "id": "sub_1", "status": "ok" }
{ "type": "published",    "msg_type": "...", "logical_topic": "...", "status": "ok" }

// Error
{ "type": "error", "message": "...", "ref_id": "sub_1" }   // ref_id present when applicable
```

### REST helpers (no WebSocket required)

```
GET /api/topics           → { topics: [...] }
GET /api/topics?stale_threshold_s=120
GET /api/stats            → { uptime_s, clients_connected, total_dispatched, topics_active, dds_domain, publish_enabled }
```

## Browser client library

[`static/spatialdds-ws-client.js`](static/spatialdds-ws-client.js) is a ~120-line
zero-dep ES6 client. Auto-reconnects with a 2 s backoff, runs a 10 s ping.

```html
<script src="/static/spatialdds-ws-client.js"></script>
<script>
  const sdds = new SpatialDDSClient();          // ws://<host>/ws by default
  await sdds.connect();

  sdds.subscribe(
    "spatialdds/*/sensing/detection3d/v1",
    (msg) => console.log(msg.payload),
    { msgTypes: ["Detection3DSet", "ROS2_DETECTION3D_SET"], maxRateHz: 5 }
  );

  const topics = await sdds.listTopics();       // [{logical_topic, rate_hz, ...}]
</script>
```

## Built-in debug dashboard

If the bridge is running with the default static dir, hit `/static/index.html`
in a browser for a minimal topic browser + raw-message viewer. ~80 LOC of
vanilla HTML/JS. Useful for verifying which topics are live without wiring
up a real client.

## Architecture (one CycloneDDS reader, two fan-outs)

```
SpatialDDS bus (envelope topic)
        │
        ▼
  DDSTransport (sync poll thread)
        │
        ├──► _emit_dds_event ────► DDSEventBroadcaster ──► WS /v1/stream  (legacy)
        │
        └──► loop.call_soon_threadsafe(...)
                    │
                    ▼
              ClientManager.dispatch  ──► WS /ws  (generic, per-client filters)
                                       \\─ TopicRouter (stats + rate limits)

  WS /ws  publish messages ──► EnvelopePublisher (RELIABLE+KEEP_ALL writer)
                                          │
                                          ▼
                                   SpatialDDS bus
```

The `EnvelopePublisher` and `EnvelopeSubscriber` factories are shared with the
MCAP and ROS 2 bridges via [`bridges/envelope_io.py`](../envelope_io.py) so QoS
choices stay aligned.

## Tests

```bash
# Unit tests (no FastAPI, no DDS, no WebSocket — pure pytest)
python3 -m pytest -q bridges/web_bridge/test_router.py bridges/web_bridge/test_client.py

# Integration test (FastAPI TestClient WebSocket round-trip, no real DDS needed
# — uses an in-process bridge between dispatch and a synthetic envelope source)
python3 -m pytest -q bridges/web_bridge/test_integration.py
```

## Sibling bridges

- [`bridges/mcap_bridge/`](../mcap_bridge/README.md) — record/replay envelope traffic to MCAP.
- [`bridges/ros2_bridge/`](../ros2_bridge/README.md) — bidirectional bridge between SpatialDDS and ROS 2.
