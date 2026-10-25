"""
Every lane a publisher here names is a §3.3.1 name that says what it carries.

§3.3.1: `spatialdds/<domain>/<stream>/<type>/<version>`. This deployment reads
it as one rule (the topic-rename ruling, 2026-10-24): the domain segment is the
operator or dataset, the stream is the sensor or lane instance, and the type
segment is the lane's registered type. Until the SDK adoption the sensor lanes
had six segments (`…/vision/<cam>/frame/v1`), which §3.3.1 does not have and
the SDK refuses, and several five-segment names did not say what they carried
(`…/fusion/track/v1` for `fused_track`).

Two types are the exception, because §2 gives each a lane template of its
own and §3.3.1 makes such a template normative where it is defined (and allows
it an extra segment): a PlannedTrajectory "SHOULD be advertised on the topic
spatialdds/<scene>/plan/<agent_id>/trajectory/v1", an EntityBinding on
spatialdds/<scene>/entity/binding/v1. This deployment follows both. The
templates are read from the SDK's registry, which reads them from §2's own
sentences as of spatialdds 0.7.3; until then its check refused the plan
template (spatialdds-py S15), and this file kept its own copy.

The subjects are the publishers' own sources of names, read as they publish
them: the announce builders, the lane tables, the topic functions and the ROS
encoders' output. A name is checked by the SDK's own §3.3.1 check, then for
the type segment.
"""

import importlib.util
import inspect
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO, REPO / "multi_operator_fusion", REPO / "bridges" / "ros2_bridge",
           REPO / "deepsense", REPO / "nuscenes"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import spatialdds as sd  # noqa: E402

