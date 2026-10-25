"""
The bridge node's own machinery, on the host, without ROS 2 or a bus.

Nothing tested `bridge_node.py` before this. Its 41 conversion tests exercise
the functions the node calls, not the node, so two names referenced but never
defined — `_TypedWriters` and `_image_chunks` — survived six days behind a
green suite. The node could not start, and once it could, it crashed on the
first image. Only Tier 3 runs the node, and Tier 3 was unreachable because the
tier before it ran a deleted file.

`bridge_node` defers its ROS 2 imports precisely so it stays importable
without them, which is what makes this possible at all. The assertions are
about what the node *decides*: topic, registered type, QoS lane, and whether
bytes travel separately. DDS delivery is the Tier 2 round-trip's. Since the
SDK adoption the writers are real SDK writers on a participant of the test's
own (a stand-in class here would test the stand-in), and each is read for
the lane it was opened on.
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
import spatialdds as sd  # noqa: E402
from spatialdds.dds import blob  # noqa: E402
from spatialdds_demo.lanes import SCENE  # noqa: E402
from ros2_mocks import (  # noqa: E402
    make_test_compressed_image, make_test_imu, make_test_pose_stamped,
)
from frame_mapping import FrameMapper  # noqa: E402


class TypedWriterRouting(unittest.TestCase):
    """
    `_TypedWriters` opens each topic on its registered type's lane.

    This is the class whose absence stopped the bridge from starting.
    """

    def setUp(self):
        try:
            self.participant = sd.participant(
                SCENE, domain=72, service_id="svc:test:ros2-writers")
        except Exception as exc:
            raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")
        self.addCleanup(self.participant.close)
        self.writers = bridge_node._TypedWriters(self.participant)

    def test_resolves_the_type_and_its_registered_lane(self):
        made = self.writers.writer("spatialdds/op/ego/framed_pose/v1",
                                   "framed_pose")
        self.assertEqual(made.topic, "spatialdds/op/ego/framed_pose/v1")
        self.assertEqual(made.type_name, "framed_pose")
        # §3.3.2 puts framed_pose on POSE_RT, and the SDK applies the row.
        self.assertEqual(made.profile.name, "POSE_RT")

    def test_one_writer_per_topic_not_per_message(self):
        first = self.writers.writer("spatialdds/op/ego/framed_pose/v1",
                                    "framed_pose")
        for _ in range(3):
            again = self.writers.writer("spatialdds/op/ego/framed_pose/v1",
                                        "framed_pose")
            self.assertIs(again, first, "writer rebuilt per message")

    def test_distinct_topics_get_distinct_writers(self):
        pose = self.writers.writer("spatialdds/op/ego/framed_pose/v1",
                                   "framed_pose")
        dets = self.writers.writer("spatialdds/op/sensing/detection3d/v1",
                                   "detection3d")
        self.assertIsNot(pose, dets)
        self.assertEqual([pose.profile.name, dets.profile.name],
                         ["POSE_RT", "DET_RT"])

    def test_a_row_with_no_profile_takes_the_declared_one(self):
        """
        §3.3.2 names no profile for navsat_status. The SDK refuses to guess,
        so this deployment declares one (lanes.DECLARED_PROFILES), in the
        open, where `topic_types.profile_for` used to default it silently.
        """
        fix = self.writers.writer("spatialdds/op/gnss/navsat_status/v1",
                                  "navsat_status")
        self.assertEqual(fix.profile.name, "POSE_RT")

    def test_an_unresolvable_type_raises_rather_than_being_skipped(self):
        """
        This bridge is the producer. A producer that cannot build the type it
        is about to advertise has nothing to write, so silence would be worse
        than the exception the caller already logs.
        """
        with self.assertRaises(Exception):
            self.writers.write("spatialdds/op/x/v1", "not_a_registered_type", {})


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
