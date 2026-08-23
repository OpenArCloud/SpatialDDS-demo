"""Unit tests for Phase 3a (EntityBinding) + Phase 4a (Announce).

The fusion service's ``_publish_entity_bindings`` and the synthetic
publisher's announce builders are pure functions over a transport
recorder, so we test them with a captured-publish stub instead of DDS.
"""

from __future__ import annotations

import json
import sys
import types as _types
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Tier-1 unit tests don't have cyclonedds — stub envelope_io so importing
# synthetic_publisher works even on a host without DDS.
if "envelope_io" not in sys.modules:
    stub = _types.ModuleType("envelope_io")
    class _StubPublisher:
        def __init__(self, *_a, **_kw): ...
        def publish(self, *_a, **_kw): ...
        def close(self): ...
    stub.EnvelopePublisher = _StubPublisher  # type: ignore[attr-defined]
    sys.modules["envelope_io"] = stub

from fusion import FusedTrack, Position, Velocity  # noqa: E402
from fusion_service import (  # noqa: E402
    DET3D_TOPIC_FMT,
    ENTITY_BINDING_MSG_TYPE,
    ENTITY_BINDING_TOPIC,
    FusionService,
    TRACK_TOPIC,
)
from synthetic_publisher import (  # noqa: E402
    COVERAGE_RADIUS_M,
    INFRA_BS_POSITION,
    INFRA_COVERAGE_RADIUS_M,
    _build_infra_announce,
    _build_operator_announce,
    _operator_coverage,
)
from spatialdds_demo.json_mapping import from_json  # noqa: E402
from spatialdds_demo.topics import validate_topic_meta  # noqa: E402
from spatialdds_idl.spatial.disco import Announce, ServiceKind  # noqa: E402


