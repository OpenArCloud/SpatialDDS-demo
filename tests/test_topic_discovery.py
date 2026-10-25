"""
Discovery-driven typed subscription.

The envelope's real convenience was one reader seeing everything. Typed topics
give that up, and this is what replaces it: a consumer reads announces and
subscribes to whatever they advertise, using TopicMeta.type to pick the class
and TopicMeta.qos_profile to pick the QoS.

The bus part needs a DDS domain and skips loudly without one; the registry and
TopicMeta parsing are pure.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import spatialdds as sd  # noqa: E402

from spatialdds_demo import lanes  # noqa: E402


class Registry(unittest.TestCase):
    """The names an announce carries resolve through lanes.class_for: the
    SDK's registry first, then this deployment's own. (Until the SDK
    adoption: spatialdds_demo.topic_types, a copy of the registry.)"""

    def test_registered_names_resolve_to_classes(self):
        for name in ("geopose", "planned_trajectory", "entity_binding",
                     "spatial_event", "radar_detection", "video_frame"):
            with self.subTest(type=name):
                self.assertTrue(callable(lanes.class_for(name)))

    def test_radar_detection_is_the_radar_set_not_the_semantic_one(self):
        """
        3.3.2's `radar_detection` is "structured radar detections" —
        rad::RadDetectionSet, which carries range/azimuth/doppler/rcs. It is
        not semantics::Detection3DSet, which carries 3D boxes.

        The registry names no type for the semantic set, though the spec's
        own "what objects exist and where?" table points at exactly that
        type. So the demo has to reach for an `oarc.*` name for the most
        central perception type there is.
        """
        self.assertEqual(lanes.class_for("radar_detection").__name__,
                         "RadDetectionSet")
        self.assertEqual(lanes.class_for("detection3d").__name__,
                         "Detection3DSet")

    def test_an_unknown_type_is_refused_with_its_reason(self):
        """§3.3.2 treats unregistered values as extension points, so a reader
        skips such a lane (StreamSubscriber.skipped, below) rather than fail."""
        with self.assertRaises(sd.dds.RegistryError):
            lanes.class_for("someone.elses.type")

    def test_every_extension_is_a_lane_this_deployment_can_open(self):
        """
        Every `oarc.*` type, announced on its declared profile, passes the
        check a document's rows are held to (lanes.topic_meta_problems). The
        old validator and registry each kept a list of extensions, and they
        disagreed once; there is one list now, `lanes.EXTENSIONS`.
        """
        rows = [lanes.topic_meta(f"spatialdds/x/s{i}/{type_name}/v1", type_name,
                                 lanes.DECLARED_PROFILES[type_name])
                for i, type_name in enumerate(sorted(lanes.EXTENSIONS))]
        self.assertEqual(lanes.topic_meta_problems(rows), [])


class TheExtensionsAreDocumentedAsRegistered(unittest.TestCase):
    """
    §3.3.2 asks that deployment-specific type names be documented, and
    SPEC_COMPLIANCE's extensions table is that documentation. It had drifted:
    it listed the world-model and catalogue extensions long after the
    registry stopped carrying them, and its count said five against three.
    """

    def test_the_table_lists_exactly_the_registry(self):
        import re
        text = (REPO / "ar_demo" / "SPEC_COMPLIANCE.md").read_text()
        section = text[text.index("## Extensions: what 1.8 still has no type for"):]
        section = section[:section.index("<!-- extension-count:")]
        listed = set(re.findall(r"^\| `(oarc\.[a-z_]+)` \|", section, re.M))
        self.assertEqual(listed, set(lanes.EXTENSIONS))

    def test_the_stated_count_is_the_registry_size(self):
        import re
        text = (REPO / "ar_demo" / "SPEC_COMPLIANCE.md").read_text()
        stated = re.search(r"<!-- extension-count: (\d+) -->", text)
        self.assertIsNotNone(stated, "the extension-count marker is gone")
        self.assertEqual(int(stated.group(1)), len(lanes.EXTENSIONS))


class TopicMetaParsing(unittest.TestCase):
    """The shipped announce-driven reader must tolerate what the bus carries."""

    def _subscriber(self):
        from spatialdds_demo.stream import StreamSubscriber
        try:
            self._p = sd.participant("topic-discovery-test", domain=37)
        except Exception as exc:
            raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")
        self.addCleanup(self._p.close)
        return StreamSubscriber(self._p, lambda *a: None)

    def test_unknown_and_incomplete_rows_are_skipped(self):
        sub = self._subscriber()
        rows = [
            {"name": "spatialdds/test/a/someone.elses.type/v1",
             "type": "someone.elses.type", "version": "v1",
             "qos_profile": "POSE_RT"},                            # unknown type
            {"name": "", "type": "geopose", "version": "v1",
             "qos_profile": "POSE_RT"},                            # no name
            {"name": "spatialdds/test/b/geopose/v1", "type": "geopose",
             "version": "v1"},                                     # no qos
        ]
        self.assertEqual([r["name"] for r in rows if sub._open(r)], [])
        self.assertEqual(sub.topics, [])
        self.assertIn("spatialdds/test/a/someone.elses.type/v1", sub.skipped)

    def test_a_good_row_subscribes_once(self):
        sub = self._subscriber()
        row = {"name": "spatialdds/test/pose/geopose/v1", "type": "geopose",
               "version": "v1", "qos_profile": "POSE_RT"}
        self.assertTrue(sub._open(row))
        # Idempotent: a re-announce must not create a second reader.
        self.assertFalse(sub._open(row))
        self.assertEqual(sub.topics, ["spatialdds/test/pose/geopose/v1"])


if __name__ == "__main__":
    unittest.main()
