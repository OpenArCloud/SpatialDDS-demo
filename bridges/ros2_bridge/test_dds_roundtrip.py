#!/usr/bin/env python3
"""Tier-2 round-trip test for the ROS 2 bridge, over a real DDS bus.

Drives ROS 2 mocks through the full bridge path on a real CycloneDDS domain:

    mock ROS 2 msg
        --> encode_*            (ros2_to_spatialdds)
        --> a typed writer      (per-type topic, per-type 3.3.3 QoS)
        --> the DDS bus
        --> a typed reader
        --> msg_type_to_decoder (spatialdds_to_ros2)
        --> reconstructed mock ROS 2 msg

The middle two steps are what changed: each type now has its own topic and
its own QoS lane, so this exercises five topics rather than one, and a
payload that is not a valid sample of its type never reaches the bus at all.

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
sys.path.insert(0, str(_BRIDGES))
sys.path.insert(0, str(_REPO_ROOT))

from spatialdds_demo import blob, topic_types, typed_transport as tt  # noqa: E402
from frame_mapping import FrameMapper  # noqa: E402
from ros2_to_spatialdds import (  # noqa: E402
    compressed_image_blob,
    encode_compressed_image,
    encode_detection3d_array,
    encode_imu,
    encode_nav_sat_fix,
    encode_pose_stamped,
)
from spatialdds_to_ros2 import (  # noqa: E402
    msg_type_to_decoder,
    vision_frame_blob_id,
)
from test_mocks import (  # noqa: E402
    make_test_compressed_image,
    make_test_detection3d_array,
    make_test_imu,
    make_test_nav_sat_fix,
    make_test_pose_stamped,
)


# Each type on its own lane with its own 3.3.3 profile, as the bridge does.
PROFILES = {
    "oarc.framed_pose": "POSE_RT",
    "geopose": "POSE_RT",
    "navsat_status": "POSE_RT",
    "oarc.imu_sample": "POSE_RT",
    "video_frame": "VIDEO_LIVE",
    "oarc.detection3d_velocity": "RADAR_RT",
    "oarc.blob_chunk": "GEOM_TILE",
}


def _prewarm_idl(domain: int) -> None:
    """CycloneDDS Python lazily fills its type-object cache the first time a
    Topic is built. Pre-warm in the main thread before the publisher and
    subscriber participants race on it."""
    from cyclonedds.domain import DomainParticipant

    participant = DomainParticipant(domain)
    for i, type_name in enumerate(PROFILES):
        tt.make_writer(participant, f"spatialdds/prewarm/{i}/v1",
                       topic_types.resolve(type_name), PROFILES[type_name])
    time.sleep(0.3)


class _Endpoints:
    """
    A reader and a writer per topic, created on demand.

    Explicitly named rather than discovery-driven: this test publishes onto
    topics no service announces, so there is nothing to discover. A real
    consumer reads announces; a test harness names what it wants.
    """

    def __init__(self, domain: int, on_sample):
        from cyclonedds.domain import DomainParticipant

        self._pub = DomainParticipant(domain)
        self._sub = DomainParticipant(domain)
        self._on_sample = on_sample
        self._writers = {}
        self._readers = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _endpoint_pair(self, topic: str, type_name: str):
        if topic in self._writers:
            return self._writers[topic]
        datatype = topic_types.resolve(type_name)
        profile = PROFILES.get(type_name, "EVENT_RT")
        self._readers[topic] = (
            tt.make_reader(self._sub, topic, datatype, profile), type_name)
        writer = tt.TypedDictWriter(self._pub, topic, datatype, profile)
        self._writers[topic] = writer
        # Let discovery complete before the first sample: most of the spec's
        # profiles are BEST_EFFORT, so a writer used the instant it is built
        # loses its opening samples.
        time.sleep(1.5)
        return writer

    def publish(self, topic: str, type_name: str, payload) -> None:
        self._endpoint_pair(topic, type_name).write(payload)

    def _run(self):
        from spatialdds_demo.json_mapping import to_json

        while not self._stop.is_set():
            for topic, (reader, type_name) in list(self._readers.items()):
                for sample in tt.take_samples(reader):
                    self._on_sample(type_name, topic, to_json(sample))
            self._stop.wait(0.02)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)


class TestEnvelopeRoundtrip(unittest.TestCase):
    domain: int = int(os.getenv("ROS2_BRIDGE_TEST_DOMAIN", "71"))

    @classmethod
    def setUpClass(cls):
        _prewarm_idl(cls.domain)
        cls.received: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
        cls._lock = threading.Lock()

        def on_sample(type_name, topic, payload):
            with cls._lock:
                cls.received[type_name].append((topic, payload))

        cls.endpoints = _Endpoints(cls.domain, on_sample)

    @classmethod
    def tearDownClass(cls):
        cls.endpoints.close()

    def _wait_for(self, msg_type: str, expected: int, timeout_s: float = 8.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.received[msg_type]) >= expected:
                    return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {expected} {msg_type} samples "
                  f"(got {len(self.received[msg_type])})")

    def _publish_and_take(self, topic: str, msg_type: str, payload: dict,
                          *, timeout_s: float = 8.0) -> Tuple[str, dict]:
        """Publish one sample and return the *next* one received under
        ``msg_type``, rather than relying on global indexing across tests."""
        with self._lock:
            before = len(self.received[msg_type])
        self.endpoints.publish(topic, msg_type, payload)
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
        # GeoPose carries covariance as a CovMatrix union; the mock supplies
        # a 3x3, so it comes back as NavSatFix's COVARIANCE_TYPE_KNOWN.
        self.assertEqual(recovered.position_covariance_type, 3)

    def test_imu_roundtrip(self):
        mapper = FrameMapper("op_c")
        original = make_test_imu()
        topic, msg_type, payload = encode_imu(original, "op_c", "imu_0", mapper)
        _t, received_payload = self._publish_and_take(topic, msg_type, payload)

        decoder = msg_type_to_decoder(msg_type)
        recovered = decoder(received_payload, mapper)
        self.assertAlmostEqual(recovered.linear_acceleration.z, 9.78)
        self.assertAlmostEqual(recovered.angular_velocity.z, 0.05)
        # vio::ImuSample has no orientation field, so REP-145's "not
        # provided" sentinel is the truth. On the findings list.
        self.assertEqual(recovered.orientation_covariance[0], -1.0)
        self.assertEqual(recovered.header.frame_id, "imu_0")

    def test_compressed_image_roundtrip(self):
        """
        The frame and its bytes travel separately, which is what the spec
        asks for: a VisionFrame is metadata plus a BlobRef, and the bytes go
        as blob chunks. The old path hex-encoded them into a `data_hex` key
        VisionFrame does not have.
        """
        mapper = FrameMapper("op_d")
        original = make_test_compressed_image(fmt="jpeg")
        topic, msg_type, payload = encode_compressed_image(
            original, "op_d", "cam_front", mapper, frame_seq=42)
        _t, frame = self._publish_and_take(topic, msg_type, payload)

        # The bytes, over the shared blob topic, keyed by (blob_id, index).
        chunks = list(compressed_image_blob(original, "cam_front"))
        self.assertTrue(chunks)
        with self._lock:
            before = len(self.received[blob.BLOB_TYPE])
        for chunk in chunks:
            self.endpoints.publish(blob.BLOB_TOPIC, blob.BLOB_TYPE, chunk)
        self._wait_for(blob.BLOB_TYPE, before + len(chunks), timeout_s=10.0)

        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.oarc_demo import BlobChunk

        reassembler = blob.Reassembler()
        data = None
        with self._lock:
            arrived = [p for _t, p in self.received[blob.BLOB_TYPE][before:]]
        for payload_dict in sorted(arrived, key=lambda p: p["index"]):
            data = reassembler.feed(from_json(BlobChunk, payload_dict)) or data
        self.assertEqual(data, original.data)

        decoder = msg_type_to_decoder(msg_type)
        recovered = decoder(frame, mapper, data=data)
        self.assertEqual(recovered.data, original.data)
        self.assertEqual(recovered.format, "jpeg")
        self.assertEqual(recovered.header.frame_id, "cam_front")
        self.assertTrue(vision_frame_blob_id(frame))

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

    def test_burst_on_a_reliable_lane_loses_nothing(self):
        """
        A reliable lane must not collapse a burst.

        EVENT_RT is RELIABLE, so all 50 must arrive. Planned trajectories go
        here because a missed intent update matters; detections do not.
        """
        topic = "spatialdds/op_burst/plan/burst_ego/trajectory/v1"
        plan_type = "planned_trajectory"
        sys.path.insert(0, str(_REPO_ROOT / "multi_operator_fusion"))
        from spatialdds_types import make_planned_trajectory, make_planned_waypoint

        def plan(i):
            return make_planned_trajectory(
                "burst_ego", f"plan-{i}", i, "scene/intersection",
                waypoints=[make_planned_waypoint(float(i), 0.0, 0.0,
                                                 timestamp_s=float(i))],
                horizon_sec=1.0, replan_rate_hz=2.0, timestamp_s=float(i))

        self.endpoints.publish(topic, plan_type, plan(0))
        time.sleep(1.0)
        with self._lock:
            before = len(self.received[plan_type])
        for i in range(1, 51):
            self.endpoints.publish(topic, plan_type, plan(i))
        self._wait_for(plan_type, before + 50, timeout_s=20.0)
        with self._lock:
            burst = [p for _t, p in self.received[plan_type][before:]]
        self.assertEqual(len({p["plan_id"] for p in burst}), 50)

    def test_realtime_lane_is_allowed_to_drop_and_that_is_the_profile(self):
        """
        What per-type QoS changed, stated as a test.

        The envelope put every stream on one RELIABLE + KEEP_ALL topic, so
        the demo never lost a sample regardless of what the spec said the
        lane was. RADAR_RT is "Partial" in 3.3.3, which DDS renders
        BEST_EFFORT: under a 50-sample microsecond burst it *will* drop, and
        that is the profile working, not a regression. A real-time detection
        lane trades completeness for latency on purpose.

        Asserted loosely — the exact count is a scheduling detail — but the
        direction is the point: this lane is lossy and the reliable one above
        is not.
        """
        mapper = FrameMapper("op_lossy")
        det_type = "oarc.detection3d_velocity"
        topic = "spatialdds/op_lossy/sensing/detection3d/v1"
        self.endpoints.publish(topic, det_type, encode_detection3d_array(
            make_test_detection3d_array(n=1), "op_lossy", mapper)[2])
        time.sleep(1.0)

        with self._lock:
            before = len(self.received[det_type])
        for i in range(50):
            arr = make_test_detection3d_array(n=2)
            arr.detections[0].id = f"burst_{i}"
            _t, _mt, payload = encode_detection3d_array(arr, "op_lossy", mapper)
            self.endpoints.publish(topic, det_type, payload)
        time.sleep(3.0)
        with self._lock:
            burst = [p for _t, p in self.received[det_type][before:]]
        # At least something got through, and every sample that did is a
        # well-formed one — dropping is allowed, corruption is not.
        self.assertGreater(len(burst), 0)
        for payload in burst:
            self.assertTrue(payload["dets"][0]["detection"]["det_id"]
                            .startswith("burst_"))


if __name__ == "__main__":
    unittest.main()
