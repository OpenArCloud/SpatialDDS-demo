"""
The VPS request carries query imagery by reference, and the bytes ride as
`spatial::core::BlobChunk` — chunked at the 65,535-byte bound that spec
Finding 2 (batch 2) reduced `BlobChunk.data` to.

Nothing else in the demo exercises that bound on the *request* side: sensor
frames chunk their payloads, but the VPS query image is the one request-time
blob. This test moves an image larger than a single chunk end-to-end on the
request side — build the typed `argeo::VpsRequest`, confirm its `query_blobs`
carry only references (no inline bytes), then chunk / reassemble the image and
tie the reassembled bytes back to the `BlobRef.checksum` the request advertised.

The wire-level DDS round-trip of `BlobChunk` is covered by the Docker suite
(bridges/ros2_bridge/test_dds_roundtrip.py); this is the host-runnable proof
that the request side is chunk-clean.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import blob  # noqa: E402
from spatialdds_demo.json_mapping import from_json  # noqa: E402
from spatialdds_idl.spatial.argeo import VpsRequest  # noqa: E402
from spatialdds_idl.spatial.core import BlobChunk  # noqa: E402
from spatialdds_test import SpatialDDSClientV15, SpatialDDSLogger  # noqa: E402


class BlobLane(unittest.TestCase):
    """
    The publisher/subscriber pair, without a bus.

    `chunk`/`Reassembler` were always tested; what did not exist was anything
    that put chunks *on* a topic or took them off one. The VPS request
    therefore referenced a blob whose bytes had never been published anywhere,
    and no responder looked, so nothing said so.
    """

    IMAGE = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"jpeg-ish" * 30000  # ~240 KB

    def test_the_lane_is_reliable_and_durable(self):
        """
        A blob with a hole in it is not a blob, and a request can overtake its
        own imagery — so the lane has to be both reliable and TRANSIENT_LOCAL
        for a reader that opens late to still receive every chunk.
        """
        from spatialdds_demo import qos_profiles

        profile = qos_profiles.get(blob.BLOB_PROFILE)
        self.assertTrue(profile.reliable)
        self.assertTrue(profile.latched)

    def test_chunks_reassemble_to_the_advertised_checksum(self):
        blob_id = "11111111-2222-3333-4444-555555555555"
        ref = blob.blob_ref(blob_id, "vps/query-image", self.IMAGE)

        reassembler = blob.Reassembler()
        recovered = None
        for sample in blob.chunk(blob_id, self.IMAGE):
            recovered = reassembler.feed(sample) or recovered

        self.assertEqual(recovered, self.IMAGE)
        # The end-to-end check a responder makes: what arrived is what the
        # BlobRef advertised. Chunk CRC32 catches corruption in transit; this
        # catches a truncated or mismatched blob.
        self.assertEqual(blob.checksum(recovered), ref["checksum"])

    def test_a_corrupt_chunk_is_refused_not_absorbed(self):
        blob_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        chunks = list(blob.chunk(blob_id, self.IMAGE))
        chunks[0].data = bytes(chunks[0].data) + b"tamper"

        with self.assertRaises(blob.CorruptChunk):
            blob.Reassembler().feed(chunks[0])

    def test_out_of_order_chunks_still_reassemble(self):
        """Chunks are keyed (blob_id, index); arrival order is not delivery order."""
        blob_id = "99999999-8888-7777-6666-555555555555"
        chunks = list(blob.chunk(blob_id, self.IMAGE))
        self.assertGreater(len(chunks), 2)

        reassembler = blob.Reassembler()
        recovered = None
        for sample in reversed(chunks):
            recovered = reassembler.feed(sample) or recovered
        self.assertEqual(recovered, self.IMAGE)

    def test_an_incomplete_blob_yields_nothing(self):
        blob_id = "12121212-3434-5656-7878-909090909090"
        chunks = list(blob.chunk(blob_id, self.IMAGE))
        reassembler = blob.Reassembler()
        for sample in chunks[:-1]:
            self.assertIsNone(reassembler.feed(sample))
        self.assertEqual(reassembler.pending, [blob_id])


class VpsBlobPath(unittest.TestCase):
    # Larger than one 65,535-byte chunk so chunking is actually forced.
    IMAGE = b"\x89JPEG-mock-query-image-" * 8000  # ~184 KB

    def _typed_request_with_image(self):
        """A real VpsRequest whose one query blob references self.IMAGE."""
        client = SpatialDDSClientV15(SpatialDDSLogger())
        req = client.create_localize_request("svc:vps-blob-test")
        # Replace the tiny mock blob with a canonical BlobRef for the big image.
        blob_id = req["query_blobs"][0]["blob_id"]
        req["query_blobs"][0] = blob.blob_ref(blob_id, "vps/query-image", self.IMAGE)
        return blob_id, from_json(VpsRequest, req)

    def test_query_imagery_is_by_reference_not_inline(self):
        _, typed = self._typed_request_with_image()
        self.assertTrue(typed.query_blobs, "request carries a query blob")
        for ref in typed.query_blobs:
            # A BlobRef is id + role + checksum — no byte payload rides inline.
            self.assertTrue(ref.blob_id and ref.checksum)
            self.assertFalse(hasattr(ref, "data"))
        self.assertEqual(typed.query_blobs[0].role, "vps/query-image")

    def test_bound_is_the_batch2_ceiling(self):
        self.assertEqual(blob.MAX_CHUNK_BYTES, 65535)

    def test_image_chunks_within_bound_and_reassembles(self):
        blob_id, typed = self._typed_request_with_image()

        chunks = list(blob.chunk(blob_id, self.IMAGE))
        self.assertGreater(len(chunks), 1, "image spans multiple chunks")
        for c in chunks:
            self.assertIsInstance(c, BlobChunk)
            self.assertLessEqual(len(c.data), blob.MAX_CHUNK_BYTES)
            self.assertEqual(c.blob_id, blob_id)
            self.assertEqual(c.total_chunks, len(chunks))

        reassembler = blob.Reassembler()
        recovered = None
        for c in chunks:
            recovered = reassembler.feed(c) or recovered
        self.assertEqual(recovered, self.IMAGE)
        # The bytes tie back to the reference the request advertised.
        self.assertEqual(blob.checksum(recovered), typed.query_blobs[0].checksum)


if __name__ == "__main__":
    unittest.main()
