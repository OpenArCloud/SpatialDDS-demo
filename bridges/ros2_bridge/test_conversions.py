"""Tier-1 conversion tests for the ROS 2 bridge.

No rclpy, no DDS bus. Drives every converter with mock ROS 2 messages from
``test_mocks``, **builds each payload into its IDL type**, and round-trips
back through ``spatialdds_to_ros2`` checking field preservation.

Building into the type is the assertion that matters, and its absence was
expensive: before the typed migration, every one of the five encoders
produced a payload that could not have been a valid SpatialDDS message —
missing required fields, inventing others — and every test here passed,
because the tests asserted against the same invented shape the encoders
produced. Nothing compared either to the IDL. See TestEncodersMatchTheIdl.
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
    compressed_image_blob,
    nav_sat_fix_to_nav_sat_status,
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
    vision_frame_blob_id,
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
        msg = make_test_pose_stamped(x=1.0, y=2.0, z=3.0)
        topic, msg_type, payload = encode_pose_stamped(msg, "op_a",
                                                       FrameMapper("op_a"))
        self.assertEqual(topic, "spatialdds/op_a/ego/pose/v1")
        self.assertEqual(msg_type, MSG_TYPE_FRAMED_POSE)
        # FramedPose is (pose, frame_ref, cov, stamp) and nothing else. The
        # old payload also carried schema_version and source_operator, which
        # the type does not have — the operator is in the topic name.
        self.assertEqual(sorted(payload), ["cov", "frame_ref", "pose", "stamp"])
        self.assertEqual(payload["pose"]["t"], [1.0, 2.0, 3.0])
        self.assertEqual(payload["frame_ref"]["fqn"], "op_a/map")
        self.assertTrue(payload["frame_ref"]["has_coord_convention"])
        self.assertEqual(payload["frame_ref"]["coord_convention"], "ENU")
        # PoseStamped has no covariance, so COV_NONE rather than a zero
        # matrix pretending to be one.
        self.assertEqual(payload["cov"]["discriminator"], "COV_NONE")

    def test_quaternion_passthrough_no_reorder(self):
        msg = make_test_pose_stamped(qx=0.1, qy=0.2, qz=0.3, qw=0.9)
        _t, _mt, payload = encode_pose_stamped(msg, "op", FrameMapper("op"))
        # QuaternionXYZW is an array in [x, y, z, w] order.
        self.assertEqual(payload["pose"]["q"], [0.1, 0.2, 0.3, 0.9])

    def test_roundtrip(self):
        mapper = FrameMapper("op_a")
        original = make_test_pose_stamped(x=5.0, y=-3.0, z=1.5)
        _t, _mt, payload = encode_pose_stamped(original, "op_a", mapper)
        recovered = framed_pose_to_pose_stamped(payload, mapper)
        self.assertEqual(recovered.header.frame_id, original.header.frame_id)
        self.assertAlmostEqual(recovered.pose.position.x, 5.0)
        self.assertAlmostEqual(recovered.pose.position.y, -3.0)
        self.assertAlmostEqual(recovered.pose.position.z, 1.5)


class TestNavSatFix(unittest.TestCase):
    def test_encode(self):
        msg = make_test_nav_sat_fix(lat=37.7749, lon=-122.4194, alt=15.0)
        topic, msg_type, payload = encode_nav_sat_fix(msg, "op_a")
        self.assertEqual(topic, "spatialdds/op_a/geo/gnss/pose/v1")
        self.assertEqual(msg_type, MSG_TYPE_GEO_POSE)
        # GeoPose is lat/lon/alt + orientation + stamp + cov. It has no
        # frame_ref, no sensor_id, and no fix status.
        self.assertEqual(sorted(payload),
                         ["alt_m", "cov", "lat_deg", "lon_deg", "q", "stamp"])
        self.assertAlmostEqual(payload["lat_deg"], 37.7749)
        self.assertAlmostEqual(payload["alt_m"], 15.0)

    def test_fix_status_goes_to_the_registered_companion_type(self):
        """
        GeoPose has no fix-status field, and the old encoder invented one.
        3.3.2 registers `navsat_status` as the "companion to GeoPose" — so
        the bridge publishes both rather than bolting a field onto the pose.
        """
        msg = make_test_nav_sat_fix(status=-1)
        _t, _mt, pose = encode_nav_sat_fix(msg, "op_a")
        self.assertNotIn("fix_status", pose)
        status = nav_sat_fix_to_nav_sat_status(msg, "gnss")
        self.assertEqual(status["fix_type"], "NO_FIX")
        self.assertEqual(status["gnss_id"], "gnss")

    def test_zero_lat_lon_passes(self):
        msg = make_test_nav_sat_fix(lat=0.0, lon=0.0, alt=0.0)
        _t, _mt, payload = encode_nav_sat_fix(msg, "op")
        self.assertEqual(payload["lat_deg"], 0.0)
        self.assertEqual(payload["lon_deg"], 0.0)

    def test_roundtrip(self):
        mapper = FrameMapper("op_a")
        original = make_test_nav_sat_fix(lat=51.5, lon=-0.12, alt=35.0)
        _t, _mt, payload = encode_nav_sat_fix(original, "op_a")
        status = nav_sat_fix_to_nav_sat_status(original, "gnss")
        recovered = geo_pose_to_nav_sat_fix(payload, mapper, status=status)
        self.assertAlmostEqual(recovered.latitude, 51.5)
        self.assertAlmostEqual(recovered.longitude, -0.12)
        self.assertAlmostEqual(recovered.altitude, 35.0)


class TestImu(unittest.TestCase):
    def test_encode(self):
        msg = make_test_imu(ax=0.1, ay=0.2, az=9.81, gx=0.01, gy=0.02, gz=0.03)
        topic, msg_type, payload = encode_imu(msg, "op_a", "imu0",
                                              FrameMapper("op_a"))
        self.assertEqual(topic, "spatialdds/op_a/imu/imu0/sample/v1")
        self.assertEqual(msg_type, MSG_TYPE_IMU_SAMPLE)
        # ImuSample is (imu_id, accel, gyro, stamp, source_id, seq). The old
        # payload used ROS 2's field names — linear_acceleration,
        # angular_velocity, orientation — none of which the type has.
        # accel/gyro covariances were added to ImuSample in 1.7's
        # findings-batch-2 revision; before that the only home for a ROS 2
        # IMU's noise characteristics was nowhere.
        self.assertEqual(sorted(payload),
                         ["accel", "accel_cov", "gyro", "gyro_cov",
                          "has_accel_cov", "has_gyro_cov", "imu_id", "seq",
                          "source_id", "stamp"])
        self.assertEqual(payload["accel"], [0.1, 0.2, 9.81])
        self.assertEqual(payload["gyro"], [0.01, 0.02, 0.03])
        self.assertEqual(payload["imu_id"], "imu0")

    def test_orientation_is_dropped_because_the_type_has_no_field_for_it(self):
        """
        sensor_msgs/Imu carries an orientation and three covariances;
        vio::ImuSample carries neither. Dropped rather than smuggled — a
        fused attitude is a FramedPose. On the findings list.
        """
        _t, _mt, payload = encode_imu(make_test_imu(), "op", "imu0",
                                      FrameMapper("op"))
        for absent in ("orientation", "has_orientation",
                       "orientation_covariance"):
            self.assertNotIn(absent, payload)

    def test_roundtrip(self):
        mapper = FrameMapper("op_a")
        original = make_test_imu(ax=1.0, ay=2.0, az=3.0, gx=0.1, gy=0.2, gz=0.3)
        _t, _mt, payload = encode_imu(original, "op_a", "imu0", mapper)
        recovered = imu_sample_to_imu(payload, mapper)
        self.assertAlmostEqual(recovered.linear_acceleration.x, 1.0)
        self.assertAlmostEqual(recovered.angular_velocity.z, 0.3)
        # REP-145: orientation genuinely is not provided, so the sentinel is
        # the truth rather than a fallback.
        self.assertEqual(recovered.orientation_covariance[0], -1.0)


class TestCompressedImage(unittest.TestCase):
    def test_encode_jpeg(self):
        msg = make_test_compressed_image(fmt="jpeg", data=b"\xff\xd8\xff\xe0")
        topic, msg_type, payload = encode_compressed_image(
            msg, "op_a", "cam0", FrameMapper("op_a"), frame_seq=7)
        self.assertEqual(topic, "spatialdds/op_a/vision/cam0/frame/v1")
        self.assertEqual(msg_type, MSG_TYPE_VISION_FRAME)
        self.assertEqual(payload["codec"], "JPEG")
        self.assertEqual(payload["frame_seq"], 7)
        self.assertEqual(payload["stream_id"], "cam0")

    def test_bytes_are_not_in_the_frame_message(self):
        """
        The spec is explicit that heavy content is never inlined: a frame is
        metadata plus a BlobRef, and the bytes travel as blob chunks. The old
        encoder inlined them as a hex string under a `data_hex` key
        VisionFrame does not have, so it silently vanished on the wire.
        """
        raw = b"\xff\xd8" + bytes(range(256)) * 4
        msg = make_test_compressed_image(fmt="jpeg", data=raw)
        _t, _mt, payload = encode_compressed_image(msg, "op", "cam0",
                                                   FrameMapper("op"))
        self.assertNotIn("data_hex", payload)
        blobs = payload["hdr"]["blobs"]
        self.assertEqual(blobs[0]["role"], "image")
        self.assertTrue(blobs[0]["checksum"].startswith("sha256:"))

        chunks = list(compressed_image_blob(msg, "cam0"))
        self.assertTrue(chunks)
        rebuilt = b"".join(bytes(c.data) for c in chunks)
        self.assertEqual(rebuilt, raw)
        self.assertEqual(blobs[0]["blob_id"], chunks[0].blob_id)

    def test_png_is_announced_as_png(self):
        """
        `Codec` gained PNG in 1.7's findings-batch-2 revision. Before that a
        PNG had to go out as CODEC_NONE — wrong in a way a consumer cannot
        detect, and ROS 2 uses PNG routinely for depth and mask imagery.
        """
        msg = make_test_compressed_image(fmt="png", data=b"\x89PNG")
        _t, _mt, payload = encode_compressed_image(msg, "op", "cam0",
                                                   FrameMapper("op"))
        self.assertEqual(payload["codec"], "PNG")

    def test_roundtrip_bytes_preserved(self):
        mapper = FrameMapper("op_a")
        raw = bytes(range(64))
        msg = make_test_compressed_image(fmt="jpeg", data=raw)
        _t, _mt, payload = encode_compressed_image(msg, "op_a", "cam0", mapper)
        blob = b"".join(bytes(c.data)
                        for c in compressed_image_blob(msg, "cam0"))
        recovered = vision_frame_to_compressed_image(payload, mapper, data=blob)
        self.assertEqual(recovered.data, raw)
        self.assertEqual(recovered.format, "jpeg")
        self.assertEqual(vision_frame_blob_id(payload), "cam0_100_750000000")


class TestDetection3DArray(unittest.TestCase):
    def test_encode(self):
        arr = make_test_detection3d_array(n=3)
        topic, msg_type, payload = encode_detection3d_array(
            arr, "op_a", FrameMapper("op_a"))
        self.assertEqual(topic, "spatialdds/op_a/sensing/detection3d/v1")
        # Not the registered `radar_detection`: this writes onto the same
        # topic the fusion demo reads, and a topic is one type.
        self.assertEqual(msg_type, "detection3d")
        self.assertEqual(payload["source_id"], "op_a")
        self.assertEqual(len(payload["dets"]), 3)
        first = payload["dets"][0]
        self.assertEqual(first["center"], [0.0, 5.0, 1.0])
        self.assertEqual(first["size"], [4.5, 1.8, 1.6])
        self.assertEqual(first["det_id"], "det_0")

    def test_velocity_absent_is_flagged_not_zeroed(self):
        """ROS 2's Detection3DArray has no velocity. The flag says so rather
        than a zero vector passing as a measurement."""
        _t, _mt, payload = encode_detection3d_array(
            make_test_detection3d_array(n=1), "op", FrameMapper("op"))
        self.assertFalse(payload["dets"][0]["has_velocity"])

    def test_top_hypothesis_chosen(self):
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
        _t, _mt, payload = encode_detection3d_array(
            Detection3DArray(detections=[det]), "op", FrameMapper("op"))
        first = payload["dets"][0]
        self.assertEqual(first["class_id"], "car")
        self.assertAlmostEqual(first["score"], 0.8)

    def test_empty_results(self):
        det = Detection3D(header=Header(frame_id="map"), id="d", results=[],
                          bbox=BoundingBox3D())
        _t, _mt, payload = encode_detection3d_array(
            Detection3DArray(detections=[det]), "op", FrameMapper("op"))
        first = payload["dets"][0]
        self.assertEqual(first["class_id"], "unknown")
        self.assertEqual(first["score"], 0.0)

    def test_roundtrip(self):
        mapper = FrameMapper("op_a")
        original = make_test_detection3d_array(n=5)
        _t, _mt, payload = encode_detection3d_array(original, "op_a", mapper)
        recovered = detection3d_set_to_array(payload, mapper)
        self.assertEqual(len(recovered.detections), 5)
        for orig, recov in zip(original.detections, recovered.detections):
            self.assertAlmostEqual(orig.bbox.center.position.x,
                                   recov.bbox.center.position.x)
            self.assertAlmostEqual(orig.bbox.size.x, recov.bbox.size.x)
            self.assertEqual(orig.results[0].hypothesis.class_id,
                             recov.results[0].hypothesis.class_id)
            self.assertTrue(recov.id.endswith(orig.id))
            self.assertTrue(recov.id.startswith("op_a/"))


class TestEncodersMatchTheIdl(unittest.TestCase):
    """
    Every encoder produces something that builds into the type it names.

    This is the test whose absence let all five encoders emit invalid
    SpatialDDS messages indefinitely with a green suite: the shape tests
    above check the fields this bridge cares about, and only ``from_json``
    checks the fields the *spec* requires.
    """

    def _check(self, type_name, payload):
        try:
            from spatialdds_demo import topic_types
            from spatialdds_demo.json_mapping import from_json
        except Exception as exc:                       # pragma: no cover
            self.skipTest(f"generated bindings unavailable: {exc}")
        cls = topic_types.try_resolve(type_name)
        self.assertIsNotNone(cls, f"{type_name!r} resolves to no class")
        return from_json(cls, payload)

    def test_every_encoder(self):
        mapper = FrameMapper("op_a")
        cases = {
            "pose": encode_pose_stamped(make_test_pose_stamped(), "op_a", mapper),
            "navsat": encode_nav_sat_fix(make_test_nav_sat_fix(), "op_a"),
            "imu": encode_imu(make_test_imu(), "op_a", "imu0", mapper),
            "image": encode_compressed_image(make_test_compressed_image(),
                                             "op_a", "cam0", mapper),
            "det3d": encode_detection3d_array(make_test_detection3d_array(n=2),
                                              "op_a", mapper),
        }
        for name, (_topic, type_name, payload) in cases.items():
            with self.subTest(encoder=name):
                self._check(type_name, payload)

    def test_navsat_status_companion(self):
        self._check("navsat_status",
                    nav_sat_fix_to_nav_sat_status(make_test_nav_sat_fix(), "gnss"))


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
    def test_registered_types_route_to_a_decoder(self):
        for type_name, decoder in (
            ("detection3d", detection3d_set_to_array),
            ("detection3d", detection3d_set_to_array),
            ("fused_track", detection3d_set_to_array),
            ("framed_pose", framed_pose_to_pose_stamped),
            ("geopose", geo_pose_to_nav_sat_fix),
            ("imu_sample", imu_sample_to_imu),
            ("video_frame", vision_frame_to_compressed_image),
        ):
            with self.subTest(type=type_name):
                self.assertIs(msg_type_to_decoder(type_name), decoder)

    def test_unknown_type_has_no_decoder(self):
        self.assertIsNone(msg_type_to_decoder("some.future.type"))

    def test_the_old_private_labels_are_gone(self):
        """
        Each publisher used to invent its own name for the same thing, so
        every consumer kept an alias list. A `NUSC_DET3D_SET` on a topic
        told a consumer nothing it could resolve into a reader.
        """
        for legacy in ("ROS2_DETECTION3D_SET", "NUSC_DET3D_SET",
                       "NUSC_EGO_POSE", "DEEPSENSE_VISION_FRAME",
                       "ROS2_IMU_SAMPLE"):
            with self.subTest(legacy=legacy):
                self.assertIsNone(msg_type_to_decoder(legacy))


class TestNumpyArrayCovariance(unittest.TestCase):
    """
    ROS 2 ships covariance arrays as numpy arrays, where ``arr or []``
    raises "truth value of an array is ambiguous". These guard the explicit
    None checks that fixed it.
    """

    def _numpy(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        return np

    def test_navsatfix_with_numpy_position_covariance(self):
        np = self._numpy()
        msg = make_test_nav_sat_fix()
        msg.position_covariance = np.array(
            [1.0, 0, 0, 0, 1.0, 0, 0, 0, 4.0], dtype=float)
        msg.position_covariance_type = 2
        _t, _mt, payload = encode_nav_sat_fix(msg, "op")
        # GeoPose carries covariance as a CovMatrix union, not a bare list.
        # The position case is COV_POS3 carrying `pos`.
        self.assertEqual(payload["cov"]["discriminator"], "COV_POS3")
        self.assertEqual(payload["cov"]["pos"][8], 4.0)

    def test_navsatfix_without_covariance_is_cov_none(self):
        msg = make_test_nav_sat_fix()
        msg.position_covariance_type = 0
        _t, _mt, payload = encode_nav_sat_fix(msg, "op")
        self.assertEqual(payload["cov"]["discriminator"], "COV_NONE")

    def test_imu_with_numpy_arrays_does_not_raise(self):
        np = self._numpy()
        msg = make_test_imu()
        msg.orientation_covariance = np.array([-1.0] + [0.0] * 8, dtype=float)
        _t, _mt, payload = encode_imu(msg, "op", "imu0", FrameMapper("op"))
        self.assertEqual(payload["imu_id"], "imu0")


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


class TestUnionCaseNamesAreChecked(unittest.TestCase):
    """
    A union case name the type does not have must be refused.

    cyclonedds accepts one silently and yields a union with no active case,
    which serialises to ``{"discriminator": None}`` — not a valid sample, and
    indistinguishable downstream from a legitimately empty one. This bridge
    wrote its covariance under an invented ``COV_FULL_3X3``/``full3x3`` for
    exactly that reason, and the value simply disappeared.
    """

    def _cov_matrix(self):
        try:
            from spatialdds_idl.spatial.core import CovMatrix
        except Exception as exc:                       # pragma: no cover
            self.skipTest(f"generated bindings unavailable: {exc}")
        return CovMatrix

    def test_unknown_case_is_refused(self):
        from spatialdds_demo.json_mapping import from_json

        with self.assertRaises(ValueError):
            from_json(self._cov_matrix(),
                      {"discriminator": "COV_FULL_3X3", "full3x3": [0.0] * 9})

    def test_real_case_round_trips(self):
        from spatialdds_demo.json_mapping import from_json, to_json

        values = [1.0, 0, 0, 0, 1.0, 0, 0, 0, 4.0]
        out = to_json(from_json(self._cov_matrix(),
                                {"discriminator": "COV_POS3", "pos": values}))
        self.assertEqual(out["discriminator"], "COV_POS3")
        self.assertEqual(out["pos"][8], 4.0)

    def test_bare_discriminator_still_works(self):
        """The Cesium client and the demo's builders write COV_NONE this way."""
        from spatialdds_demo.json_mapping import from_json, to_json

        out = to_json(from_json(self._cov_matrix(),
                                {"discriminator": "COV_NONE"}))
        self.assertEqual(out["discriminator"], "COV_NONE")