class _RecordingTransport:
    """Captures publish() calls so the test can assert on payloads."""

    def __init__(self) -> None:
        self.calls = []

    def publish(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _make_fused_track(track_id="fused-7", op_dets=None, cls="vehicle.car"):
    return FusedTrack(
        track_id=track_id,
        position=Position(1.0, 2.0, 0.0),
        velocity=Velocity(0.5, 0.5, 0.0),
        position_uncertainty=0.4,
        object_class=cls,
        confidence=0.92,
        source_operators=sorted(op_dets or {}),
        source_modalities=["det3d"],
        source_count=len(op_dets or {}),
        timestamp=10.0,
        track_age=2.5,
        last_det_per_operator=dict(op_dets or {}),
    )


class TestEntityBindingPublish(unittest.TestCase):

    def setUp(self):
        self.tx = _RecordingTransport()

        # Build a FusionService without running its tick loop. The
        # constructor only requires (transport, fuser, tick_hz, sigma,
        # quiet) — fuser can be a stub since we won't call it.
        class _StubFuser:
            def tick(self, *_a, **_kw): return []
            def on_detection(self, *_a, **_kw): pass
        self.svc = FusionService(transport=self.tx, fuser=_StubFuser(),
                                  tick_hz=2.0, default_sigma=0.5, quiet=True)

    def test_emits_one_binding_per_track(self):
        tracks = [
            _make_fused_track("fused-1", {"operator_a": "det_42"}),
            _make_fused_track("fused-2", {"operator_a": "det_77",
                                           "operator_b": "det_99"}),
        ]
        self.svc._publish_entity_bindings(tracks, t=10.0)
        self.assertEqual(len(self.tx.calls), 2)
        for call in self.tx.calls:
            self.assertEqual(call["logical_topic"], ENTITY_BINDING_TOPIC)
            self.assertEqual(call["msg_type"], ENTITY_BINDING_MSG_TYPE)

    def test_components_include_track_and_each_operator_det(self):
        track = _make_fused_track("fused-7", {
            "operator_a": "det_42",
            "operator_c": "det_18",
            "infrastructure": "infra_0_2_55",
        })
        self.svc._publish_entity_bindings([track], t=10.0)
        binding = self.tx.calls[0]["payload"]
        topics = [c["topic"] for c in binding["components"]]
        keys = [c["key"] for c in binding["components"]]

        self.assertIn(TRACK_TOPIC, topics)
        self.assertIn(DET3D_TOPIC_FMT.format(operator="operator_a"), topics)
        self.assertIn(DET3D_TOPIC_FMT.format(operator="operator_c"), topics)
        self.assertIn(DET3D_TOPIC_FMT.format(operator="infrastructure"), topics)
        self.assertIn("fused-7", keys)
        self.assertIn("det_42", keys)
        self.assertIn("infra_0_2_55", keys)

    def test_skips_operators_without_det_id(self):
        """If a contributing detection lacked a det_id (legacy data, edge
        case in tests), no component_ref is emitted for that operator —
        but the fused-track ref is always present."""
        track = _make_fused_track("fused-3", {"operator_a": "",
                                                "operator_b": "det_5"})
        self.svc._publish_entity_bindings([track], t=10.0)
        binding = self.tx.calls[0]["payload"]
        ops_in_components = [c["topic"].split("/")[1]
                              for c in binding["components"]
                              if "sensing/detection3d" in c["topic"]]
        self.assertNotIn("operator_a", ops_in_components)
        self.assertIn("operator_b", ops_in_components)

    def test_pose_attached(self):
        track = _make_fused_track("fused-1", {"operator_a": "det_1"})
        self.svc._publish_entity_bindings([track], t=10.0)
        binding = self.tx.calls[0]["payload"]
        self.assertTrue(binding["has_pose"])
        self.assertEqual(binding["pose"]["t"]["x"], 1.0)
        self.assertEqual(binding["pose"]["t"]["y"], 2.0)
        self.assertEqual(binding["entity_class"], "vehicle.car")

    def test_payload_is_json_serialisable(self):
        track = _make_fused_track("fused-1", {"operator_a": "det_1"})
        self.svc._publish_entity_bindings([track], t=10.0)
        # Round-trips cleanly — the IDL's wire format is JSON-on-envelope.
        round_tripped = json.loads(json.dumps(self.tx.calls[0]["payload"]))
        self.assertEqual(round_tripped["entity_id"], "entity_fused-1")


class TestAnnounceBuilder(unittest.TestCase):
    """
    The announce is a real spatial::disco::Announce.

    It used to be a demo-private shape — `operator`, `service_kind`,
    `has_coverage`, topics as {topic, msg_type} — which the repo's own
    validate_topic_meta rejected and which AnnounceCache dropped for having no
    service_id, so the flagship demo was quietly missing from discovery
    (findings 5.1, 5.2). These tests now assert the spec shape.
    """

    def test_operator_announce_is_spec_shaped(self):
        a = _build_operator_announce(op_idx=0, t_wall=42.0)
        self.assertEqual(a["service_id"], "svc:operator_a")   # the key
        self.assertEqual(a["name"], "operator_a")
        self.assertEqual(a["kind"], "OTHER")
        self.assertEqual(a["stamp"]["sec"], 42)
        self.assertTrue(a["manifest_uri"])
        # ServiceKind cannot express "sensor fleet", so the role is a hint.
        self.assertIn({"key": "role", "value": "SENSING"}, a["hints"])

    def test_announce_passes_the_repos_own_validator(self):
        """findings 5.2: this used to fail with three TopicMeta errors."""
        for op_idx in (0, 1, 2):
            with self.subTest(op_idx=op_idx):
                a = _build_operator_announce(op_idx=op_idx, t_wall=0.0)
                ok, errors = validate_topic_meta(a["topics"])
                self.assertTrue(ok, errors)

    def test_announce_builds_a_real_typed_announce(self):
        a = _build_operator_announce(op_idx=0, t_wall=1.0)
        typed = from_json(Announce, a)
        self.assertEqual(typed.service_id, "svc:operator_a")
        self.assertEqual(typed.kind, ServiceKind.OTHER)

    def test_operator_announce_lists_owned_topics_with_registered_types(self):
        a = _build_operator_announce(op_idx=1, t_wall=0.0)
        self.assertEqual({t["name"] for t in a["topics"]}, {
            "spatialdds/operator_b/sensing/detection3d/v1",
            "spatialdds/operator_b/ego/pose/v1",
            "spatialdds/operator_b/plan/operator_b_ego/trajectory/v1",
        })
        by_name = {t["name"]: t for t in a["topics"]}
        det = by_name["spatialdds/operator_b/sensing/detection3d/v1"]
        self.assertEqual(det["type"], "radar_detection")
        self.assertEqual(det["qos_profile"], "RADAR_RT")
        plan = by_name["spatialdds/operator_b/plan/operator_b_ego/trajectory/v1"]
        self.assertEqual(plan["type"], "planned_trajectory")

    def test_coverage_is_a_coverage_element_centred_on_ego_start(self):
        """
        CoverageElement has no circle, so a circular area is its bounding aabb
        in local metres. Centre and half-width give the circle back — which is
        what the canvas dashboard does.
        """
        cov = _operator_coverage(op_idx=0)  # operator_a starts at (0, -30)
        self.assertTrue(cov["has_aabb"])
        self.assertFalse(cov["has_bbox"])
        lo, hi = cov["aabb"]["min_xyz"], cov["aabb"]["max_xyz"]
        self.assertEqual((lo[0] + hi[0]) / 2, 0.0)
        self.assertEqual((lo[1] + hi[1]) / 2, -30.0)
        self.assertEqual((hi[0] - lo[0]) / 2, COVERAGE_RADIUS_M)

    def test_infra_announce_uses_bs_position(self):
        a = _build_infra_announce(t_wall=1.0)
        self.assertEqual(a["service_id"], "svc:infrastructure")
        self.assertIn({"key": "role", "value": "INFRASTRUCTURE"}, a["hints"])
        cov = a["coverage"][0]
        lo, hi = cov["aabb"]["min_xyz"], cov["aabb"]["max_xyz"]
        self.assertEqual((lo[0] + hi[0]) / 2, INFRA_BS_POSITION["x"])
        self.assertEqual((lo[1] + hi[1]) / 2, INFRA_BS_POSITION["y"])
        self.assertEqual((hi[0] - lo[0]) / 2, INFRA_COVERAGE_RADIUS_M)
        self.assertEqual(len(a["topics"]), 1)
        self.assertEqual(a["topics"][0]["name"],
                         "spatialdds/infrastructure/sensing/detection3d/v1")

    def test_announce_is_json_serialisable(self):
        a = _build_operator_announce(op_idx=2, t_wall=99.5)
        self.assertEqual(json.loads(json.dumps(a))["service_id"], "svc:operator_c")


if __name__ == "__main__":
    unittest.main()