# The nuScenes devkit is an import the publishers make for annotations and
# for run(); stubbed, as nuscenes/test_nuscenes_shapes.py does, so their lane
# names can be read without the dataset or the package installed.
for _name in ("nuscenes.nuscenes", "nuscenes.utils", "nuscenes.utils.data_classes"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["nuscenes.nuscenes"].NuScenes = type("NuScenes", (), {})
sys.modules["nuscenes.utils.data_classes"].RadarPointCloud = type(
    "RadarPointCloud", (), {})


def load(relpath: str):
    """A module by its file, under a name of its own. Three directories here
    each have a `publisher.py`, and importing by bare name gets whichever
    `sys.path` happens to put first."""
    path = REPO / relpath
    name = "topic_names_subject_" + relpath.replace("/", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: §2's lane templates, as the SDK's registry read them from the spec.
SPEC_TEMPLATES = {t: r.profile_template for t, r in
                  sd.dds.registry.registrations().items() if r.profile_template}


def problem(topic: str, type_name: str):
    """Why `topic` is not this deployment's name for a `type_name` lane, or None."""
    template = SPEC_TEMPLATES.get(type_name)
    if template is not None:
        return None if sd.dds.registry.fits_template(topic, template) else (
            f"§2 gives {type_name} the lane {template}")
    try:
        sd.dds.registry.check_topic(topic, type_name)
    except Exception as error:
        return f"not §3.3.1: {error}"
    parts = topic.split("/")
    if len(parts) != 5:
        return f"{len(parts)} segments, not spatialdds/<scene>/<stream>/<type>/<v>"
    if parts[3] != type_name:
        return f"type segment {parts[3]!r} names a type it does not carry ({type_name!r})"
    return None


class NameRule(unittest.TestCase):
    """The rule itself, so the checks below cannot pass by being lenient."""

    def test_a_six_segment_sensor_lane_is_refused(self):
        self.assertIsNotNone(
            problem("spatialdds/op/vision/cam0/frame/v1", "video_frame"))

    def test_a_name_that_misnames_its_type_is_refused(self):
        self.assertIsNotNone(
            problem("spatialdds/platform/fusion/track/v1", "fused_track"))

    def test_the_rule_admits_its_own_form(self):
        self.assertIsNone(
            problem("spatialdds/op/cam0/video_frame/v1", "video_frame"))

    def test_the_registry_holds_both_of_section_2s_templates(self):
        self.assertEqual(SPEC_TEMPLATES, {
            "planned_trajectory": "spatialdds/<scene>/plan/<agent_id>/trajectory/v1",
            "entity_binding": "spatialdds/<scene>/entity/binding/v1",
        })

    def test_a_templated_type_takes_its_template(self):
        self.assertIsNone(problem(
            "spatialdds/op_a/plan/op_a_ego/trajectory/v1", "planned_trajectory"))
        self.assertIsNone(problem(
            "spatialdds/platform/entity/binding/v1", "entity_binding"))
        # The type-segment form is §3.3.1's, but §2's template wins here.
        self.assertIsNotNone(problem(
            "spatialdds/op_a/op_a_ego/planned_trajectory/v1", "planned_trajectory"))
        self.assertIsNotNone(problem(
            "spatialdds/platform/entity/entity_binding/v1", "entity_binding"))


class EveryPublisher(unittest.TestCase):
    def check(self, lanes):
        lanes = list(lanes)
        self.assertTrue(lanes, "no lanes found: the subject is empty")
        for topic, type_name in lanes:
            with self.subTest(topic=topic):
                self.assertIsNone(problem(topic, type_name))

    def test_the_synthetic_operators_and_infrastructure(self):
        sp = load("multi_operator_fusion/synthetic_publisher.py")

        rows = list(sp._build_infra_announce(0.0)["topics"])
        for op_idx in range(3):     # the operator id is built from the index
            rows += sp._build_operator_announce(op_idx, 0.0)["topics"]
        self.check((row["name"], row["type"]) for row in rows)

    def test_the_fusion_platform(self):
        fs = load("multi_operator_fusion/fusion_service.py")

        self.check((topic, type_name) for topic, type_name, _ in fs.PLATFORM_LANES)

    def test_the_nuscenes_operator_publisher(self):
        mp = load("multi_operator_fusion/publisher.py")

        lanes = [(mp._topic("operator_a", stream, type_name), type_name)
                 for stream, type_name, _ in
                 (lane for lane in mp.LANES.values() if lane is not None)]
        for cam in mp.CAM_CHANNELS:
            for type_name, _ in (mp.VISION_META_TYPE, mp.VISION_FRAME_TYPE):
                lanes.append((mp._topic("operator_a", cam, type_name), type_name))
        for radar in mp.RADAR_CHANNELS:
            lanes.append((mp._topic("operator_a", radar, mp.RADAR_TYPE[0]),
                          mp.RADAR_TYPE[0]))
        self.check(lanes)

    def test_the_infrastructure_publisher(self):
        ip = load("multi_operator_fusion/infrastructure_publisher.py")

        self.check((ip.lane_topic(stream, type_name), type_name)
                   for stream, type_name, _ in ip.RAW_LANES.values())

    def test_the_deepsense_publisher(self):
        dp = load("deepsense/publisher.py")

        self.check((topic, type_name) for key, (topic, type_name, _)
                   in dp.LANES.items() if key != "blob")

    def test_the_nuscenes_publisher(self):
        """Every `_topic_*` function the module defines, by its own lane."""
        npub = load("nuscenes/publisher.py")

        lanes = []
        for name, fn in inspect.getmembers(npub, inspect.isfunction):
            if not name.startswith("_topic_"):
                continue
            args = ["CAM_FRONT"] * len(inspect.signature(fn).parameters)
            topic = fn(*args)
            lanes.append((topic, topic.split("/")[3]))
        types = {t for t, _ in (npub.TYPE_EGO_POSE, npub.TYPE_GEO_POSE,
                                npub.TYPE_VISION_META, npub.TYPE_VISION,
                                npub.TYPE_LIDAR_META, npub.TYPE_LIDAR,
                                npub.TYPE_RAD_DET, npub.TYPE_DET3D)}
        self.assertEqual({t for _, t in lanes}, types,
                         "a lane type with no topic function, or the reverse")
        self.check(lanes)

    def test_the_ros2_bridge_encoders(self):
        import ros2_to_spatialdds as r2s
        from frame_mapping import FrameMapper
        from ros2_mocks import (
            make_test_compressed_image, make_test_detection3d_array,
            make_test_imu, make_test_nav_sat_fix, make_test_pose_stamped,
        )

        mapper = FrameMapper("op_a")
        cases = {
            "encode_pose_stamped": r2s.encode_pose_stamped(
                make_test_pose_stamped(), "op_a", mapper),
            "encode_nav_sat_fix": r2s.encode_nav_sat_fix(
                make_test_nav_sat_fix(), "op_a"),
            "encode_imu": r2s.encode_imu(make_test_imu(), "op_a", "imu0", mapper),
            "encode_compressed_image": r2s.encode_compressed_image(
                make_test_compressed_image(), "op_a", "cam0", mapper),
            "encode_detection3d_array": r2s.encode_detection3d_array(
                make_test_detection3d_array(n=1), "op_a", mapper),
        }
        defined = {name for name, _ in
                   inspect.getmembers(r2s, inspect.isfunction)
                   if name.startswith("encode_") and
                   inspect.getmodule(_) is r2s}
        self.assertEqual(set(cases), defined,
                         "an encoder this test does not exercise")
        self.check((topic, type_name) for topic, type_name, _ in cases.values())


if __name__ == "__main__":
    unittest.main()
