# SpatialDDS ↔ MCAP Bridge

Record any SpatialDDS demo traffic to an [MCAP](https://mcap.dev) file, replay
it back onto a CycloneDDS domain. Works with every demo in this repo
(nuScenes, DeepSense, multi-operator fusion) without per-demo wiring.

## Why it's small

The repo's envelope transport (`nuscenes/dds_envelope_transport.py`) already
ships every payload as JSON inside a single DDS topic
(`spatialdds/envelope/v1`). The bridge is therefore **transport-level**: it
records envelopes verbatim and replays them verbatim — no per-dataclass
serialization, no IDL knowledge, no schema generation. New `msg_type`s the
demos add later just register a permissive default schema automatically.

## Install

```bash
python3 -m pip install -r bridges/requirements.txt
```

Requires `cyclonedds==0.10.5` (already pinned by the rest of the repo) and
`mcap>=1.1.0`. The unit tests don't need cyclonedds — DDS imports are lazy.

## Record

```bash
# Record everything on domain 1 until Ctrl-C
python3 -m bridges.mcap_bridge.recorder out.mcap --domain 1

# Record only one operator for 30 seconds
python3 -m bridges.mcap_bridge.recorder out.mcap --domain 1 --duration 30 \
  --topic 'spatialdds/operator_a/*'

# Multiple filters (any-match)
python3 -m bridges.mcap_bridge.recorder out.mcap --domain 1 \
  --topic 'spatialdds/operator_a/*' \
  --topic 'spatialdds/infrastructure/*'
```

Each unique `logical_topic` becomes one MCAP channel; each unique `msg_type`
becomes one MCAP schema (jsonschema, permissive). Channel metadata carries
`spatialdds_msg_type` and `spatialdds_version` for downstream tools.

## Replay

```bash
# Real-time replay onto domain 1
python3 -m bridges.mcap_bridge.replayer out.mcap --domain 1

# Double speed
python3 -m bridges.mcap_bridge.replayer out.mcap --domain 1 --speed 2.0

# Loop forever
python3 -m bridges.mcap_bridge.replayer out.mcap --domain 1 --loop
```

The replayer reads in log-time order, sleeps to preserve relative spacing,
and republishes via the same `EnvelopeTransport` the publishers use — so
existing subscribers (Rerun viz, fusion service, web bridge) consume the
replay identically to a live publisher.

## End-to-end with a demo

```bash
# Terminal 1 — start a publisher (no Rerun viewer needed for recording)
bash nuscenes/run_docker_demo.sh        # or deepsense / multi_operator_fusion

# Terminal 2 — record 30 s
python3 -m bridges.mcap_bridge.recorder /tmp/nusc.mcap --domain 1 --duration 30

# Terminal 3 — stop the publisher, then replay into Rerun
python3 -m bridges.mcap_bridge.replayer /tmp/nusc.mcap --domain 1
```

The Rerun visualization should look identical to a live run. That's the
acceptance test for the bridge.

## Foxglove

Open the recorded `.mcap` directly in [Foxglove
Studio](https://foxglove.dev/studio) — channels appear under their
`logical_topic` names; the Raw Messages panel shows the SpatialDDS payload
JSON with original field names. No custom decoder needed.

## Custom message types

If you add a new SpatialDDS dataclass / msg_type, the bridge will record it
under a permissive default schema with no code changes. To advertise a
stricter JSON schema (better Foxglove docs / validation), pass
`schema_overrides` to `recorder.record()`:

```python
from bridges.mcap_bridge import recorder
recorder.record(
    "out.mcap",
    domain_id=1,
    schema_overrides={
        "MY_CUSTOM_TYPE": {
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "required": ["foo"],
        },
    },
)
```

Or extend `KNOWN_MSG_TYPES` in `schema_registry.py` if the type is part of
the repo's standard set.

## Tests

```bash
python3 -m pytest -q bridges/mcap_bridge/test_roundtrip.py
```

Six MCAP-only tests: schema-table coverage, channel/schema deduplication,
payload + metadata round-trip, unknown-type fallback, replayer-side metadata
extraction. They don't need DDS.

## Layout

```
bridges/
├── requirements.txt
└── mcap_bridge/
    ├── __init__.py
    ├── schema_registry.py   # 21 known msg_types, permissive default schema, override hook
    ├── recorder.py          # DDS envelope subscriber → MCAP writer
    ├── replayer.py          # MCAP reader → DDS envelope publisher
    ├── test_roundtrip.py    # MCAP-only round-trip tests
    └── README.md            # this file
```

## Sibling: `bridges/web_bridge/`

The HTTP-to-DDS bridge that powers the Cesium web UI lives at
[`bridges/web_bridge/`](../web_bridge/README.md). Both bridges live under
`bridges/` and translate SpatialDDS envelopes to and from another protocol
(HTTP/WebSocket for the web bridge; MCAP files for this one).
