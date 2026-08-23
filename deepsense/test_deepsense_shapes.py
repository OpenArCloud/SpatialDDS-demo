"""
Every DeepSense converter emits a valid sample of the type its lane announces.

No dataset: each converter is driven with synthetic Scenario 9 rows and its
output built into its IDL type. Same rationale as the nuScenes equivalent —
these converters built hand-written dataclasses mirroring the spec's
provisional `rf_beam` and `rad` profiles, and nothing compared them to the
IDL, so they had drifted. They are the generated types now.

The file-reading converters (beam frame, radar tensor, lidar, geoposes) need
real Scenario 9 files, so they are exercised through small fakes rather than
skipped: the point is the payload shape, not the file format.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
for _p in (str(_REPO_ROOT), str(_HERE), str(_REPO_ROOT / "nuscenes")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ROW = {
    "index": "37", "seq_index": "1",
    "unit1_rgb": "./unit1/camera/img_0037.jpg",
    "unit1_radar": "./unit1/radar/rad_0037.mat",
    "unit1_pwr_60ghz": "./unit1/mmWave/pwr_0037.txt",
    "unit1_lidar": "./unit1/lidar/lid_0037.mat",
    "unit1_loc": "./unit1/gps/loc_0037.txt",
    "unit2_loc_cal": "./unit2/gps/loc_0037.txt",
}

# (§3.3.2 type name, §3.3.3 QoS profile) per lane the infrastructure
# publisher owns. Mirrors multi_operator_fusion/infrastructure_publisher.py.
LANES = {
    "beam_meta": ("oarc.rf_beam_meta", "MAP_META"),
    "beam_frame": ("rf_beam", "RF_BEAM_RT"),
    "radar_meta": ("oarc.radar_tensor_meta", "MAP_META"),
    "radar_tensor": ("radar_tensor", "RADAR_RT"),
    "vision_meta": ("oarc.video_frame_meta", "MAP_META"),
    "vision_frame": ("video_frame", "VIDEO_LIVE"),
    "geopose": ("geopose", "POSE_RT"),
    "detection2d": ("oarc.detection2d_set", "RADAR_RT"),
}


def _load(name: str):
    """Import by path: `deepsense_types` and `publisher` exist elsewhere too."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"_ds_{name}", _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ConverterShapes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            import scipy.io  # noqa: F401

            from spatialdds_demo import topic_types  # noqa: F401
        except Exception as exc:                       # pragma: no cover
            raise unittest.SkipTest(f"dependencies unavailable: {exc}")
        cls.d2s = _load("deepsense_to_spatialdds")

    def _check(self, lane_key, payload):
        from spatialdds_demo import topic_types
        from spatialdds_demo.json_mapping import from_json

        type_name, _profile = LANES[lane_key]
        cls = topic_types.try_resolve(type_name)
        self.assertIsNotNone(cls, f"{type_name!r} resolves to no class")
        from_json(cls, payload)

    def test_metadata_converters(self):
        d2s = self.d2s
        for key, build in (("beam_meta", d2s.make_beam_meta),
                           ("radar_meta", d2s.make_radar_meta),
                           ("vision_meta", d2s.make_vision_meta)):
            with self.subTest(converter=key):
                self._check(key, d2s.to_dict(build()))

    def test_vision_frame(self):
        self._check("vision_frame",
                    self.d2s.to_dict(self.d2s.row_to_vision_frame(ROW)))

    def test_beam_frame(self):
        import numpy as np

        d2s = self.d2s
        with _fake_file(d2s, "np.loadtxt",
                        lambda *_a, **_k: np.linspace(-70.0, -40.0, 64,
                                                      dtype=np.float32)):
            frame = d2s.row_to_beam_frame(ROW, Path("/nonexistent"))
        self._check("beam_frame", d2s.to_dict(frame))
        self.assertEqual(len(frame.power), 64)

    def test_radar_tensor(self):
        import numpy as np

        d2s = self.d2s
        cube = np.zeros((4, 256, 128), dtype=np.complex64)
        with _fake_file(d2s, "scipy.io.loadmat", lambda *_a, **_k: {"data": cube}):
            frame, returned = d2s.row_to_radar_tensor(ROW, Path("/nonexistent"))
        self._check("radar_tensor", d2s.to_dict(frame))
        self.assertEqual(returned.shape, (4, 256, 128))

    def test_geoposes(self):
        import numpy as np

        d2s = self.d2s
        with _fake_file(d2s, "np.loadtxt",
                        lambda *_a, **_k: np.array([33.42, -111.93])):
            bs, veh = d2s.row_to_geoposes(ROW, Path("/nonexistent"))
        for label, pose in (("bs", bs), ("veh", veh)):
            with self.subTest(unit=label):
                self._check("geopose", d2s.to_dict(pose))

    def test_detection2d_set_is_demo_owned(self):
        """
        1.7 has Detection3D for 3D boxes and nothing for 2D ones —
        VisionDetections carries keypoints and 2D tracks, not labelled boxes.
        So the most common camera perception output there is has no spec
        type, and this one is defined in idl/demo/oarc_demo.idl.
        """
        det_set = self.d2s.row_to_detection2d(ROW, Path("/nonexistent"))
        self._check("detection2d", self.d2s.to_dict(det_set))
        self.assertEqual(det_set.stream_id, "unit1_cam")

    def test_lanes_name_resolvable_types_and_real_profiles(self):
        from spatialdds_demo import qos_profiles, topic_types

        for key, (type_name, profile) in LANES.items():
            with self.subTest(lane=key):
                self.assertIsNotNone(topic_types.try_resolve(type_name))
                self.assertIsNotNone(qos_profiles.get(profile))


