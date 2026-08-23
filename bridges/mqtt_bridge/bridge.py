"""MQTT <-> SpatialDDS bridge.

A typed adapter, not a relay. DDS carries real samples; MQTT carries JSON,
because that is what MQTT clients expect. Inbound, the topic (or an MQTT v5
user property) names a §3.3.2 type, the JSON is built into that type, and a
malformed payload is refused at the bridge instead of being republished as a
well-formed string nobody can parse. Outbound, typed samples are serialised
once, here.

Topic strings are 1:1 between MQTT and DDS — no translation.

Loop prevention
---------------

Two independent mechanisms, neither of which touches the payload:

* **DDS side** — the bridge's readers carry IGNORE_LOCAL_PARTICIPANT, so
  they never see what the bridge's own writers published. DDS answers this
  at the middleware; previously the bridge injected a ``_bridge_id`` field
  into the payload and filtered on it, which is not expressible in a typed
  struct and was never really the payload's business.
* **MQTT side** — the bridge tags what it publishes with a ``spatialdds_bridge_id``
  MQTT v5 user property and drops anything arriving with its own id, so two
  peered bridges do not ping-pong. Transport metadata rides in transport
  headers.

Non-overlapping inbound / outbound topic filters remain the first line of
defence for both.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Sibling envelope helper
_HERE = Path(__file__).resolve().parent
_BRIDGES = _HERE.parent
for p in (str(_HERE), str(_BRIDGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from topic_mapping import infer_msg_type, get_qos, matches_any, to_broker_filter  # noqa: E402
from config import BridgeConfig  # noqa: E402

logger = logging.getLogger("spatialdds.mqtt_bridge")


@dataclass
class BridgeStats:
    inbound_count: int = 0
    outbound_count: int = 0
    inbound_dropped_loop: int = 0
    outbound_dropped_loop: int = 0
    inbound_dropped_filter: int = 0
    outbound_dropped_filter: int = 0
    inbound_errors: int = 0
    outbound_errors: int = 0
    last_inbound_topic: str = ""
    last_outbound_topic: str = ""
    last_log_time: float = 0.0


class MqttDdsBridge:
    """Long-lived MQTT ↔ DDS relay. Call ``start()`` to run; ``stop()`` to
    tear down (or send SIGINT)."""

    # MQTT v5 user property naming the bridge that published a message, so
    # a peer bridge can drop its own traffic without a payload field.
    USER_PROPERTY_BRIDGE_ID = "spatialdds_bridge_id"

    # The MQTT user-property name used to override topic-based msg_type
    # inference, when the publisher knows the type explicitly.
    USER_PROPERTY_MSG_TYPE = "spatialdds_msg_type"

    # 3.3.3 QoS profile per registered type. A message bridged in from MQTT
    # goes onto the lane the spec assigns its type — not onto one profile
    # the bridge picked for everything, which is what a single envelope
    # topic forced.
    DDS_QOS_PROFILES = {
        "geopose": "POSE_RT",
        "navsat_status": "POSE_RT",
        "planned_trajectory": "EVENT_RT",
        "entity_binding": "MAP_META",
        "spatial_event": "EVENT_RT",
        "video_frame": "VIDEO_LIVE",
        "radar_tensor": "RADAR_RT",
        "radar_detection": "RADAR_RT",
        "oarc.detection3d_set": "RADAR_RT",
        "rf_beam": "RF_BEAM_RT",
        "oarc.detection3d_velocity": "RADAR_RT",
        "oarc.framed_pose": "POSE_RT",
        "oarc.fused_track": "POSE_RT",
        "oarc.fusion_coverage": "MAP_META",
        "oarc.lidar_frame": "GEOM_TILE",
        "oarc.lidar_meta": "MAP_META",
        "oarc.radar_tensor_meta": "MAP_META",
        "oarc.video_frame_meta": "MAP_META",
        "oarc.rf_beam_meta": "MAP_META",
    }
    DEFAULT_DDS_PROFILE = "EVENT_RT"

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.stats = BridgeStats()
        self._stop_event = threading.Event()
        self._participant = None       # cyclonedds DomainParticipant
        self._writers: dict = {}       # logical_topic -> TypedDictWriter
        self._stream_sub = None        # spatialdds_demo.stream.StreamSubscriber
        self._pump = None              # thread polling the stream subscriber
        self._mqtt = None              # paho.mqtt.client.Client
        self._mqtt_lock = threading.Lock()  # paho's thread-safety story is fuzzy

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, block: bool = True) -> None:
        """Connect MQTT, set up DDS endpoints, run until stopped."""
        # Lazy-import paho/cyclonedds so unit tests can import the module
        # without these deps installed.
        import paho.mqtt.client as mqtt
        from cyclonedds.domain import DomainParticipant

        from spatialdds_demo.stream import StreamSubscriber

        # ── DDS side ────────────────────────────────────────────────────
        # One participant for both directions, which is what makes
        # IGNORE_LOCAL_PARTICIPANT the loop guard: the readers below are in
        # the same participant as the writers this bridge creates inbound.
        self._participant = DomainParticipant(self.config.dds_domain_id)

        if self.config.direction in ("bidirectional", "outbound_only"):
            self._stream_sub = StreamSubscriber(
                self._participant, self._on_dds_sample,
                on_announce=self._on_dds_announce,
                ignore_local=True,
            )
            self._pump = threading.Thread(target=self._poll_dds, daemon=True)
            self._pump.start()

        # ── MQTT side ───────────────────────────────────────────────────
        client_kwargs = {
            "client_id": self.config.mqtt_client_id,
            "protocol": mqtt.MQTTv5,
        }
        if hasattr(mqtt, "CallbackAPIVersion"):
            client_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        self._mqtt = mqtt.Client(**client_kwargs)
        self._mqtt.on_connect = self._on_mqtt_connect
        self._mqtt.on_disconnect = self._on_mqtt_disconnect
        self._mqtt.on_message = self._on_mqtt_message

        if self.config.mqtt_username:
            self._mqtt.username_pw_set(
                self.config.mqtt_username, self.config.mqtt_password or "")
        if self.config.tls:
            self._mqtt.tls_set(
                ca_certs=self.config.tls.ca_cert,
                certfile=self.config.tls.client_cert,
                keyfile=self.config.tls.client_key,
            )

        logger.info(
            "[mqtt_bridge] direction=%s mqtt=%s:%d dds_domain=%d",
            self.config.direction,
            self.config.mqtt_broker,
            self.config.mqtt_port,
            self.config.dds_domain_id,
        )
        self._mqtt.connect(
            self.config.mqtt_broker,
            self.config.mqtt_port,
            keepalive=self.config.mqtt_keepalive,
        )
        self._mqtt.loop_start()

        if not block:
            return

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=self.config.log_interval_s)
                self._log_stats()
        except KeyboardInterrupt:
            logger.info("[mqtt_bridge] interrupted")
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        if self._pump is not None:
            self._pump.join(timeout=2)
        if self._mqtt is not None:
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
        self._log_stats(force=True)

    # ── MQTT → DDS ──────────────────────────────────────────────────────

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties=None):
        # paho-mqtt v2 callback signature: (client, userdata, flags, reason_code, properties)
        if hasattr(reason_code, "is_failure") and reason_code.is_failure:
            logger.error("[mqtt_bridge] MQTT connect failed: %s", reason_code)
            return
        logger.info("[mqtt_bridge] MQTT connected")
        if self.config.direction not in ("bidirectional", "inbound_only"):
            return
        # Subscribe with a coarsened filter (MQTT spec doesn't allow ``+``
        # mixed with literals in one segment); per-message filtering
        # against the original pattern still happens in ``matches_any``.
        seen = set()
        for pattern in self.config.inbound_topics:
            broker_filter = to_broker_filter(pattern)
            if broker_filter in seen:
                continue
            seen.add(broker_filter)
            try:
                client.subscribe(broker_filter, qos=1)
                if broker_filter == pattern:
                    logger.info("[mqtt_bridge]   subscribed: %s", pattern)
                else:
                    logger.info("[mqtt_bridge]   subscribed: %s  (filter: %s)",
                                 pattern, broker_filter)
            except Exception as exc:
                logger.error("[mqtt_bridge] subscribe failed for %s: %s",
                              broker_filter, exc)

    def _on_mqtt_disconnect(self, client, userdata, *args, **kwargs):
        logger.warning("[mqtt_bridge] MQTT disconnected")

    def _on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            logger.error("[mqtt_bridge] non-JSON payload on %s: %s", topic, exc)
            self.stats.inbound_errors += 1
            return
        if not isinstance(payload, dict):
            logger.error("[mqtt_bridge] payload on %s is not a JSON object", topic)
            self.stats.inbound_errors += 1
            return

        # Loop prevention: our own traffic coming back from a peer bridge.
        # The id is an MQTT user property, so it never enters the payload
        # and never has to be expressible as a struct field.
        if self._extract_bridge_id(msg) == self.config.bridge_id:
            self.stats.inbound_dropped_loop += 1
            return

        # Topic filter — should already be matched by the broker via
        # subscription, but double-check in case of misconfig.
        if not matches_any(topic, self.config.inbound_topics):
            self.stats.inbound_dropped_filter += 1
            return

        msg_type = self._extract_msg_type(msg) or infer_msg_type(topic)

        try:
            self._dds_writer(topic, msg_type).write(payload)
            self.stats.inbound_count += 1
            self.stats.last_inbound_topic = topic
        except Exception as exc:
            # Includes an unresolvable type and a payload that is not a
            # well-formed sample of it. Both are the bridge's problem to
            # report, not the next consumer's to discover.
            logger.error("[mqtt_bridge] DDS publish failed for %s (%s): %s",
                         topic, msg_type, exc)
            self.stats.inbound_errors += 1

    def _dds_writer(self, logical_topic: str, msg_type: str):
        """A typed writer for ``logical_topic``, created on first use."""
        writer = self._writers.get(logical_topic)
        if writer is not None:
            return writer

        from spatialdds_demo import topic_types, typed_transport as tt

        datatype = topic_types.resolve(msg_type)
        writer = tt.TypedDictWriter(
            self._participant, logical_topic, datatype,
            self.DDS_QOS_PROFILES.get(msg_type, self.DEFAULT_DDS_PROFILE))
        self._writers[logical_topic] = writer
        return writer

    def _extract_bridge_id(self, msg) -> Optional[str]:
        return self._user_property(msg, self.USER_PROPERTY_BRIDGE_ID)

    def _extract_msg_type(self, msg) -> Optional[str]:
        """If the publisher attached a ``spatialdds_msg_type`` MQTT v5 user
        property, prefer it over topic-based inference."""
        return self._user_property(msg, self.USER_PROPERTY_MSG_TYPE)

    @staticmethod
    def _user_property(msg, name: str) -> Optional[str]:
        properties = getattr(msg, "properties", None)
        if properties is None:
            return None
        for k, v in (getattr(properties, "UserProperty", None) or []):
            if k == name:
                return str(v)
        return None

    # ── DDS → MQTT ──────────────────────────────────────────────────────

    def _poll_dds(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._stream_sub.poll(stamp_ns=time.time_ns())
            except Exception:
                # One bad sample must not stop the bridge; next poll continues.
                logger.exception("[mqtt_bridge] DDS poll failed")
            self._stop_event.wait(0.02)

    def _on_dds_announce(self, service_id: str, announce: dict) -> None:
        """
        Announces cross as a message on a per-service topic.

        On DDS an announce is a keyed instance and a departure is a dispose;
        MQTT has neither, which is why C.5 asks for a `Depart` message
        alongside the dispose. This is the crossing point where that matters.
        """
        name = announce.get("name") or service_id.removeprefix("svc:")
        self._on_dds_sample("spatialdds/discovery/announce",
                            f"spatialdds/{name}/discovery/announce/v1",
                            announce, time.time_ns())

    def _on_dds_sample(self, type_name: str, logical_topic: str,
                       payload: dict, stamp_ns: int) -> None:
        """
        One typed sample, serialised for MQTT. Runs on the DDS poll thread.

        Nothing checks for the bridge's own traffic here: the reader carries
        IGNORE_LOCAL_PARTICIPANT, so what this bridge wrote never arrives.
        """
        if not isinstance(payload, dict):
            return
        if not matches_any(logical_topic, self.config.outbound_topics):
            self.stats.outbound_dropped_filter += 1
            return

        qos, retain = get_qos(logical_topic)
        properties = self._publish_properties(type_name)

        try:
            with self._mqtt_lock:
                self._mqtt.publish(
                    topic=logical_topic,
                    payload=json.dumps(payload),
                    qos=qos,
                    retain=retain,
                    properties=properties,
                )
            self.stats.outbound_count += 1
            self.stats.last_outbound_topic = logical_topic
        except Exception as exc:
            logger.exception("[mqtt_bridge] MQTT publish failed for %s: %s",
                              logical_topic, exc)
            self.stats.outbound_errors += 1

    def _publish_properties(self, type_name: str):
        """
        MQTT v5 user properties carrying what used to be in the payload: the
        bridge id (loop prevention) and the SpatialDDS type, so a subscriber
        knows what the JSON is without inferring it from the topic.
        """
        try:
            from paho.mqtt.properties import Properties
            from paho.mqtt.packettypes import PacketTypes
        except Exception:                                   # pragma: no cover
            return None
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [
            (self.USER_PROPERTY_BRIDGE_ID, self.config.bridge_id),
            (self.USER_PROPERTY_MSG_TYPE, type_name),
        ]
        return props

    # ── Stats ──────────────────────────────────────────────────────────

    def _log_stats(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self.stats.last_log_time) < self.config.log_interval_s:
            return
        self.stats.last_log_time = now
        logger.info(
            "[mqtt_bridge] in=%d (drop loop=%d filter=%d err=%d)  "
            "out=%d (drop loop=%d filter=%d err=%d)",
            self.stats.inbound_count, self.stats.inbound_dropped_loop,
            self.stats.inbound_dropped_filter, self.stats.inbound_errors,
            self.stats.outbound_count, self.stats.outbound_dropped_loop,
            self.stats.outbound_dropped_filter, self.stats.outbound_errors,
        )
