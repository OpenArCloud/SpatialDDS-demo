"""
The bridge node's own machinery, on the host, without ROS 2 or a bus.

Nothing tested `bridge_node.py` before this. Its 41 conversion tests exercise
the functions the node calls, not the node, so two names referenced but never
defined — `_TypedWriters` and `_image_chunks` — survived six days behind a
green suite. The node could not start, and once it could, it crashed on the
first image. Only Tier 3 runs the node, and Tier 3 was unreachable because the
tier before it ran a deleted file.

`bridge_node` defers its ROS 2 imports precisely so it stays importable
without them, which is what makes this possible at all. The DDS writer is
replaced with a recorder, so the assertions are about what the node *decides*
— topic, registered type, QoS lane, and whether bytes travel separately —
rather than about DDS delivery, which the Tier 2 round-trip already covers.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
for _p in (str(_HERE), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bridge_node  # noqa: E402
from spatialdds_demo import blob  # noqa: E402
from test_mocks import (  # noqa: E402
    make_test_compressed_image, make_test_imu, make_test_pose_stamped,
)
from frame_mapping import FrameMapper  # noqa: E402


class _RecordingWriter:
    """Stands in for a TypedDictWriter; remembers what it was asked to send."""

    def __init__(self, participant, topic, datatype, qos_profile, **_kw):
        self.topic = topic
        self.datatype = datatype
        self.qos_profile = qos_profile
        _RecordingWriter.made.append(self)
        self.written = []

    made: list = []

    def write(self, payload):
        self.written.append(payload)


class TypedWriterRouting(unittest.TestCase):
    """
    `_TypedWriters` resolves a §3.3.2 type to its class and picks its lane.

    This is the class whose absence stopped the bridge from starting.
    """

    def setUp(self):
        _RecordingWriter.made = []
        self._real = bridge_node.tt.TypedDictWriter
        bridge_node.tt.TypedDictWriter = _RecordingWriter
        self.addCleanup(setattr, bridge_node.tt, "TypedDictWriter", self._real)

    def test_resolves_the_type_and_its_registered_lane(self):
        writers = bridge_node._TypedWriters(participant=None)
        writers.write("spatialdds/op/ego/pose/v1", "framed_pose", {"any": "payload"})

        self.assertEqual(len(_RecordingWriter.made), 1)
        made = _RecordingWriter.made[0]
        self.assertEqual(made.topic, "spatialdds/op/ego/pose/v1")
        # §3.3.3 assigns framed_pose to POSE_RT; taking it from the shared
        # table is what stops this bridge drifting from the others.
        self.assertEqual(made.qos_profile, "POSE_RT")
        self.assertEqual(made.written, [{"any": "payload"}])

    def test_one_writer_per_topic_not_per_message(self):
        writers = bridge_node._TypedWriters(participant=None)
        for i in range(3):
            writers.write("spatialdds/op/ego/pose/v1", "framed_pose", {"n": i})
        self.assertEqual(len(_RecordingWriter.made), 1, "writer rebuilt per message")
        self.assertEqual(len(_RecordingWriter.made[0].written), 3)

    def test_distinct_topics_get_distinct_writers(self):
        writers = bridge_node._TypedWriters(participant=None)
        writers.write("spatialdds/op/ego/pose/v1", "framed_pose", {})
        writers.write("spatialdds/op/sensing/detection3d/v1", "detection3d", {})
        self.assertEqual([w.qos_profile for w in _RecordingWriter.made],
                         ["POSE_RT", "DET_RT"])

    def test_an_unresolvable_type_raises_rather_than_being_skipped(self):
        """
        This bridge is the producer. A producer that cannot build the type it
        is about to advertise has nothing to write, so silence would be worse
        than the exception the caller already logs.
        """
        writers = bridge_node._TypedWriters(participant=None)
        with self.assertRaises(Exception):
            writers.write("spatialdds/op/x/v1", "not_a_registered_type", {})


class ImageBytesTravelSeparately(unittest.TestCase):
    """
    `_image_chunks` — the function whose absence crashed the node on the first
    camera frame. A VisionFrame is metadata plus a BlobRef; the pixels ride
    the blob lane.
    """

    MAPPING = {"sensor_id": "cam_front"}

    def test_a_compressed_image_yields_chunks_that_reassemble(self):
        payload = bytes(range(256)) * 700          # ~179 KB, spans chunks
        msg = make_test_compressed_image(data=payload)

        chunks = list(bridge_node._image_chunks(
            bridge_node._enc_compressed_image, msg, self.MAPPING))
        self.assertGreater(len(chunks), 1, "image should span multiple chunks")

        reassembler = blob.Reassembler()
        recovered = None
        for chunk in chunks:
            recovered = reassembler.feed(chunk) or recovered
        self.assertEqual(recovered, payload)

    def test_the_frame_references_the_same_blob_the_chunks_carry(self):
        """
        The BlobRef in the frame and the blob_id on the chunks are what tie
        metadata to bytes. If they disagree, a consumer waits forever for a
        blob nobody is sending.
        """
        payload = b"\\xff\\xd8jpeg-bytes" * 6000
        msg = make_test_compressed_image(data=payload)

        _topic, _type, frame = bridge_node._enc_compressed_image(
            msg, "operator_a", FrameMapper("operator_a"), self.MAPPING)
        refs = (frame.get("hdr") or {}).get("blobs") or frame.get("blobs") or []
        self.assertTrue(refs, "vision frame carries a BlobRef")

        chunks = list(bridge_node._image_chunks(
            bridge_node._enc_compressed_image, msg, self.MAPPING))
        self.assertEqual({c.blob_id for c in chunks}, {refs[0]["blob_id"]})

    def test_an_empty_image_yields_nothing(self):
        msg = make_test_compressed_image(data=b"")
        self.assertEqual(
            list(bridge_node._image_chunks(
                bridge_node._enc_compressed_image, msg, self.MAPPING)), [])

    def test_non_image_encoders_yield_nothing(self):
        """Only the image lane has bytes to send; every other mapping is metadata."""
        for encoder, msg in ((bridge_node._enc_pose, make_test_pose_stamped()),
                             (bridge_node._enc_imu, make_test_imu())):
            with self.subTest(encoder=encoder.__name__):
                self.assertEqual(
                    list(bridge_node._image_chunks(encoder, msg, self.MAPPING)), [])


class TheNodeCanBeConstructed(unittest.TestCase):
    """
    The failure that started this: names referenced by `main` but never
    defined. `test_static_checks.py` catches those statically; this catches
    the case where the name exists but is not callable as `main` uses it.
    """

    def test_every_name_main_reaches_for_exists_and_is_callable(self):
        for name in ("_TypedWriters", "_image_chunks", "_enc_compressed_image",
                     "_enc_pose", "_enc_imu", "_enc_navsat", "_enc_detection3d",
                     "_resolve_ros2_class", "_infer_type_for_topic"):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(bridge_node, name, None)),
                                f"bridge_node.{name} is missing or not callable")


if __name__ == "__main__":
    unittest.main()
