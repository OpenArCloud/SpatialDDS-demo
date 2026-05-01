"""Tier-1 conversion tests for the ROS 2 bridge.

Pure pytest — no rclpy, no cyclonedds. Drives every converter with mock
ROS 2 messages from ``test_mocks`` and verifies the resulting payload-dict
shape, then round-trips back through ``spatialdds_to_ros2`` and checks
field preservation.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from frame_mapping import FrameMapper, deterministic_uuid  # noqa: E402
from ros2_to_spatialdds import (  # noqa: E402
    MSG_TYPE_DETECTION3D_SET,
    MSG_TYPE_FRAMED_POSE,
    MSG_TYPE_GEO_POSE,
    MSG_TYPE_IMU_SAMPLE,
    MSG_TYPE_VISION_FRAME,
    SCHEMA_CORE,
    SCHEMA_SEMANTICS,
    SCHEMA_VISION,
    encode_compressed_image,
    encode_detection3d_array,
    encode_imu,
    encode_nav_sat_fix,
    encode_pose_stamped,
)
from spatialdds_to_ros2 import (  # noqa: E402
    detection3d_set_to_array,
    framed_pose_to_pose_stamped,
    geo_pose_to_nav_sat_fix,
    imu_sample_to_imu,
    msg_type_to_decoder,
    vision_frame_to_compressed_image,
)
from test_mocks import (  # noqa: E402
    BoundingBox3D,
    Detection3D,
    Detection3DArray,
    Header,
    NavSatStatus,
    ObjectHypothesis,
    ObjectHypothesisWithPose,
    Pose,
    Point,
    Quaternion,
    Time,
    Vector3,
    make_test_compressed_image,
    make_test_detection3d_array,
    make_test_imu,
    make_test_nav_sat_fix,
    make_test_pose_stamped,
)


class TestFrameMapping(unittest.TestCase):
    def test_deterministic_uuid_stable(self):
        u1 = deterministic_uuid("op_a", "base_link")
        u2 = deterministic_uuid("op_a", "base_link")
        self.assertEqual(u1, u2)

    def test_operator_isolation(self):
        u_a = deterministic_uuid("op_a", "base_link")
        u_b = deterministic_uuid("op_b", "base_link")
        self.assertNotEqual(u_a, u_b)

    def test_fqn_format(self):
        m = FrameMapper("fleet_alpha")
        ref = m.frame_id_to_frame_ref("camera_color_optical_frame")
        self.assertEqual(ref["fqn"], "fleet_alpha/camera_color_optical_frame")
        # UUID is a valid uuidv5 string
        self.assertEqual(len(ref["uuid"]), 36)

    def test_roundtrip_local(self):
        m = FrameMapper("op")
        ref = m.frame_id_to_frame_ref("lidar_top")
        self.assertEqual(m.frame_ref_to_frame_id(ref), "lidar_top")

    def test_roundtrip_foreign_via_fqn(self):
        """Frames the mapper has never seen are recovered from FQN."""
        m = FrameMapper("op")
        # Pretend we got this from another publisher
        foreign = {"uuid": "00000000-0000-0000-0000-000000000000",
                    "fqn": "op/external_frame"}
        self.assertEqual(m.frame_ref_to_frame_id(foreign), "external_frame")


class TestPoseStamped(unittest.TestCase):
    def test_encode_shape(self):
        ps = make_test_pose_stamped(x=1.5, y=2.5, z=3.5)
        topic, msg_type, payload = encode_pose_stamped(ps, "op_a", FrameMapper("op_a"))
        self.assertEqual(topic, "spatialdds/op_a/ego/pose/v1")
        self.assertEqual(msg_type, MSG_TYPE_FRAMED_POSE)
        self.assertEqual(payload["schema_version"], SCHEMA_CORE)
        self.assertEqual(payload["source_operator"], "op_a")
        self.assertEqual(payload["frame_ref"]["fqn"], "op_a/map")
        self.assertEqual(payload["pose"]["t"], {"x": 1.5, "y": 2.5, "z": 3.5})
        self.assertEqual(payload["stamp"], {"sec": 100, "nanosec": 500_000_000})

    def test_quaternion_passthrough_no_reorder(self):
        """ROS 2 (x,y,z,w) → SpatialDDS (x,y,z,w) — no reordering."""
        ps = make_test_pose_stamped()
        ps.pose.orientation = Quaternion(x=0.1, y=0.2, z=0.3, w=0.9)
        _topic, _mt, payload = encode_pose_stamped(ps, "op", FrameMapper("op"))
        q = payload["pose"]["q"]
        self.assertAlmostEqual(q["x"], 0.1)
        self.assertAlmostEqual(q["y"], 0.2)
        self.assertAlmostEqual(q["z"], 0.3)
        self.assertAlmostEqual(q["w"], 0.9)

    def test_roundtrip(self):
        mapper = FrameMapper("op_x")
        original = make_test_pose_stamped(x=42.0, y=-7.5, z=1.2)
        _t, _mt, payload = encode_pose_stamped(original, "op_x", mapper)
        recovered = framed_pose_to_pose_stamped(payload, mapper)
        self.assertAlmostEqual(recovered.pose.position.x, 42.0)
        self.assertAlmostEqual(recovered.pose.position.y, -7.5)
        self.assertAlmostEqual(recovered.pose.position.z, 1.2)
        # frame_id survives via the FrameMapper cache
        self.assertEqual(recovered.header.frame_id, original.header.frame_id)
        self.assertEqual(recovered.header.stamp.sec, 100)


class TestNavSatFix(unittest.TestCase):
    def test_encode(self):
        fix = make_test_nav_sat_fix(lat=30.267, lon=-97.743, alt=150.0)
        topic, msg_type, payload = encode_nav_sat_fix(fix, "op_a", sensor_id="gnss_0")
        self.assertEqual(topic, "spatialdds/op_a/geo/gnss_0/pose/v1")
        self.assertEqual(msg_type, MSG_TYPE_GEO_POSE)
        self.assertAlmostEqual(payload["lat_deg"], 30.267)
        self.assertAlmostEqual(payload["lon_deg"], -97.743)
        self.assertAlmostEqual(payload["alt_m"], 150.0)
        self.assertEqual(payload["fix_status"], 0)
        # Covariance forwarded since type=2 (covariance known)
        self.assertEqual(len(payload["position_covariance"]), 9)
        self.assertEqual(payload["position_covariance_type"], 2)

    def test_zero_lat_lon_passes(self):
        """Null Island isn't treated as a missing fix."""
        fix = make_test_nav_sat_fix(lat=0.0, lon=0.0, alt=0.0)
        _t, _mt, payload = encode_nav_sat_fix(fix, "op")
        self.assertEqual(payload["lat_deg"], 0.0)
        self.assertEqual(payload["lon_deg"], 0.0)

    def test_no_fix_status_preserved(self):
        fix = make_test_nav_sat_fix()
        fix.status = NavSatStatus(status=-1, service=1)
        _t, _mt, payload = encode_nav_sat_fix(fix, "op")
        self.assertEqual(payload["fix_status"], -1)

    def test_roundtrip(self):
        original = make_test_nav_sat_fix(lat=37.7749, lon=-122.4194, alt=15.0)
        _t, _mt, payload = encode_nav_sat_fix(original, "op", sensor_id="gnss_0")
        recovered = geo_pose_to_nav_sat_fix(payload, FrameMapper("op"))
        self.assertAlmostEqual(recovered.latitude, 37.7749)
        self.assertAlmostEqual(recovered.longitude, -122.4194)
        self.assertAlmostEqual(recovered.altitude, 15.0)
        self.assertEqual(recovered.position_covariance_type, 2)
        self.assertEqual(len(recovered.position_covariance), 9)


