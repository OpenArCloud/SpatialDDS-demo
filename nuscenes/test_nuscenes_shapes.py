"""
Every nuScenes converter emits a valid sample of the type its lane announces.

No dataset and no devkit: each converter is driven with synthetic nuScenes
records and its output built into its IDL type. That build is the assertion —
the converters produce spec types, and only ``from_json`` checks that every
field the *spec* requires is there.

This test is the one whose absence let the nuScenes path emit invalid
SpatialDDS for as long as it existed. The converters built hand-written
dataclasses that mirrored the IDL and had drifted from it — ``StreamMeta``
missing ``schema_version``, ``FrameHeader`` missing ``sensor_pose``
entirely, ``VisionFrame`` missing eight fields, ``LidarFrame`` nine — and
nothing compared them to the spec, because they *were* the demo's definition
of the types. They are the generated types now, so the drift cannot recur;
this catches a converter that stops filling them.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
for _p in (str(_REPO_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

def _load(name: str):
    """
    Import a module from this directory, by path.

    Several demos have a `publisher.py` and there used to be three
    `spatialdds_types.py`; whichever directory reached ``sys.path`` first
    won. Importing by location means this test cannot silently exercise
    another demo's module — which it did, and reported as a skip.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"_nusc_{name}", _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The devkit is a type-annotation import for the converters exercised here.
