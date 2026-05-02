"""Tier-2 integration tests for the MQTT ↔ DDS bridge.

Spins up the bridge in-process against a running Mosquitto broker and the
local CycloneDDS bus. Each test publishes on one side and asserts the
other side receives.

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
    from nuscenes.dds_envelope_transport import EnvelopeTransport
    warm = EnvelopeTransport(lambda _e: None, DDS_DOMAIN, "mqtt-prewarm")
    warm.start()
    time.sleep(0.2)
    warm.stop()


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
    # Inbound: MQTT publish → bridge → DDS subscriber sees envelope
    # ──────────────────────────────────────────────────────────────────────

    def test_mqtt_to_dds_inbound(self):
        from bridge import MqttDdsBridge

        cfg = _make_config("inbound_only", bridge_id="bridge-in",
                            client_id="bridge-in-mqtt")
        bridge = MqttDdsBridge(cfg)
        bridge.start(block=False)
        time.sleep(2.0)  # MQTT connect + DDS discovery

        # Subscribe on DDS via the lossless reader from envelope_io
        from envelope_io import EnvelopeSubscriber
        received: List[dict] = []

        def on_dds(msg_type, logical_topic, payload, _stamp_ns):
            if logical_topic.startswith("spatialdds/operator_test/"):
                received.append({
                    "msg_type": msg_type,
                    "logical_topic": logical_topic,
                    "payload": payload,
                })

        sub = EnvelopeSubscriber(DDS_DOMAIN, on_dds)
        sub.start()
        time.sleep(2.0)

        try:
            mqtt_pub = _new_mqtt_client("test-pub-inbound")
            mqtt_pub.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
            mqtt_pub.loop_start()
            mqtt_pub.publish(
                "spatialdds/operator_test/sensing/detection3d/v1",
                json.dumps({"detections": [{"det_id": "d1"}],
                             "source_operator": "operator_test"}),
                qos=1,
            )
            time.sleep(2.0)
            mqtt_pub.loop_stop()
            mqtt_pub.disconnect()
        finally:
            sub.stop()
            bridge.stop()

        self.assertGreaterEqual(len(received), 1,
            "DDS side should have seen at least one envelope")
        match = next((r for r in received
                       if r["msg_type"] == "Detection3DSet"), None)
        self.assertIsNotNone(match, f"no Detection3DSet seen — got {received}")
        self.assertEqual(match["logical_topic"],
                          "spatialdds/operator_test/sensing/detection3d/v1")
        self.assertEqual(match["payload"]["detections"][0]["det_id"], "d1")
        # Bridge tags every relayed payload with its bridge_id
        self.assertEqual(match["payload"].get("_bridge_id"), "bridge-in")

    # ──────────────────────────────────────────────────────────────────────
    # Outbound: DDS publish → bridge → MQTT subscriber sees message
    # ──────────────────────────────────────────────────────────────────────

    def test_dds_to_mqtt_outbound(self):
        from bridge import MqttDdsBridge

        cfg = _make_config("outbound_only", bridge_id="bridge-out",
                            client_id="bridge-out-mqtt")
        bridge = MqttDdsBridge(cfg)
        bridge.start(block=False)
        time.sleep(2.0)

        # Subscribe on MQTT
        received: List = []
        mqtt_sub = _new_mqtt_client("test-sub-outbound")

        def on_mqtt(_client, _ud, msg):
            received.append(msg)

        mqtt_sub.on_message = on_mqtt
        mqtt_sub.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        mqtt_sub.subscribe("spatialdds/platform/fusion/#", qos=1)
        mqtt_sub.loop_start()
        time.sleep(1.0)

        try:
            from envelope_io import EnvelopePublisher
            pub = EnvelopePublisher(DDS_DOMAIN)
            pub.publish(
                logical_topic="spatialdds/platform/fusion/track/v1",
                msg_type="FusedTrackSet",
                payload={"tracks": [{"track_id": "t1"}], "frame_seq": 7},
            )
            time.sleep(2.0)
            pub.close()
        finally:
            mqtt_sub.loop_stop()
            mqtt_sub.disconnect()
            bridge.stop()

        self.assertGreaterEqual(len(received), 1,
            "MQTT subscriber should have received at least one message")
        msg = received[0]
        self.assertEqual(msg.topic, "spatialdds/platform/fusion/track/v1")
        body = json.loads(msg.payload.decode("utf-8"))
        self.assertEqual(body["tracks"][0]["track_id"], "t1")
        self.assertEqual(body.get("_bridge_id"), "bridge-out")

    # ──────────────────────────────────────────────────────────────────────
    # Loop prevention: messages tagged with the bridge's own bridge_id
    # don't get re-relayed.
    # ──────────────────────────────────────────────────────────────────────

    def test_loop_prevention_inbound(self):
        from bridge import MqttDdsBridge

        cfg = _make_config("inbound_only", bridge_id="bridge-loop",
                            client_id="bridge-loop-mqtt")
        bridge = MqttDdsBridge(cfg)
        bridge.start(block=False)
        time.sleep(2.0)

        from envelope_io import EnvelopeSubscriber
        received: List[dict] = []

        def on_dds(_mt, logical_topic, payload, _ns):
            if logical_topic.startswith("spatialdds/operator_loop/"):
                received.append(payload)

        sub = EnvelopeSubscriber(DDS_DOMAIN, on_dds)
        sub.start()
        time.sleep(2.0)

        try:
            mqtt_pub = _new_mqtt_client("test-pub-loop")
            mqtt_pub.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
            mqtt_pub.loop_start()
            # Tag with the bridge's own bridge_id → must be dropped
            mqtt_pub.publish(
                "spatialdds/operator_loop/sensing/detection3d/v1",
                json.dumps({"detections": [], "_bridge_id": "bridge-loop"}),
                qos=1,
            )
            time.sleep(1.5)
            mqtt_pub.loop_stop()
            mqtt_pub.disconnect()
        finally:
            sub.stop()
            bridge.stop()

        self.assertEqual(len(received), 0,
            f"Bridge republished its own message: {received}")
        self.assertGreaterEqual(bridge.stats.inbound_dropped_loop, 1)

    # ──────────────────────────────────────────────────────────────────────
    # Topic filtering: a message that doesn't match outbound_topics is not
    # relayed even if it arrives on DDS.
    # ──────────────────────────────────────────────────────────────────────

    def test_outbound_filter_excludes_unmatched(self):
        from bridge import MqttDdsBridge

        cfg = _make_config("outbound_only", bridge_id="bridge-filter",
                            client_id="bridge-filter-mqtt")
        bridge = MqttDdsBridge(cfg)
        bridge.start(block=False)
        time.sleep(2.0)

        received: List = []
        mqtt_sub = _new_mqtt_client("test-sub-filter")
        mqtt_sub.on_message = lambda _c, _u, msg: received.append(msg)
        mqtt_sub.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        # Wide subscription so we'd see anything that DOES make it through
        mqtt_sub.subscribe("spatialdds/#", qos=1)
        mqtt_sub.loop_start()
        time.sleep(1.0)

        try:
            from envelope_io import EnvelopePublisher
            pub = EnvelopePublisher(DDS_DOMAIN)
            # NOT in outbound_topics filter — should be dropped
            pub.publish(
                logical_topic="spatialdds/operator_a/sensing/detection3d/v1",
                msg_type="Detection3DSet",
                payload={"detections": []},
            )
            time.sleep(1.5)
            pub.close()
        finally:
            mqtt_sub.loop_stop()
            mqtt_sub.disconnect()
            bridge.stop()

        unmatched = [m for m in received
                       if m.topic == "spatialdds/operator_a/sensing/detection3d/v1"]
        self.assertEqual(len(unmatched), 0,
            "Out-of-filter DDS message leaked to MQTT")
        self.assertGreaterEqual(bridge.stats.outbound_dropped_filter, 1)


if __name__ == "__main__":
    unittest.main()
