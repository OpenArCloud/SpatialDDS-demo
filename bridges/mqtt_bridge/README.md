# SpatialDDS ↔ MQTT Bridge

Typed adapter between an MQTT broker (local Mosquitto, AWS IoT Core, etc.)
and a CycloneDDS domain. Edge devices publish JSON on MQTT topics; the
bridge resolves the topic to a §3.3.2 type, builds the JSON into that type,
and writes a real sample on the QoS profile §3.3.3 assigns it. Outbound
traffic (fused tracks, infrastructure telemetry) is serialised back to JSON
here. DDS carries types; MQTT carries JSON, which is what MQTT clients
expect.

A payload that is not a well-formed sample of its type is refused at the
bridge, with the error attributed to the topic it arrived on — rather than
being republished as well-formed JSON that fails somewhere else later.

The MQTT topic IS the DDS topic — there's no translation table to maintain.

**Loop prevention** uses no payload field. On DDS the bridge's readers carry
`IGNORE_LOCAL_PARTICIPANT`, so they never see its own writes; on MQTT it
tags what it publishes with a `spatialdds_bridge_id` user property and drops
anything arriving with its own id. Transport metadata belongs in transport
headers, and a typed struct has no field for it anyway.

## When to use it

- **Edge devices over WAN** — robots, sensors, base stations that can't
  see your local DDS domain. They speak MQTT to AWS IoT Core; the bridge
  in your VPC fans out onto the SpatialDDS bus.
- **Browser dashboards over WebSockets** — Mosquitto's WebSocket listener
  + the bridge gives you topic-routed JSON streaming with no custom
  protocol. (For richer per-client filtering / publishing, see
  [`bridges/web_bridge/`](../web_bridge/README.md).)
- **Cross-VPC / cross-cloud federation** — two SpatialDDS domains can
  peer through a shared MQTT broker without exposing DDS multicast.

## Layout

```
bridges/mqtt_bridge/
├── topic_mapping.py            type inference + QoS/retain rules + pattern matching
├── config.py                   YAML config schema + env-var overrides
├── bridge.py                   the adapter itself (paho-mqtt + typed DDS)
├── __main__.py                 CLI entry point
├── mosquitto.conf              local-dev Mosquitto config (anonymous, 1883 + WS 9001)
├── config.example.yaml         template config (Mosquitto + AWS IoT Core blocks)
├── docker-compose.test.yaml    Mosquitto + Tier-2 pytest in one shot
├── test_topic_mapping.py       Tier-1 unit tests (no MQTT, no DDS)  →  27 tests
├── test_bridge.py              Tier-2 integration tests (Mosquitto + CycloneDDS)
└── requirements.txt            paho-mqtt, cyclonedds, pyyaml
```

## Run locally

```bash
# Start a local Mosquitto
docker run --rm -p 1883:1883 -p 9001:9001 \
  -v "$(pwd)/bridges/mqtt_bridge/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
  eclipse-mosquitto:2

# In another terminal, install bridge deps + run
python3 -m pip install -r bridges/mqtt_bridge/requirements.txt
SPATIALDDS_DDS_DOMAIN=1 \
  python3 -m bridges.mqtt_bridge \
    --config bridges/mqtt_bridge/config.example.yaml
```

Environment overrides (handy for one-off runs / Docker):

| Var | Effect |
|---|---|
| `MQTT_BROKER`, `MQTT_PORT` | Override `mqtt.broker` / `mqtt.port` in the YAML |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Plain-auth creds |
| `SPATIALDDS_DDS_DOMAIN` | Override `dds.domain_id` |
| `MQTT_BRIDGE_ID` | Override `bridge.bridge_id` (for loop prevention) |

## Wire format

```
MQTT  topic:    spatialdds/<operator>/<family>/<sensor?>/<kind>/v1
MQTT  payload:  the sample as JSON (json_mapping.to_json / from_json)

DDS   topic        = MQTT topic, verbatim
DDS   type         = derived from topic by topic_mapping.infer_msg_type,
                     or from a `spatialdds_msg_type` MQTT v5 user property
DDS   QoS profile  = whatever 3.3.3 assigns that type
                       (or supplied via the MQTT v5 user property
                       `spatialdds_msg_type` on inbound messages)
```

The bridge writes its own `_bridge_id` field into every relayed payload
(both sides) for loop prevention. Subscribers can ignore it.

## QoS / retain mapping

`topic_mapping.QOS_MAP` chooses MQTT QoS + retain by topic-suffix
pattern. Defaults that ship:

