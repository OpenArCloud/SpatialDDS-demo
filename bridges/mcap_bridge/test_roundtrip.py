"""MCAP-only round-trip tests for the SpatialDDS bridge.

These tests exercise the file-format path without requiring CycloneDDS to be
installed: they drive the recorder's `_ChannelTable` directly with synthetic
envelopes, then read back with the standard MCAP reader. End-to-end DDS
record→replay verification lives in the demo Docker scripts.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make `import recorder` / `replayer` / `schema_registry` work whether the
# tests are run via `pytest` from any cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from mcap.reader import make_reader  # noqa: E402
from mcap.writer import Writer  # noqa: E402

from recorder import _ChannelTable  # noqa: E402
from schema_registry import (  # noqa: E402
    KNOWN_MSG_TYPES,
    build_schema_table,
    default_schema,
)


class _FakeEnvelope:
    """Stand-in for SpatialDDSEnvelope (so tests don't need cyclonedds)."""

    def __init__(self, msg_type: str, logical_topic: str, payload: dict, stamp_ns: int):
        self.msg_type = msg_type
        self.logical_topic = logical_topic
        self.payload_json = json.dumps(payload)
        self.stamp_ns = stamp_ns
        self.request_id = ""


# Synthetic samples covering the breadth of demo msg_types; the bridge is
# transport-level so we don't need real dataclasses to validate the code path.
SAMPLES = [
    _FakeEnvelope(
        "NUSC_DET3D_SET",
        "spatialdds/operator_a/sensing/detection3d/v1",
        {"frame_seq": 7, "stamp": {"sec": 100, "nanosec": 0}, "detections": []},
        100_000_000_000,
    ),
    _FakeEnvelope(
        "NUSC_VISION_FRAME",
        "spatialdds/operator_a/vision/CAM_FRONT/frame/v1",
        {"stream_id": "cam_front", "schema_version": "1.7", "codec": "JPEG"},
        100_500_000_000,
    ),
    _FakeEnvelope(
        "DEEPSENSE_RF_BEAM_FRAME",
        "spatialdds/infrastructure/rf_beam/unit1/frame/v1",
        {"stream_id": "bs_beam", "schema_version": "1.7"},
        101_000_000_000,
    ),
    _FakeEnvelope(
        "NUSC_FUSED_TRACK_SET",
        "spatialdds/platform/fusion/track/v1",
        {"frame_seq": 1, "tracks": [{"track_id": "t1"}]},
        102_000_000_000,
    ),
    _FakeEnvelope(
        "ANNOUNCE",
        "spatialdds/operator_a/announce/v1",
        {"service_id": "svc:op_a", "version": "1.7"},
        99_000_000_000,
    ),
]


def _write(envelopes, path: str) -> None:
    schemas = build_schema_table()
    with open(path, "wb") as f:
        writer = Writer(f)
        writer.start(profile="x-jsonschema", library="spatialdds-mcap-bridge-tests")
        table = _ChannelTable(writer, schemas)
        for env in envelopes:
            ch_id = table.channel_id(env.logical_topic, env.msg_type)
            writer.add_message(
                channel_id=ch_id,
                log_time=env.stamp_ns,
                publish_time=env.stamp_ns,
                data=env.payload_json.encode("utf-8"),
            )
        writer.finish()


def _read(path: str):
    with open(path, "rb") as f:
        reader = make_reader(f)
        return list(reader.iter_messages(log_time_order=True))


class RoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="spatialdds_mcap_")
        self.path = os.path.join(self.tmp, "test.mcap")

    def tearDown(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)
        os.rmdir(self.tmp)

    def test_known_msg_types_table(self) -> None:
        table = build_schema_table()
        for name in KNOWN_MSG_TYPES:
            self.assertIn(name, table)
            self.assertEqual(table[name]["type"], "object")

    def test_overrides_extend_table(self) -> None:
        custom = {"MY_CUSTOM_TYPE": {"type": "object", "properties": {"x": {"type": "number"}}}}
        table = build_schema_table(custom)
        self.assertIn("MY_CUSTOM_TYPE", table)
        # known types still present
        self.assertIn("NUSC_DET3D_SET", table)

    def test_writes_one_channel_per_logical_topic(self) -> None:
        _write(SAMPLES, self.path)
        with open(self.path, "rb") as f:
            reader = make_reader(f)
            summary = reader.get_summary()
            channels = summary.channels
            self.assertEqual(len(channels), len({s.logical_topic for s in SAMPLES}))
            schema_names = {s.name for s in summary.schemas.values()}
            for s in SAMPLES:
                self.assertIn(s.msg_type, schema_names)

    def test_messages_round_trip_preserves_payload_and_metadata(self) -> None:
        _write(SAMPLES, self.path)
        records = _read(self.path)
        self.assertEqual(len(records), len(SAMPLES))
        # Sort both sides by log_time so we can compare positionally
        records.sort(key=lambda r: r[2].log_time)
        expected = sorted(SAMPLES, key=lambda e: e.stamp_ns)
        for (schema, channel, message), env in zip(records, expected):
            self.assertEqual(channel.topic, env.logical_topic)
            self.assertEqual(schema.name, env.msg_type)
            self.assertEqual(channel.metadata.get("spatialdds_msg_type"), env.msg_type)
            self.assertEqual(channel.metadata.get("spatialdds_version"), "1.7")
            self.assertEqual(message.log_time, env.stamp_ns)
            self.assertEqual(json.loads(message.data), json.loads(env.payload_json))

    def test_unknown_msg_type_falls_back_to_default_schema(self) -> None:
        unknown = _FakeEnvelope(
            "TOTALLY_UNREGISTERED",
            "spatialdds/custom/topic/v1",
            {"foo": "bar"},
            200_000_000_000,
        )
        _write([unknown], self.path)
        records = _read(self.path)
        self.assertEqual(len(records), 1)
        schema, channel, message = records[0]
        self.assertEqual(schema.name, "TOTALLY_UNREGISTERED")
        # Default schema is permissive
        self.assertEqual(json.loads(schema.data), default_schema("TOTALLY_UNREGISTERED"))
        self.assertEqual(channel.topic, "spatialdds/custom/topic/v1")
        self.assertEqual(json.loads(message.data), {"foo": "bar"})

    def test_replayer_metadata_extracts_msg_type(self) -> None:
        """The replayer reads `msg_type` from channel metadata, falling back
        to schema name. Both paths should yield the original msg_type."""
        _write(SAMPLES, self.path)
        records = _read(self.path)
        for schema, channel, _message in records:
            recovered = (
                channel.metadata.get("spatialdds_msg_type") if channel.metadata else None
            ) or schema.name
            self.assertIn(recovered, {s.msg_type for s in SAMPLES})


if __name__ == "__main__":
    unittest.main()