class TestImu(unittest.TestCase):
    def test_encode(self):
        imu = make_test_imu()
        mapper = FrameMapper("op_a")
        topic, msg_type, payload = encode_imu(imu, "op_a", "imu_0", mapper)
        self.assertEqual(topic, "spatialdds/op_a/imu/imu_0/sample/v1")
        self.assertEqual(msg_type, MSG_TYPE_IMU_SAMPLE)
        self.assertAlmostEqual(payload["linear_acceleration"]["z"], 9.78)
        self.assertAlmostEqual(payload["angular_velocity"]["z"], 0.05)
        self.assertTrue(payload["has_orientation"])
        self.assertAlmostEqual(payload["orientation"]["w"], 0.9999)

    def test_no_orientation_sentinel(self):
        """REP-145 sentinel: orientation_covariance[0] == -1 → has_orientation=False."""
        imu = make_test_imu()
        imu.orientation_covariance = [-1.0] + [0.0] * 8
        _t, _mt, payload = encode_imu(imu, "op", "imu_0", FrameMapper("op"))
        self.assertFalse(payload["has_orientation"])
        self.assertIsNone(payload["orientation"])

    def test_roundtrip(self):
        mapper = FrameMapper("op_y")
        original = make_test_imu()
        _t, _mt, payload = encode_imu(original, "op_y", "imu_0", mapper)
        recovered = imu_sample_to_imu(payload, mapper)
        self.assertAlmostEqual(recovered.angular_velocity.z, 0.05)
        self.assertAlmostEqual(recovered.linear_acceleration.x, 0.1)
        # has_orientation=True → covariance is zeros (not the -1 sentinel)
        self.assertEqual(recovered.orientation_covariance[0], 0.0)

    def test_no_orientation_roundtrip(self):
        mapper = FrameMapper("op")
        imu = make_test_imu()
        imu.orientation_covariance = [-1.0] + [0.0] * 8
        _t, _mt, payload = encode_imu(imu, "op", "imu_0", mapper)
        recovered = imu_sample_to_imu(payload, mapper)
        # Sentinel preserved end-to-end
        self.assertEqual(recovered.orientation_covariance[0], -1.0)


