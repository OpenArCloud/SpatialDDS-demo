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