class _fake_file:
    """Patch one dotted attribute on the converter module for a block."""

    def __init__(self, module, dotted, value):
        self._module, self._dotted, self._value = module, dotted, value

    def __enter__(self):
        head, _, tail = self._dotted.partition(".")
        target = getattr(self._module, head)
        self._target = target if tail else self._module
        self._name = tail.split(".")[-1] if tail else head
        if tail and "." in tail:
            for part in tail.split(".")[:-1]:
                self._target = getattr(self._target, part)
        self._saved = getattr(self._target, self._name)
        setattr(self._target, self._name, self._value)
        return self

    def __exit__(self, *_exc):
        setattr(self._target, self._name, self._saved)
        return False


if __name__ == "__main__":
    unittest.main()


class PublisherLanes(unittest.TestCase):
    """The publisher's lane table names resolvable types and real profiles."""

    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            import scipy.io  # noqa: F401

            from spatialdds_demo import topic_types  # noqa: F401
        except Exception as exc:                       # pragma: no cover
            raise unittest.SkipTest(f"dependencies unavailable: {exc}")
        cls.pub = _load("publisher")

    def test_every_lane_resolves(self):
        from spatialdds_demo import qos_profiles, topic_types

        for key, (topic, type_name, profile) in self.pub.LANES.items():
            with self.subTest(lane=key):
                self.assertTrue(topic.startswith("spatialdds/"))
                self.assertIsNotNone(topic_types.try_resolve(type_name))
                self.assertIsNotNone(qos_profiles.get(profile))

    def test_lidar_sweep_goes_as_blob_chunks(self):
        """
        The old payload inlined the point array under a `points` key
        LidarFrame does not have, so it never reached a consumer as anything
        typed. The frame names a blob; the bytes travel as chunks.
        """
        import numpy as np

        from spatialdds_demo import blob
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.oarc_demo import BlobChunk
        from spatialdds_idl.spatial.sensing.lidar import LidarFrame

        points = np.arange(4 * 512, dtype=np.float32).reshape(512, 4)
        frame, chunks = self.pub._lidar_frame_and_blob(ROW, points)

        from_json(LidarFrame, self.pub.to_dict(frame))
        self.assertNotIn("points", self.pub.to_dict(frame))

        blob_ref = frame.hdr.blobs[0]
        self.assertEqual(blob_ref.role, "lidar")
        self.assertTrue(blob_ref.checksum.startswith("sha256:"))
        self.assertEqual(blob_ref.blob_id, chunks[0].blob_id)

        reassembler = blob.Reassembler()
        rebuilt = None
        for chunk in chunks:
            rebuilt = reassembler.feed(chunk) or rebuilt
        self.assertEqual(rebuilt, points.tobytes())
