"""MCAP-only round-trip tests for the SpatialDDS bridge.

These exercise the file-format path without a DDS bus: they drive the
recorder's ``_ChannelTable`` directly with real payloads and read back with
the standard MCAP reader. End-to-end record→replay over DDS lives in
``test_live.py``.

The payloads are built with the publishers' own helpers and round-tripped
through their IDL types, so what is written here is what would actually be
recorded — and the generated schema is asserted against it.
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
_REPO_ROOT = _HERE.parent.parent
for _p in (str(_HERE), str(_REPO_ROOT), str(_REPO_ROOT / "multi_operator_fusion")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mcap.reader import make_reader  # noqa: E402
from mcap.writer import Writer  # noqa: E402

from recorder import _ChannelTable  # noqa: E402
from schema_registry import (  # noqa: E402
    build_schema_table,
    default_schema,
    schema_for,
)

SCENE = "scene/intersection"


def _wire(cls, payload):
    """The payload as recorded: built into its type, then serialised."""
    from spatialdds_demo.json_mapping import from_json, to_json

    return to_json(from_json(cls, payload))


def _samples():
    """``[(type_name, topic, payload, stamp_ns)]`` — real payloads."""
    from spatialdds_idl.oarc_demo import FusedTrackSet
    from spatialdds_idl.spatial.semantics import Detection3DSet
    from spatialdds_idl.spatial.core import FramedPose
    from spatialdds_idl.spatial.disco import Announce
    from fusion import FusedTrack, Position, Velocity
    from spatialdds_types import (
        circle_coverage, make_announce, make_detection, make_detection_set,
        make_framed_pose, make_fused_track_set,
        topic_meta,
    )

    det = make_detection(
        det_id="d1", class_id="vehicle.car", score=0.9, center=(1.0, 2.0, 0.0),
        size=(4.5, 1.8, 1.6), q=(0.0, 0.0, 0.0, 1.0), frame_ref_fqn=SCENE,
        timestamp_s=100.0, source_id="operator_a")
    track = FusedTrack(
        track_id="t1", position=Position(0.0, 0.0, 0.0),
        velocity=Velocity(0.0, 0.0, 0.0), position_uncertainty=0.3,
        object_class="vehicle.car", confidence=0.9,
        source_operators=["operator_a"], source_modalities=["det3d"],
        source_count=1, timestamp=102.0, track_age=1.0)

    return [
        ("detection3d",
         "spatialdds/operator_a/sensing/detection3d/v1",
         _wire(Detection3DSet, make_detection_set(
             set_id="s1", source_operator="operator_a", frame_ref_fqn=SCENE,
             dets=[det],
             frame_seq=7, timestamp_s=100.0)),
         100_000_000_000),
        ("framed_pose", "spatialdds/operator_a/ego/pose/v1",
         _wire(FramedPose, make_framed_pose(
             1.0, 2.0, 0.0, q=(0.0, 0.0, 0.0, 1.0),
             frame_ref_fqn="operator_a/map", timestamp_s=100.5)),
         100_500_000_000),
        ("oarc.fused_track", "spatialdds/platform/fusion/track/v1",
         _wire(FusedTrackSet, make_fused_track_set([track], timestamp_s=102.0)),
         102_000_000_000),
        ("spatialdds/discovery/announce",
         "spatialdds/operator_a/discovery/announce/v1",
         _wire(Announce, make_announce(
             operator="operator_a", service_kind="SENSING",
             topics=[topic_meta("spatialdds/operator_a/ego/pose/v1",
                                "framed_pose", "POSE_RT")],
             coverage=circle_coverage(0.0, 0.0, 80.0), timestamp_s=99.0)),
         99_000_000_000),
    ]


def _write(samples, path: str) -> None:
    schemas = build_schema_table()
    with open(path, "wb") as f:
        writer = Writer(f)
        writer.start(profile="x-jsonschema", library="spatialdds-mcap-bridge-tests")
        table = _ChannelTable(writer, schemas)
        for type_name, topic, payload, stamp_ns in samples:
            ch_id = table.channel_id(topic, type_name)
            writer.add_message(channel_id=ch_id, log_time=stamp_ns,
                               publish_time=stamp_ns,
                               data=json.dumps(payload).encode("utf-8"))
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

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="spatialdds_mcap_")
        self.path = os.path.join(self.tmp, "test.mcap")
        self.samples = _samples()

    def tearDown(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)
        os.rmdir(self.tmp)

    def test_table_covers_every_registered_type(self) -> None:
        from spatialdds_demo import topic_types

        table = build_schema_table()
        for name in topic_types.ALL:
            with self.subTest(type=name):
                self.assertIn(name, table)
                self.assertEqual(table[name]["type"], "object")

    def test_schema_is_generated_from_the_idl_not_permissive(self) -> None:
        """
        The point of the migration for MCAP: a recording now says what is in
        its messages, not just what they are called. Under the envelope every
        schema was `{"type": "object", "additionalProperties": true}`.
        """
        schema = schema_for("framed_pose")
        self.assertNotEqual(schema, default_schema("framed_pose"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(sorted(schema["properties"]),
                         ["cov", "frame_ref", "pose", "stamp"])
        # Vec3 is a fixed-length array of doubles, and the schema says so.
        t = schema["properties"]["pose"]["properties"]["t"]
        self.assertEqual(t["type"], "array")
        self.assertEqual((t["minItems"], t["maxItems"]), (3, 3))
        self.assertEqual(t["items"]["type"], "number")
        # §2.8: enums are their identifier strings on the wire.
        conv = schema["properties"]["frame_ref"]["properties"]["coord_convention"]
        self.assertEqual(conv["type"], "string")
        self.assertIn("ENU", conv["enum"])

    def test_recorded_payloads_validate_against_their_schema(self) -> None:
        """A generated schema that the recorder's own output fails is worse
        than no schema at all."""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        for type_name, _topic, payload, _stamp in self.samples:
            if type_name == "spatialdds/discovery/announce":
                continue                      # not a registry type
            with self.subTest(type=type_name):
                jsonschema.validate(payload, schema_for(type_name))

    def test_overrides_replace_generated_schema(self) -> None:
        custom = {"framed_pose": {"type": "object", "title": "custom"}}
        table = build_schema_table(custom)
        self.assertEqual(table["framed_pose"]["title"], "custom")
        self.assertIn("detection3d", table)

    def test_writes_one_channel_per_topic(self) -> None:
        _write(self.samples, self.path)
        with open(self.path, "rb") as f:
            summary = make_reader(f).get_summary()
        self.assertEqual(len(summary.channels),
                         len({s[1] for s in self.samples}))
        schema_names = {s.name for s in summary.schemas.values()}
        for type_name, _topic, _payload, _stamp in self.samples:
            self.assertIn(type_name, schema_names)

    def test_messages_round_trip_preserves_payload_and_metadata(self) -> None:
        _write(self.samples, self.path)
        records = sorted(_read(self.path), key=lambda r: r[2].log_time)
        expected = sorted(self.samples, key=lambda s: s[3])
        self.assertEqual(len(records), len(expected))
        for (schema, channel, message), (type_name, topic, payload, stamp) in zip(
                records, expected):
            self.assertEqual(channel.topic, topic)
            self.assertEqual(schema.name, type_name)
            self.assertEqual(channel.metadata.get("spatialdds_msg_type"), type_name)
            self.assertEqual(channel.metadata.get("spatialdds_version"), "1.7")
            self.assertEqual(message.log_time, stamp)
            self.assertEqual(json.loads(message.data), payload)

    def test_unknown_type_falls_back_to_default_schema(self) -> None:
        """A recorder must never refuse a stream because this build has never
        heard of its type — 3.3.2 treats unknown names as extension points."""
        _write([("totally.unregistered", "spatialdds/custom/topic/v1",
                 {"foo": "bar"}, 200_000_000_000)], self.path)
        records = _read(self.path)
        self.assertEqual(len(records), 1)
        schema, channel, message = records[0]
        self.assertEqual(schema.name, "totally.unregistered")
        self.assertEqual(json.loads(schema.data),
                         default_schema("totally.unregistered"))
        self.assertEqual(channel.topic, "spatialdds/custom/topic/v1")
        self.assertEqual(json.loads(message.data), {"foo": "bar"})

    def test_replayer_recovers_the_type_from_channel_metadata(self) -> None:
        _write(self.samples, self.path)
        expected = {s[0] for s in self.samples}
        for schema, channel, _message in _read(self.path):
            recovered = ((channel.metadata or {}).get("spatialdds_msg_type")
                         or schema.name)
            self.assertIn(recovered, expected)


if __name__ == "__main__":
    unittest.main()