class TestCompressedImage(unittest.TestCase):
    def test_encode_jpeg(self):
        img = make_test_compressed_image(fmt="jpeg")
        mapper = FrameMapper("op_a")
        topic, msg_type, payload = encode_compressed_image(
            img, "op_a", "cam_front", mapper, frame_seq=42)
        self.assertEqual(topic, "spatialdds/op_a/vision/cam_front/frame/v1")
        self.assertEqual(msg_type, MSG_TYPE_VISION_FRAME)
        self.assertEqual(payload["codec"], "JPEG")
        self.assertEqual(payload["schema_version"], SCHEMA_VISION)
        self.assertEqual(payload["hdr"]["frame_seq"], 42)
        # Hex-encoded bytes round-trip
        self.assertEqual(bytes.fromhex(payload["data_hex"]), img.data)
        self.assertEqual(payload["size_bytes"], len(img.data))

    def test_encode_png(self):
        img = make_test_compressed_image(fmt="png")
        _t, _mt, payload = encode_compressed_image(img, "op", "cam", FrameMapper("op"))
        self.assertEqual(payload["codec"], "PNG")

    def test_roundtrip_bytes_preserved(self):
        mapper = FrameMapper("op")
        original = make_test_compressed_image(fmt="jpeg")
        _t, _mt, payload = encode_compressed_image(original, "op", "cam", mapper)
        recovered = vision_frame_to_compressed_image(payload, mapper)
        self.assertEqual(recovered.data, original.data)
        self.assertEqual(recovered.format, "jpeg")


class TestDetection3DArray(unittest.TestCase):
    def test_encode(self):
        arr = make_test_detection3d_array(n=3)
        mapper = FrameMapper("op_a")
        topic, msg_type, payload = encode_detection3d_array(arr, "op_a", mapper)
        self.assertEqual(topic, "spatialdds/op_a/sensing/detection3d/v1")
        self.assertEqual(msg_type, MSG_TYPE_DETECTION3D_SET)
        self.assertEqual(payload["schema_version"], SCHEMA_SEMANTICS)
        self.assertEqual(payload["source_operator"], "op_a")
        self.assertEqual(len(payload["detections"]), 3)
        # First detection: position (0, 5, 1), size (4.5, 1.8, 1.6)
        self.assertEqual(payload["detections"][0]["center"], {"x": 0.0, "y": 5.0, "z": 1.0})
        self.assertEqual(payload["detections"][0]["size"], {"x": 4.5, "y": 1.8, "z": 1.6})
        self.assertEqual(payload["detections"][0]["det_id"], "det_0")

    def test_top_hypothesis_chosen(self):
        """Multiple hypotheses → highest-scoring one used."""
        det = Detection3D(
            header=Header(frame_id="map"),
            id="det_0",
            results=[
                ObjectHypothesisWithPose(hypothesis=ObjectHypothesis(class_id="truck", score=0.3)),
                ObjectHypothesisWithPose(hypothesis=ObjectHypothesis(class_id="car",   score=0.8)),
                ObjectHypothesisWithPose(hypothesis=ObjectHypothesis(class_id="bus",   score=0.1)),
            ],
            bbox=BoundingBox3D(),
        )
        arr = Detection3DArray(detections=[det])
        _t, _mt, payload = encode_detection3d_array(arr, "op", FrameMapper("op"))
        self.assertEqual(payload["detections"][0]["class_id"], "car")
        self.assertAlmostEqual(payload["detections"][0]["score"], 0.8)

    def test_empty_results(self):
        det = Detection3D(header=Header(frame_id="map"), id="d", results=[],
                           bbox=BoundingBox3D())
        _t, _mt, payload = encode_detection3d_array(
            Detection3DArray(detections=[det]), "op", FrameMapper("op"))
        self.assertEqual(payload["detections"][0]["class_id"], "unknown")
        self.assertEqual(payload["detections"][0]["score"], 0.0)

    def test_roundtrip(self):
        mapper = FrameMapper("op_a")
        original = make_test_detection3d_array(n=5)
        _t, _mt, payload = encode_detection3d_array(original, "op_a", mapper)
        recovered = detection3d_set_to_array(payload, mapper)
        self.assertEqual(len(recovered.detections), 5)
        for orig, recov in zip(original.detections, recovered.detections):
            self.assertAlmostEqual(orig.bbox.center.position.x, recov.bbox.center.position.x)
            self.assertAlmostEqual(orig.bbox.size.x, recov.bbox.size.x)
            self.assertEqual(orig.results[0].hypothesis.class_id,
                              recov.results[0].hypothesis.class_id)
            # Recovered id is prefixed with the operator name
            self.assertTrue(recov.id.endswith(orig.id))
            self.assertTrue(recov.id.startswith("op_a/"))