# Stub it so this runs without the dataset or the package installed.
for _name in ("nuscenes.nuscenes", "nuscenes.utils", "nuscenes.utils.data_classes"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["nuscenes.nuscenes"].NuScenes = type("NuScenes", (), {})
sys.modules["nuscenes.utils.data_classes"].RadarPointCloud = type(
    "RadarPointCloud", (), {})

EGO = {
    "timestamp": 1_531_000_000_000_000,
    "translation": [100.0, 200.0, 0.0],
    "rotation": [0.924, 0.0, 0.0, 0.383],
}
CS = {
    "token": "cs-token",
    "translation": [1.0, 0.0, 1.5],
    "rotation": [0.5, -0.5, 0.5, -0.5],
    "camera_intrinsic": [[1266.0, 0.0, 816.0], [0.0, 1266.0, 491.0],
                         [0.0, 0.0, 1.0]],
}
SD_CAM = {"channel": "CAM_FRONT", "filename": "samples/CAM_FRONT/x.jpg",
          "timestamp": 1_531_000_000_000_000, "width": 1600, "height": 900,
          "token": "cam-token", "calibrated_sensor_token": "cs"}
SD_LIDAR = {"channel": "LIDAR_TOP", "filename": "samples/LIDAR_TOP/x.bin",
            "timestamp": 1_531_000_000_000_000, "token": "lidar-token",
            "calibrated_sensor_token": "cs"}
SD_RADAR = {"channel": "RADAR_FRONT", "timestamp": 1_531_000_000_000_000,
            "token": "radar-token", "calibrated_sensor_token": "cs"}

ANNOTATION = {
    "token": "ann-1", "translation": [1.0, 2.0, 0.5],
    "size": [1.8, 4.5, 1.6], "rotation": [0.924, 0.0, 0.0, 0.383],
    "category_name": "vehicle.car", "visibility_token": "v1",
    "num_lidar_pts": 12, "num_radar_pts": 3, "instance_token": "inst-1",
    "timestamp": 1_531_000_000_000_000,
}


class _FakeNusc:
    """The three devkit lookups ``annotation_to_detection3d`` makes."""

    def get(self, table, token):
        if table == "sample_annotation":
            return dict(ANNOTATION)
        if table == "visibility":
            return {"level": "2"}
        raise KeyError(table)

    def box_velocity(self, _token):
        return (1.0, 0.5, 0.0)


class ConverterShapes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401

            from spatialdds_demo import topic_types  # noqa: F401
        except Exception as exc:                       # pragma: no cover
            raise unittest.SkipTest(f"generated bindings unavailable: {exc}")
        # Not guarded: if the converters cannot be imported that is a
        # failure, not a reason to skip. A skip here hid a module-name
        # collision that made this whole file a no-op.
        cls.n2s = _load("nuscenes_to_spatialdds")
        cls.pub = _load("publisher")
        cls.sensor_types = _load("sensor_types")

    def _check(self, lane, payload):
        from spatialdds_demo import topic_types
        from spatialdds_demo.json_mapping import from_json

        type_name, profile = lane
        cls = topic_types.try_resolve(type_name)
        self.assertIsNotNone(cls, f"{type_name!r} resolves to no class")
        from_json(cls, payload)

    def test_every_converter_emits_its_announced_type(self):
        import numpy as np

        from spatialdds_demo import payloads

        n2s, pub, to_dict = self.n2s, self.pub, self.sensor_types.to_dict

        pose, geopose = n2s.ego_pose_to_spatialdds(EGO)
        stamp = {"sec": EGO["timestamp"] // 1_000_000,
                 "nanosec": (EGO["timestamp"] % 1_000_000) * 1000}

        radar_points = np.zeros((18, 3), dtype=np.float32)
        radar_points[0] = [10.0, 11.0, 12.0]
        radar_points[3] = [0, 1, 4]
        radar_points[5] = [5.5, 6.5, 7.5]

        lidar_meta, lidar_frame = n2s.lidar_to_meta_and_frame(SD_LIDAR, CS, 1)
        cases = {
            "ego pose": (pub.TYPE_EGO_POSE, {
                "pose": to_dict(pose),
                "frame_ref": payloads.frame_ref("nuscenes/map"),
                "cov": dict(payloads.COV_NONE), "stamp": stamp}),
            "geopose": (pub.TYPE_GEO_POSE, {**to_dict(geopose), "stamp": stamp}),
            "vision meta": (pub.TYPE_VISION_META,
                            to_dict(n2s.camera_to_vision_meta(SD_CAM, CS))),
            "vision frame": (pub.TYPE_VISION,
                             to_dict(n2s.sample_data_to_vision_frame(SD_CAM, 1))),
            "lidar meta": (pub.TYPE_LIDAR_META, to_dict(lidar_meta)),
            "lidar frame": (pub.TYPE_LIDAR, to_dict(lidar_frame)),
            "radar set": (pub.TYPE_RAD_DET, to_dict(
                n2s.radar_to_detection_set(SD_RADAR, radar_points, 1))),
            "detection3d set": (pub.TYPE_DET3D, to_dict(
                n2s.sample_annotations_to_set(
                    _FakeNusc(),
                    {"timestamp": EGO["timestamp"], "anns": ["ann-1"]}, 1))),
        }
        for label, (lane, payload) in cases.items():
            with self.subTest(converter=label):
                self._check(lane, payload)

    def test_ego_pose_and_geopose_are_separate_lanes(self):
        """
        The old payload bundled `pose_se3` and `geopose` in one message with
        a frame_seq — a shape no spec type has. A local pose and a geographic
        one are two types, so they are two lanes.
        """
        pub = self.pub

        self.assertNotEqual(pub.TYPE_EGO_POSE, pub.TYPE_GEO_POSE)
        self.assertEqual(pub.TYPE_EGO_POSE[0], "oarc.framed_pose")
        self.assertEqual(pub.TYPE_GEO_POSE[0], "geopose")

    def test_lanes_name_registered_or_documented_types(self):
        from spatialdds_demo import topic_types

        pub = self.pub
        for lane in (pub.TYPE_EGO_POSE, pub.TYPE_GEO_POSE, pub.TYPE_VISION_META,
                     pub.TYPE_VISION, pub.TYPE_LIDAR_META, pub.TYPE_LIDAR,
                     pub.TYPE_RAD_DET, pub.TYPE_DET3D):
            type_name, profile = lane
            with self.subTest(type=type_name):
                self.assertIsNotNone(topic_types.try_resolve(type_name))
                from spatialdds_demo import qos_profiles
                self.assertIsNotNone(qos_profiles.get(profile))


if __name__ == "__main__":
    unittest.main()
