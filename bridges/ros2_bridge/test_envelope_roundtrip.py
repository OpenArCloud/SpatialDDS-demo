#!/usr/bin/env python3
"""Tier-2 envelope round-trip test for the ROS 2 bridge.

Drives ROS 2 mocks through the full bridge path on a real CycloneDDS
domain:

    mock ROS 2 msg
        ──► encode_*  (ros2_to_spatialdds)
        ──► EnvelopePublisher  (lossless RELIABLE+KEEP_ALL writer)
        ──► spatialdds/envelope/v1 on the DDS bus
        ──► EnvelopeSubscriber  (lossless RELIABLE+KEEP_ALL reader)
        ──► msg_type_to_decoder  (spatialdds_to_ros2)
        ──► reconstructed mock ROS 2 msg

Run inside the cyclonedds-python container — needs cyclonedds; does not
need rclpy.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

_HERE = Path(__file__).resolve().parent
_BRIDGES = _HERE.parent
_REPO_ROOT = _BRIDGES.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_BRIDGES))   # for the shared envelope_io
sys.path.insert(0, str(_REPO_ROOT))

from envelope_io import EnvelopePublisher, EnvelopeSubscriber  # noqa: E402
from frame_mapping import FrameMapper  # noqa: E402
from ros2_to_spatialdds import (  # noqa: E402
    encode_compressed_image,
    encode_detection3d_array,
    encode_imu,
    encode_nav_sat_fix,
    encode_pose_stamped,
)
from spatialdds_to_ros2 import msg_type_to_decoder  # noqa: E402
from test_mocks import (  # noqa: E402
    make_test_compressed_image,
    make_test_detection3d_array,
    make_test_imu,
    make_test_nav_sat_fix,
    make_test_pose_stamped,
)


def _prewarm_idl(domain: int) -> None:
    """CycloneDDS Python lazily fills its type-object cache the first time
    a Topic is built. Pre-warm in the main thread before the publisher and
    subscriber participants race on it."""
    from nuscenes.dds_envelope_transport import EnvelopeTransport
    warm = EnvelopeTransport(lambda _e: None, domain, "ros2-bridge-prewarm")
    warm.start()
    time.sleep(0.2)
    warm.stop()


class TestEnvelopeRoundtrip(unittest.TestCase):
    domain: int = int(os.getenv("ROS2_BRIDGE_TEST_DOMAIN", "71"))

    @classmethod
    def setUpClass(cls):
        _prewarm_idl(cls.domain)
        cls.received: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
        cls._lock = threading.Lock()

        def on_envelope(msg_type, logical_topic, payload, _stamp_ns):
            with cls._lock:
                cls.received[msg_type].append((logical_topic, payload))

        cls.subscriber = EnvelopeSubscriber(cls.domain, on_envelope)
        cls.subscriber.start()
        cls.publisher = EnvelopePublisher(cls.domain)
        # Discovery handshake: give the RELIABLE reader/writer a moment.
        time.sleep(2.0)

    @classmethod
    def tearDownClass(cls):
        cls.subscriber.stop()
        cls.publisher.close()

    def _wait_for(self, msg_type: str, expected: int, timeout_s: float = 5.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.received[msg_type]) >= expected:
                    return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {expected} {msg_type} envelopes "
                  f"(got {len(self.received[msg_type])})")

    def _publish_and_take(self, topic: str, msg_type: str, payload: dict,
                            *, timeout_s: float = 5.0) -> Tuple[str, dict]:
        """Publish one envelope and return the *next* envelope received under
        ``msg_type`` (vs. relying on global indexing across tests). Avoids
        cross-test contamination when several tests share a msg_type."""
        with self._lock:
            before = len(self.received[msg_type])
        self.publisher.publish(topic, msg_type, payload)
        self._wait_for(msg_type, before + 1, timeout_s=timeout_s)
        with self._lock:
            return self.received[msg_type][before]

    # ── Single-type round-trips ──────────────────────────────────────────────

    def test_pose_stamped_roundtrip(self):
        mapper = FrameMapper("op_a")
        original = make_test_pose_stamped(x=1.5, y=2.5, z=3.5)
        topic, msg_type, payload = encode_pose_stamped(original, "op_a", mapper)
        received_topic, received_payload = self._publish_and_take(topic, msg_type, payload)
        self.assertEqual(received_topic, topic)

        decoder = msg_type_to_decoder(msg_type)
        self.assertIsNotNone(decoder)
        recovered = decoder(received_payload, mapper)
        self.assertAlmostEqual(recovered.pose.position.x, 1.5)
        self.assertAlmostEqual(recovered.pose.position.y, 2.5)
        self.assertAlmostEqual(recovered.pose.position.z, 3.5)
        self.assertEqual(recovered.header.frame_id, "map")
        self.assertEqual(recovered.header.stamp.sec, 100)

    def test_nav_sat_fix_roundtrip(self):
        original = make_test_nav_sat_fix(lat=37.7749, lon=-122.4194, alt=15.0)
        topic, msg_type, payload = encode_nav_sat_fix(original, "op_b", sensor_id="gnss_0")
        _t, received_payload = self._publish_and_take(topic, msg_type, payload)

        decoder = msg_type_to_decoder(msg_type)
        recovered = decoder(received_payload, FrameMapper("op_b"))
        self.assertAlmostEqual(recovered.latitude, 37.7749)
        self.assertAlmostEqual(recovered.longitude, -122.4194)
        self.assertAlmostEqual(recovered.altitude, 15.0)
        self.assertEqual(recovered.position_covariance_type, 2)

    def test_imu_roundtrip(self):
        mapper = FrameMapper("op_c")
        original = make_test_imu()
        topic, msg_type, payload = encode_imu(original, "op_c", "imu_0", mapper)
        _t, received_payload = self._publish_and_take(topic, msg_type, payload)

        decoder = msg_type_to_decoder(msg_type)
        recovered = decoder(received_payload, mapper)
        self.assertAlmostEqual(recovered.linear_acceleration.z, 9.78)
        self.assertAlmostEqual(recovered.angular_velocity.z, 0.05)
        self.assertAlmostEqual(recovered.orientation.w, 0.9999, places=4)
        self.assertEqual(recovered.header.frame_id, "imu_link")

    def test_compressed_image_roundtrip(self):
        mapper = FrameMapper("op_d")
        original = make_test_compressed_image(fmt="jpeg")
        topic, msg_type, payload = encode_compressed_image(
            original, "op_d", "cam_front", mapper, frame_seq=42)
        _t, received_payload = self._publish_and_take(topic, msg_type, payload)

        decoder = msg_type_to_decoder(msg_type)
        recovered = decoder(received_payload, mapper)
        self.assertEqual(recovered.data, original.data)
        self.assertEqual(recovered.format, "jpeg")
        self.assertEqual(recovered.header.frame_id, "camera_optical")

    def test_detection3d_array_roundtrip(self):
        mapper = FrameMapper("op_e")
        original = make_test_detection3d_array(n=4)
        topic, msg_type, payload = encode_detection3d_array(original, "op_e", mapper)
        _t, received_payload = self._publish_and_take(topic, msg_type, payload)

        decoder = msg_type_to_decoder(msg_type)
        recovered = decoder(received_payload, mapper)
        self.assertEqual(len(recovered.detections), 4)
        self.assertEqual(recovered.detections[0].id, "op_e/det_0")
        self.assertEqual(
            recovered.detections[0].results[0].hypothesis.class_id, "vehicle.car"
        )
        self.assertAlmostEqual(recovered.detections[0].bbox.size.x, 4.5)

    # ── Burst test: many envelopes back-to-back, none lost ──────────────────

    def test_burst_no_loss(self):
        """RELIABLE + KEEP_ALL must not drop samples even when 50 envelopes
        are published in a microsecond burst."""
        mapper = FrameMapper("op_burst")
        before = 0
        with self._lock:
            before = len(self.received["ROS2_DETECTION3D_SET"])
        for i in range(50):
            arr = make_test_detection3d_array(n=2)
            arr.detections[0].id = f"burst_{i}"
            topic, msg_type, payload = encode_detection3d_array(arr, "op_burst", mapper)
            self.publisher.publish(topic, msg_type, payload)
        self._wait_for("ROS2_DETECTION3D_SET", before + 50, timeout_s=10.0)
        with self._lock:
            received_burst = [
                p for _t, p in self.received["ROS2_DETECTION3D_SET"][before:]
            ]
        self.assertEqual(len(received_burst), 50)
        # Sanity: each one's first detection has a unique burst_<i> id
        ids = {p["detections"][0]["det_id"] for p in received_burst}
        self.assertEqual(len(ids), 50)


if __name__ == "__main__":
    unittest.main()
