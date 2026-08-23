"""
Typed transport: QoS profiles, and the instance lifecycle the envelope could not express.

The QoS-table tests are pure and run anywhere. The lifecycle tests need a real
DDS domain, so they skip loudly when CycloneDDS cannot create a participant
(no /etc/cyclonedds.xml on a normal host) rather than passing quietly.

Run them in the demo image:

    docker run --rm --network host -v "$PWD:/app" -w /app -e PYTHONPATH=/app \\
        -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \\
        python3 -m pytest -q tests/test_typed_transport.py
"""

import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spatialdds_demo import qos_profiles  # noqa: E402
from spatialdds_idl.builtin import Time  # noqa: E402
from spatialdds_idl.spatial.common import CoordConvention, FrameRef  # noqa: E402
from spatialdds_idl.spatial.core import Aabb3  # noqa: E402
from spatialdds_idl.spatial.disco import (  # noqa: E402
    Announce, Capabilities, CoverageElement, ServiceKind,
)

ANNOUNCE_TOPIC = "spatialdds/discovery/announce/v1"
STAMP = Time(sec=1700000000, nanosec=0)
FRAME = FrameRef(uuid="u", fqn="earth-fixed", has_coord_convention=True,
                 coord_convention=CoordConvention.ENU)
ZERO = Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0])
COVERAGE = CoverageElement(
    has_crs=True, crs="EPSG:4979", has_bbox=True, bbox=[-1.0, -1.0, 1.0, 1.0],
    has_aabb=False, aabb=ZERO, _global=False, has_frame_ref=False, frame_ref=FRAME,
    has_coverage_window=False, coverage_window_start=STAMP, coverage_window_end=STAMP,
)


def announce(service_id: str) -> Announce:
    return Announce(
        service_id=service_id, name=service_id, kind=ServiceKind.VPS, version="1.7",
        org="ExampleOrg", hints=[],
        caps=Capabilities(supported_profiles=[], preferred_profiles=[], features=[]),
        topics=[], coverage=[COVERAGE], coverage_frame_ref=FRAME,
        has_coverage_eval_time=False, coverage_eval_time=STAMP, transforms=[],
        manifest_uri="spatialdds://x/zone:z/manifest:m", auth_hint="",
        stamp=STAMP, ttl_sec=300,
    )


def _domain(domain_id: int):
    """A participant, or a skip if this environment has no usable DDS."""
    try:
        from cyclonedds.domain import DomainParticipant
        return DomainParticipant(domain_id)
    except Exception as exc:
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


class QosTable(unittest.TestCase):
    """§3.3.3 as data. No DDS needed."""

    def test_every_registered_profile_is_present(self):
        # The 14 from spec 3.3.3, plus the documented extension and the
        # discovery pair.
        for name in ("GEOM_TILE", "VIDEO_LIVE", "VIDEO_ARCHIVE", "RADAR_RT",
                     "RF_BEAM_RT", "RADIO_SCAN_RT", "SEG_MASK_RT", "DESC_BATCH",
                     "MAP_META", "ZONE_META", "EVENT_RT", "POSE_RT",
                     "VPS_REQ", "VPS_RESP"):
            with self.subTest(profile=name):
                self.assertIn(name, qos_profiles.PROFILES)

    def test_unknown_profile_is_refused(self):
        with self.assertRaises(qos_profiles.UnknownQosProfile):
            qos_profiles.get("NOT_A_PROFILE")

    def test_profiles_actually_differ(self):
        """The point of per-topic QoS: a live video lane is not a latched map lane."""
        video = qos_profiles.get("VIDEO_LIVE")
        map_meta = qos_profiles.get("MAP_META")
        self.assertFalse(video.reliable)
        self.assertFalse(video.latched)
        self.assertTrue(map_meta.reliable)
        self.assertTrue(map_meta.latched)

    def test_partial_reliability_is_documented_not_silently_dropped(self):
        """Spec says RADAR_RT is 'Partial'; DDS has no such kind."""
        radar = qos_profiles.get("RADAR_RT")
        self.assertFalse(radar.reliable)
        self.assertIn("Partial", radar.note)

    def test_qos_objects_build(self):
        for name in qos_profiles.PROFILES:
            with self.subTest(profile=name):
                self.assertIsNotNone(qos_profiles.qos_for(name))
        self.assertIsNotNone(qos_profiles.qos_for("VPS_REQ", lifespan_sec=300))


class InstanceLifecycle(unittest.TestCase):
    """
    What keyed typed topics buy, and the envelope structurally could not.

    Announce is @key service_id, so TRANSIENT_LOCAL + KEEP_LAST(1) is per
    service: a late joiner gets the current announce of every live service,
    and dispose means "this service is gone" rather than "this topic is gone".
    """

    DOMAIN = 25

    def _reader_writer(self):
        from spatialdds_demo import typed_transport as tt
        pub = _domain(self.DOMAIN)
        sub = _domain(self.DOMAIN)
        writer = tt.make_writer(pub, ANNOUNCE_TOPIC, Announce, "DISCOVERY_ANNOUNCE")
        reader = tt.make_reader(sub, ANNOUNCE_TOPIC, Announce, "DISCOVERY_ANNOUNCE")
        return tt, writer, reader

    def test_late_joiner_gets_every_service_then_dispose_evicts_one(self):
        from spatialdds_demo import typed_transport as tt
        pub = _domain(self.DOMAIN)
        writer = tt.make_writer(pub, ANNOUNCE_TOPIC, Announce, "DISCOVERY_ANNOUNCE")

        services = ["svc:a", "svc:b", "svc:c"]
        for service_id in services:
            writer.write(announce(service_id))
        time.sleep(1.0)

        # Reader created *after* the writes: this is the backfill the unkeyed
        # envelope could not do, where KEEP_LAST(1) meant one sample overall.
        sub = _domain(self.DOMAIN)
        reader = tt.make_reader(sub, ANNOUNCE_TOPIC, Announce, "DISCOVERY_ANNOUNCE")

        handles, alive = {}, set()
        for _ in range(10):
            time.sleep(0.5)
            for sample in tt.take_with_state(reader):
                if sample.data is not None:
                    handles[sample.instance_handle] = sample.data.service_id
                    alive.add(sample.data.service_id)
            if len(alive) == len(services):
                break
        self.assertEqual(alive, set(services), "late joiner did not get every instance")

        # Dispose one instance; the reader should learn that one service left.
        writer.dispose(announce("svc:b"))
        gone = set()
        for _ in range(10):
            time.sleep(0.5)
            for sample in tt.take_with_state(reader):
                if sample.disposed and sample.instance_handle in handles:
                    gone.add(handles[sample.instance_handle])
            if gone:
                break
        self.assertEqual(gone, {"svc:b"}, "dispose did not surface as a per-instance signal")


if __name__ == "__main__":
    unittest.main()
