"""MQTT ↔ SpatialDDS bridge.

Wires a paho-mqtt client to the shared envelope publisher/subscriber
(``bridges/envelope_io.py``). Topic strings are 1:1 between MQTT and the
SpatialDDS envelope's ``logical_topic`` — no translation. Payloads are JSON
on both sides.

Loop prevention
---------------

Every message the bridge republishes carries a ``_bridge_id`` field set
to ``config.bridge_id``. Messages received with the bridge's own
``_bridge_id`` are dropped. Combined with non-overlapping inbound /
outbound topic filters, this prevents the obvious echo loop where the
bridge re-relays its own output forever.
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

    # The MQTT user-property name used to override topic-based msg_type
    # inference, when the publisher knows the type explicitly.
    USER_PROPERTY_MSG_TYPE = "spatialdds_msg_type"

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.stats = BridgeStats()
        self._stop_event = threading.Event()
        self._envelope_pub = None      # bridges.envelope_io.EnvelopePublisher
        self._envelope_sub = None      # bridges.envelope_io.EnvelopeSubscriber
        self._mqtt = None              # paho.mqtt.client.Client
        self._mqtt_lock = threading.Lock()  # paho's thread-safety story is fuzzy

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, block: bool = True) -> None:
        """Connect MQTT, set up DDS endpoints, run until stopped."""
        # Lazy-import paho/cyclonedds so unit tests can import the module
        # without these deps installed.
        import paho.mqtt.client as mqtt
        from envelope_io import EnvelopePublisher, EnvelopeSubscriber

        # ── DDS side ────────────────────────────────────────────────────
        if self.config.direction in ("bidirectional", "inbound_only"):
            self._envelope_pub = EnvelopePublisher(self.config.dds_domain_id)

        if self.config.direction in ("bidirectional", "outbound_only"):
            self._envelope_sub = EnvelopeSubscriber(
                self.config.dds_domain_id,
                callback=self._on_dds_envelope,
            )

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

        if self._envelope_sub is not None:
            self._envelope_sub.start()

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
        if self._envelope_sub is not None:
            try:
                self._envelope_sub.stop()
            except Exception:
                pass
        if self._mqtt is not None:
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
        if self._envelope_pub is not None:
            try:
                self._envelope_pub.close()
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

        # Loop prevention
        if payload.get("_bridge_id") == self.config.bridge_id:
            self.stats.inbound_dropped_loop += 1
            return

        # Topic filter — should already be matched by the broker via
        # subscription, but double-check in case of misconfig.
        if not matches_any(topic, self.config.inbound_topics):
            self.stats.inbound_dropped_filter += 1
            return

        msg_type = self._extract_msg_type(msg) or infer_msg_type(topic)

        # Tag with bridge_id so the corresponding /ws or DDS subscriber
        # can recognize bridged traffic. Tagging in the OUTBOUND direction
        # also prevents an immediate ping-pong when bidirectional bridges
        # peer with each other.
        payload["_bridge_id"] = self.config.bridge_id

        try:
            self._envelope_pub.publish(
                logical_topic=topic,
                msg_type=msg_type,
                payload=payload,
            )
            self.stats.inbound_count += 1
            self.stats.last_inbound_topic = topic
        except Exception as exc:
            logger.exception("[mqtt_bridge] DDS publish failed for %s: %s", topic, exc)
            self.stats.inbound_errors += 1

    def _extract_msg_type(self, msg) -> Optional[str]:
        """If the publisher attached a ``spatialdds_msg_type`` MQTT v5 user
        property, prefer it over topic-based inference."""
        properties = getattr(msg, "properties", None)
        if properties is None:
            return None
        user_props = getattr(properties, "UserProperty", None) or []
        for k, v in user_props:
            if k == self.USER_PROPERTY_MSG_TYPE:
                return str(v)
        return None

    # ── DDS → MQTT ──────────────────────────────────────────────────────

    def _on_dds_envelope(self, msg_type: str, logical_topic: str,
                           payload: dict, stamp_ns: int) -> None:
        """``EnvelopeSubscriber`` callback. Runs on the DDS poll thread."""
        if not isinstance(payload, dict):
            return
        # Loop prevention — we receive our OWN inbound publish back via
        # DDS too; drop it.
        if payload.get("_bridge_id") == self.config.bridge_id:
            self.stats.outbound_dropped_loop += 1
            return
        if not matches_any(logical_topic, self.config.outbound_topics):
            self.stats.outbound_dropped_filter += 1
            return

        # Tag for the receiving side so a peer bridge knows it's bridged.
        out = dict(payload)
        out["_bridge_id"] = self.config.bridge_id

        qos, retain = get_qos(logical_topic)

        try:
            with self._mqtt_lock:
                info = self._mqtt.publish(
                    topic=logical_topic,
                    payload=json.dumps(out),
                    qos=qos,
                    retain=retain,
                )
            self.stats.outbound_count += 1
            self.stats.last_outbound_topic = logical_topic
        except Exception as exc:
            logger.exception("[mqtt_bridge] MQTT publish failed for %s: %s",
                              logical_topic, exc)
            self.stats.outbound_errors += 1

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