class TestFusedTrackSetReverse(unittest.TestCase):
    """SpatialDDS → ROS 2 path for the multi-operator fusion service."""

    def test_fused_track_set_decodes(self):
        # Synthetic NUSC_FUSED_TRACK_SET payload — uses ``track_id`` not ``det_id``.
        payload = {
            "schema_version": SCHEMA_SEMANTICS,
            "source_operator": "platform",
            "stamp": {"sec": 100, "nanosec": 0},
            "frame_ref": {"uuid": deterministic_uuid("platform", "map"),
                           "fqn": "platform/map"},
            "frame_seq": 7,
            "tracks": [
                {"track_id": "t1", "class_id": "vehicle.car", "score": 0.92,
                 "center": {"x": 12.0, "y": 3.0, "z": 0.5}, "size": {"x": 4.5, "y": 1.8, "z": 1.6},
                 "q": {"x": 0, "y": 0, "z": 0, "w": 1}},
                {"track_id": "t2", "class_id": "vehicle.truck", "score": 0.7,
                 "center": {"x": 30.0, "y": 1.0, "z": 0.5}, "size": {"x": 7.0, "y": 2.4, "z": 3.0},
                 "q": {"x": 0, "y": 0, "z": 0, "w": 1}},
            ],
        }
        mapper = FrameMapper("platform")
        recovered = detection3d_set_to_array(payload, mapper)
        self.assertEqual(len(recovered.detections), 2)
        self.assertEqual(recovered.detections[0].id, "platform/t1")
        self.assertEqual(recovered.detections[0].results[0].hypothesis.class_id, "vehicle.car")
        self.assertAlmostEqual(recovered.detections[0].bbox.center.position.x, 12.0)
        self.assertAlmostEqual(recovered.detections[1].bbox.size.x, 7.0)


class TestDispatch(unittest.TestCase):
    def test_msg_type_to_decoder_known(self):
        self.assertIs(msg_type_to_decoder("ROS2_DETECTION3D_SET"),
                       __import__("spatialdds_to_ros2").detection3d_set_to_array)
        self.assertIs(msg_type_to_decoder("NUSC_FUSED_TRACK_SET"),
                       __import__("spatialdds_to_ros2").detection3d_set_to_array)
        self.assertIs(msg_type_to_decoder("ROS2_FRAMED_POSE"),
                       __import__("spatialdds_to_ros2").framed_pose_to_pose_stamped)
        self.assertIs(msg_type_to_decoder("DEEPSENSE_UNIT1_GEOPOSE"),
                       __import__("spatialdds_to_ros2").geo_pose_to_nav_sat_fix)

    def test_msg_type_to_decoder_unknown(self):
        self.assertIsNone(msg_type_to_decoder("SOMETHING_ELSE"))
        self.assertIsNone(msg_type_to_decoder(""))


class TestPayloadIsJsonSerializable(unittest.TestCase):
    """Every encoder must return a JSON-serializable dict."""

    def setUp(self):
        self.mapper = FrameMapper("op")

    def test_pose_stamped_serializable(self):
        _t, _mt, p = encode_pose_stamped(make_test_pose_stamped(), "op", self.mapper)
        json.dumps(p)

    def test_nav_sat_fix_serializable(self):
        _t, _mt, p = encode_nav_sat_fix(make_test_nav_sat_fix(), "op")
        json.dumps(p)

    def test_imu_serializable(self):
        _t, _mt, p = encode_imu(make_test_imu(), "op", "imu_0", self.mapper)
        json.dumps(p)

    def test_compressed_image_serializable(self):
        _t, _mt, p = encode_compressed_image(make_test_compressed_image(),
                                              "op", "cam", self.mapper)
        json.dumps(p)

    def test_detection3d_array_serializable(self):
        _t, _mt, p = encode_detection3d_array(make_test_detection3d_array(),
                                                "op", self.mapper)
        json.dumps(p)


if __name__ == "__main__":
    unittest.main()