| Topic ends with | MQTT QoS | retain | Why |
|---|---|---|---|
| `…/meta/v1` | 1 | true | Latched-meta semantics — late joiners get the latest snapshot |
| `…/binding/v1`, `…/discovery/v1` | 1 | true | Same — slow-changing global state |
| `…/detection3d/v1`, `…/detection2d/v1`, `…/track/v1`, `…/trajectory/v1`, `…/events/v1` | 1 | false | Decisions / fused output — at-least-once |
| `…/pose/v1`, `…/frame/v1`, `…/tensor/v1`, `…/scan/v1`, `…/sample/v1`, `…/coverage/v1` | 0 | false | High-rate sensor streams — fire and forget |
| anything else | 0 | false | Default |

## Direction modes + topic filters

Three modes via `bridge.direction`:

- `bidirectional` (default) — relay MQTT↔DDS in both directions
- `inbound_only` — MQTT publishers → DDS (read-only edge)
- `outbound_only` — DDS publishers → MQTT (broadcast cloud → edge)

`bridge.inbound_topics` and `bridge.outbound_topics` are MQTT-style
patterns (`+` = single segment, `#` = trailing multi-segment). The
defaults shipped in `config.example.yaml` keep `operator_*/...`
inbound and `platform/...` + `infrastructure/...` outbound — non-
overlapping by design so a bidirectional bridge can't ping-pong.

## Loop prevention

Two layers, both required when bridges peer with each other:

1. **`_bridge_id` tagging** — every relayed payload carries this. The
   bridge drops messages with its own id on either side.
2. **Non-overlapping inbound vs outbound filters** — see above.

## Tests

### Tier 1 (host, no MQTT, no DDS)

```bash
python3 -m pytest -q bridges/mqtt_bridge/test_topic_mapping.py
```

27 tests — msg_type inference (every type in TOPIC_TYPE_MAP), QoS
mapping (meta retained, frames best-effort, decisions reliable), MQTT
wildcard matching.

### Tier 2 (Docker — Mosquitto + CycloneDDS)

```bash
cd bridges/mqtt_bridge
docker compose -f docker-compose.test.yaml up --abort-on-container-exit \
  --exit-code-from tests
docker compose -f docker-compose.test.yaml down
```

Brings up Mosquitto and runs `test_bridge.py` inside the
`cyclonedds-python` image. Verifies:

- inbound: MQTT publish → bridge → a typed DDS reader sees a real sample of
  the announced type — and a malformed payload is refused at the bridge
  rather than reaching the bus
- outbound: DDS publish → bridge → MQTT subscriber sees retained /
  qos=1 message
- loop prevention: a payload tagged with the bridge's own `_bridge_id`
  is dropped, not republished
- topic filtering: an out-of-filter DDS message doesn't leak to MQTT

Both Tier 2 sides require the `cyclonedds-python:latest` image. If you
don't already have it locally:

```bash
docker build -t cyclonedds-python .
```

## Deploying with AWS IoT Core

1. Create a Thing for the bridge (or per operator, if you want to
   restrict each operator's pub/sub scope).
2. Download the certificate bundle (device cert, private key, the
   Amazon Root CA).
3. Attach an IoT policy that allows pub/sub on `spatialdds/#`. Example:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       { "Effect": "Allow", "Action": ["iot:Connect"],
         "Resource": "arn:aws:iot:REGION:ACCOUNT:client/spatialdds-bridge-*" },
       { "Effect": "Allow", "Action": ["iot:Subscribe"],
         "Resource": "arn:aws:iot:REGION:ACCOUNT:topicfilter/spatialdds/*" },
       { "Effect": "Allow", "Action": ["iot:Publish", "iot:Receive"],
         "Resource": "arn:aws:iot:REGION:ACCOUNT:topic/spatialdds/*" }
     ]
   }
   ```

4. Update your config:

   ```yaml
   mqtt:
     broker: "<your-endpoint>.iot.<region>.amazonaws.com"
     port: 8883
     client_id: "spatialdds-bridge-01"
     tls:
       ca_cert:     "certs/AmazonRootCA1.pem"
       client_cert: "certs/bridge.pem.crt"
       client_key:  "certs/bridge.private.key"
   ```

For per-operator policies, create a separate Thing for each operator
and restrict its policy to `spatialdds/<operator_name>/#`. The bridge
config can then use `inbound_topics: ["spatialdds/<operator_name>/#"]`.

## Sibling bridges

- [`bridges/web_bridge/`](../web_bridge/README.md) — HTTP/WebSocket bridge for browsers.
- [`bridges/mcap_bridge/`](../mcap_bridge/README.md) — record/replay typed samples to MCAP files.
- [`bridges/ros2_bridge/`](../ros2_bridge/README.md) — bidirectional ROS 2 ↔ SpatialDDS bridge.
