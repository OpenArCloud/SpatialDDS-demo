"""Tier-2 integration tests for the MQTT <-> DDS bridge.

Spins up the bridge in-process against a running Mosquitto broker and the
local CycloneDDS bus. Each test publishes on one side and asserts the
other side receives — and on the DDS side that what arrived is a real
typed sample, not merely some JSON.

Skipped if Mosquitto isn't reachable on ``MQTT_BROKER:MQTT_PORT`` (defaults
``localhost:1883``). The repo's
``bridges/mqtt_bridge/docker-compose.test.yaml`` brings up Mosquitto and a
shell that runs this test.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Dict, List

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
for p in (str(_HERE), str(_HERE.parent), str(_REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


MQTT_HOST = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DDS_DOMAIN = int(os.getenv("MQTT_BRIDGE_TEST_DOMAIN", "81"))


def _mosquitto_alive() -> bool:
    try:
        with socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=1.0):
            return True
    except OSError:
        return False


def _have_cyclonedds() -> bool:
    try:
        import cyclonedds  # noqa: F401
        return True
    except ImportError:
        return False


def _have_paho() -> bool:
    try:
        import paho.mqtt.client  # noqa: F401
        return True
    except ImportError:
        return False


def _new_mqtt_client(client_id: str):
    import paho.mqtt.client as mqtt
    kwargs = {"client_id": client_id, "protocol": mqtt.MQTTv5}
    if hasattr(mqtt, "CallbackAPIVersion"):
        kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
    return mqtt.Client(**kwargs)


def _prewarm_idl():
    """CycloneDDS Python lazily fills the IDL type-object cache the first
    time a Topic is built. Pre-warm in the main thread before background
    threads (the bridge / test publisher) race on it."""
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo import typed_transport as tt
    from spatialdds_idl.oarc_demo import OperatorDetectionSet

    tt.make_writer(DomainParticipant(DDS_DOMAIN), "spatialdds/prewarm/v1",
                   OperatorDetectionSet, "RADAR_RT")
    time.sleep(0.2)


def _det_set(operator: str, det_id: str = "d1") -> dict:
    """A real OperatorDetectionSet payload, built as the publishers build it."""
    sys.path.insert(0, str(_REPO_ROOT / "multi_operator_fusion"))
    from spatialdds_types import (
        make_detection, make_detection_set, make_detection_with_velocity,
    )
    det = make_detection(
        det_id=det_id, class_id="vehicle.car", score=0.9,
        center=(1.0, 2.0, 0.0), size=(4.5, 1.8, 1.6), q=(0.0, 0.0, 0.0, 1.0),
        frame_ref_fqn="scene/intersection", timestamp_s=1.0, source_id=operator,
    )
    return make_detection_set(
        set_id="s1", source_operator=operator,
        frame_ref_fqn="scene/intersection",
        dets=[make_detection_with_velocity(det, velocity=(0.0, 0.0, 0.0),
                                           source_modality="det3d")],
        frame_seq=1, timestamp_s=1.0)


def _track_set() -> dict:
    sys.path.insert(0, str(_REPO_ROOT / "multi_operator_fusion"))
    from fusion import FusedTrack, Position, Velocity
    from spatialdds_types import make_fused_track_set

    return make_fused_track_set([FusedTrack(
        track_id="t1", position=Position(0.0, 0.0, 0.0),
        velocity=Velocity(0.0, 0.0, 0.0), position_uncertainty=0.3,
        object_class="vehicle.car", confidence=0.9,
        source_operators=["operator_a"], source_modalities=["det3d"],
        source_count=1, timestamp=1.0, track_age=1.0,
    )], timestamp_s=1.0)


class _DdsObserver:
    """
    A typed reader on one named topic, polled on a thread.

    Explicit rather than discovery-driven on purpose: these tests publish
    onto topics no service announces, so there is nothing to discover. A
    real consumer reads announces; a test harness names what it wants.
    """

    def __init__(self, topic: str, type_name: str, qos_profile: str):
        from cyclonedds.domain import DomainParticipant

        from spatialdds_demo import topic_types, typed_transport as tt
        from spatialdds_demo.json_mapping import to_json

        self._to_json = to_json
        self.datatype = topic_types.resolve(type_name)
        self.samples: List[dict] = []
        self._reader = tt.make_reader(DomainParticipant(DDS_DOMAIN), topic,
                                      self.datatype, qos_profile)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        from spatialdds_demo import typed_transport as tt

        while not self._stop.is_set():
            for sample in tt.take_samples(self._reader):
                self.samples.append(self._to_json(sample))
            self._stop.wait(0.05)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)


def _announce_lane(topic: str, type_name: str, qos_profile: str,
                   service: str):
    """
    Announce one lane so a discovery-driven consumer will open a reader.

    Returns the publisher; keep it alive for as long as the lane matters,
    since closing it disposes the instance and signals a departure.
    """
    from cyclonedds.domain import DomainParticipant

    sys.path.insert(0, str(_REPO_ROOT / "multi_operator_fusion"))
    from spatialdds_demo.stream import StreamPublisher
    from spatialdds_types import circle_coverage, make_announce, topic_meta

    publisher = StreamPublisher(DomainParticipant(DDS_DOMAIN))
    publisher.announce(make_announce(
        operator=service, service_kind="SENSING",
        topics=[topic_meta(topic, type_name, qos_profile)],
        coverage=circle_coverage(0.0, 0.0, 100.0), timestamp_s=time.time()))
    _LANES.append(publisher)
    return publisher


_LANES: List = []


def _publish_mqtt(client_id: str, topic: str, payload: dict,
                  *, bridge_id: str = "") -> None:
    """Publish JSON on MQTT, optionally tagged with a bridge id."""
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.properties import Properties

    props = None
    if bridge_id:
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [("spatialdds_bridge_id", bridge_id)]
    pub = _new_mqtt_client(client_id)
    pub.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
    pub.loop_start()
    pub.publish(topic, json.dumps(payload), qos=1, properties=props)
    time.sleep(2.0)
    pub.loop_stop()
    pub.disconnect()


def _make_config(direction: str, *, bridge_id: str = "test-bridge",
                   client_id: str = "test-mqtt-bridge") -> "BridgeConfig":
    from config import BridgeConfig
    return BridgeConfig(
        mqtt_broker=MQTT_HOST,
        mqtt_port=MQTT_PORT,
        mqtt_client_id=client_id,
        dds_domain_id=DDS_DOMAIN,
        direction=direction,
        bridge_id=bridge_id,
        inbound_topics=[
            "spatialdds/operator_+/sensing/#",
            "spatialdds/operator_+/ego/#",
        ],
        outbound_topics=[
            "spatialdds/platform/fusion/#",
            "spatialdds/infrastructure/#",
        ],
        log_interval_s=60.0,
    )


@unittest.skipUnless(_have_paho() and _have_cyclonedds(),
                      "paho-mqtt + cyclonedds required for Tier-2")
@unittest.skipUnless(_mosquitto_alive(),
                      f"Mosquitto not reachable at {MQTT_HOST}:{MQTT_PORT}")
class TestMqttBridgeIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _prewarm_idl()

    # ──────────────────────────────────────────────────────────────────────
    # Inbound: MQTT publish → bridge → a typed sample on DDS
    # ──────────────────────────────────────────────────────────────────────

    def test_mqtt_to_dds_inbound(self):
        from bridge import MqttDdsBridge

        topic = "spatialdds/operator_test/sensing/detection3d/v1"
        cfg = _make_config("inbound_only", bridge_id="bridge-in",
                           client_id="bridge-in-mqtt")
        bridge = MqttDdsBridge(cfg)
        bridge.start(block=False)
        time.sleep(2.0)                       # MQTT connect + DDS discovery

        observer = _DdsObserver(topic, "oarc.detection3d_velocity", "RADAR_RT")
        time.sleep(2.0)
        try:
            _publish_mqtt("test-pub-inbound", topic, _det_set("operator_test"))
        finally:
            observer.stop()
            bridge.stop()

        self.assertGreaterEqual(len(observer.samples), 1,
                                "DDS side saw no sample")
        sample = observer.samples[0]
        # It arrived as a real OperatorDetectionSet — the reader would not
        # have deserialised anything else onto this typed topic.
        self.assertEqual(sample["source_operator"], "operator_test")
        self.assertEqual(sample["dets"][0]["detection"]["det_id"], "d1")

    def test_malformed_payload_is_refused_at_the_bridge(self):
        """
        The envelope relayed anything that was JSON and let some later
        consumer discover it was nonsense. A typed bridge cannot: the
        payload has to build into the announced type, and this one does not.
        """
        from bridge import MqttDdsBridge

        topic = "spatialdds/operator_bad/sensing/detection3d/v1"
        cfg = _make_config("inbound_only", bridge_id="bridge-bad",
                           client_id="bridge-bad-mqtt")
        bridge = MqttDdsBridge(cfg)
        bridge.start(block=False)
        time.sleep(2.0)

        observer = _DdsObserver(topic, "oarc.detection3d_velocity", "RADAR_RT")
        time.sleep(1.5)
        try:
            _publish_mqtt("test-pub-bad", topic,
                          {"detections": [{"det_id": "d1"}]})   # old shape
        finally:
            observer.stop()
            bridge.stop()

        self.assertEqual(observer.samples, [],
                         "a malformed payload reached the bus")
        self.assertGreaterEqual(bridge.stats.inbound_errors, 1)

    # ──────────────────────────────────────────────────────────────────────
    # Outbound: typed DDS sample → bridge → MQTT subscriber
    # ──────────────────────────────────────────────────────────────────────

    def test_dds_to_mqtt_outbound(self):
        from cyclonedds.domain import DomainParticipant

        from bridge import MqttDdsBridge
        from spatialdds_demo import typed_transport as tt
        from spatialdds_idl.oarc_demo import FusedTrackSet

        topic = "spatialdds/platform/fusion/track/v1"
        cfg = _make_config("outbound_only", bridge_id="bridge-out",
                           client_id="bridge-out-mqtt")
        bridge = MqttDdsBridge(cfg)
        # The bridge subscribes through discovery, so the lane has to be
        # announced before anything is written to it.
        _announce_lane(topic, "oarc.fused_track", "POSE_RT", "platform")
        bridge.start(block=False)
        time.sleep(3.0)

        received: List = []
        mqtt_sub = _new_mqtt_client("test-sub-outbound")
        mqtt_sub.on_message = lambda _c, _u, msg: received.append(msg)
        mqtt_sub.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        mqtt_sub.subscribe("spatialdds/platform/fusion/#", qos=1)
        mqtt_sub.loop_start()
        time.sleep(1.0)

        try:
            writer = tt.TypedDictWriter(DomainParticipant(DDS_DOMAIN), topic,
                                        FusedTrackSet, "POSE_RT")
            time.sleep(2.0)
            writer.write(_track_set())
            time.sleep(3.0)
        finally:
            mqtt_sub.loop_stop()
            mqtt_sub.disconnect()
            bridge.stop()

        self.assertGreaterEqual(len(received), 1, "MQTT saw nothing")
        msg = received[0]
        self.assertEqual(msg.topic, topic)
        body = json.loads(msg.payload.decode("utf-8"))
        self.assertEqual(body["tracks"][0]["track_id"], "t1")
        # The bridge id and the SpatialDDS type ride in MQTT user
        # properties, not in the payload — a typed struct has no field for
        # either, and neither was ever the payload's business.
        self.assertNotIn("_bridge_id", body)
        props = dict(getattr(msg.properties, "UserProperty", []) or [])
        self.assertEqual(props.get("spatialdds_bridge_id"), "bridge-out")
        self.assertEqual(props.get("spatialdds_msg_type"), "oarc.fused_track")

    # ──────────────────────────────────────────────────────────────────────
    # Loop prevention
    # ──────────────────────────────────────────────────────────────────────

    def test_loop_prevention_inbound(self):
        """A message tagged with the bridge's own id is not re-relayed."""
        from bridge import MqttDdsBridge

        topic = "spatialdds/operator_loop/sensing/detection3d/v1"
        cfg = _make_config("inbound_only", bridge_id="bridge-loop",
                           client_id="bridge-loop-mqtt")
        bridge = MqttDdsBridge(cfg)
        bridge.start(block=False)
        time.sleep(2.0)

        observer = _DdsObserver(topic, "oarc.detection3d_velocity", "RADAR_RT")
        time.sleep(2.0)
        try:
            _publish_mqtt("test-pub-loop", topic, _det_set("operator_loop"),
                          bridge_id="bridge-loop")
        finally:
            observer.stop()
            bridge.stop()

        self.assertEqual(observer.samples, [],
                         "bridge republished its own message")
        self.assertGreaterEqual(bridge.stats.inbound_dropped_loop, 1)

    def test_bridge_does_not_read_back_its_own_dds_writes(self):
        """
        The DDS-side loop guard is IGNORE_LOCAL_PARTICIPANT, not a payload
        tag: a bidirectional bridge writes to DDS inbound and reads DDS
        outbound, and must not see its own write.
        """
        from bridge import MqttDdsBridge

        topic = "spatialdds/infrastructure/sensing/detection3d/v1"
        cfg = _make_config("bidirectional", bridge_id="bridge-echo",
                           client_id="bridge-echo-mqtt")
        cfg.inbound_topics = [topic]
        cfg.outbound_topics = [topic]        # deliberately overlapping
        bridge = MqttDdsBridge(cfg)
        _announce_lane(topic, "oarc.detection3d_velocity", "RADAR_RT",
                       "infrastructure")
        bridge.start(block=False)
        time.sleep(3.0)

        echoed: List = []
        mqtt_sub = _new_mqtt_client("test-sub-echo")
        mqtt_sub.on_message = lambda _c, _u, msg: echoed.append(msg)
        mqtt_sub.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        mqtt_sub.subscribe(topic, qos=1)
        mqtt_sub.loop_start()
        time.sleep(1.0)
        try:
            _publish_mqtt("test-pub-echo", topic, _det_set("infrastructure"))
            time.sleep(2.0)
        finally:
            mqtt_sub.loop_stop()
            mqtt_sub.disconnect()
            bridge.stop()

        # One message on MQTT: the one the test published. If the bridge had
        # read its own DDS write back it would have republished it.
        from_bridge = [m for m in echoed
                       if dict(getattr(m.properties, "UserProperty", []) or [])
                       .get("spatialdds_bridge_id") == "bridge-echo"]
        self.assertEqual(from_bridge, [],
                         "bridge echoed its own DDS write back to MQTT")

    # ──────────────────────────────────────────────────────────────────────
    # Topic filtering
    # ──────────────────────────────────────────────────────────────────────

    def test_outbound_filter_excludes_unmatched(self):
        from cyclonedds.domain import DomainParticipant

        from bridge import MqttDdsBridge
        from spatialdds_demo import typed_transport as tt
        from spatialdds_idl.oarc_demo import OperatorDetectionSet

        topic = "spatialdds/operator_a/sensing/detection3d/v1"   # not outbound
        cfg = _make_config("outbound_only", bridge_id="bridge-filter",
                           client_id="bridge-filter-mqtt")
        bridge = MqttDdsBridge(cfg)
        _announce_lane(topic, "oarc.detection3d_velocity", "RADAR_RT",
                       "operator_a")
        bridge.start(block=False)
        time.sleep(3.0)

        received: List = []
        mqtt_sub = _new_mqtt_client("test-sub-filter")
        mqtt_sub.on_message = lambda _c, _u, msg: received.append(msg)
        mqtt_sub.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        mqtt_sub.subscribe("spatialdds/#", qos=1)
        mqtt_sub.loop_start()
        time.sleep(1.0)

        try:
            writer = tt.TypedDictWriter(DomainParticipant(DDS_DOMAIN), topic,
                                        OperatorDetectionSet, "RADAR_RT")
            time.sleep(2.0)
            writer.write(_det_set("operator_a"))
            time.sleep(2.0)
        finally:
            mqtt_sub.loop_stop()
            mqtt_sub.disconnect()
            bridge.stop()

        self.assertEqual([m for m in received if m.topic == topic], [],
                         "out-of-filter DDS message leaked to MQTT")
        self.assertGreaterEqual(bridge.stats.outbound_dropped_filter, 1)


if __name__ == "__main__":
    unittest.main()
