"""YAML config loading for the MQTT ↔ SpatialDDS bridge.

Schema (full example): ``bridges/mqtt_bridge/config.example.yaml``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class TLSConfig:
    ca_cert: str
    client_cert: Optional[str] = None
    client_key: Optional[str] = None


@dataclass
class BridgeConfig:
    # MQTT
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = "spatialdds-mqtt-bridge"
    mqtt_keepalive: int = 60
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    tls: Optional[TLSConfig] = None

    # DDS
    dds_domain_id: int = 0

    # Bridge behavior
    direction: str = "bidirectional"   # bidirectional | inbound_only | outbound_only
    bridge_id: str = "mqtt-bridge-01"

    # Topic filters
    inbound_topics: List[str] = field(default_factory=list)
    outbound_topics: List[str] = field(default_factory=list)

    # Operational knobs
    log_interval_s: float = 10.0

    @classmethod
    def from_yaml(cls, path: str) -> "BridgeConfig":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "BridgeConfig":
        mqtt = data.get("mqtt", {}) or {}
        dds = data.get("dds", {}) or {}
        bridge = data.get("bridge", {}) or {}

        tls = None
        if mqtt.get("tls"):
            tls = TLSConfig(
                ca_cert=str(mqtt["tls"]["ca_cert"]),
                client_cert=mqtt["tls"].get("client_cert"),
                client_key=mqtt["tls"].get("client_key"),
            )

        return cls(
            mqtt_broker=str(mqtt.get("broker", "localhost")),
            mqtt_port=int(mqtt.get("port", 1883)),
            mqtt_client_id=str(mqtt.get("client_id", "spatialdds-mqtt-bridge")),
            mqtt_keepalive=int(mqtt.get("keepalive", 60)),
            mqtt_username=mqtt.get("username"),
            mqtt_password=mqtt.get("password"),
            tls=tls,
            dds_domain_id=int(dds.get("domain_id", 0)),
            direction=str(bridge.get("direction", "bidirectional")),
            bridge_id=str(bridge.get("bridge_id", "mqtt-bridge-01")),
            inbound_topics=list(bridge.get("inbound_topics") or []),
            outbound_topics=list(bridge.get("outbound_topics") or []),
            log_interval_s=float(bridge.get("log_interval_s", 10.0)),
        )

    def validate(self) -> None:
        if self.direction not in ("bidirectional", "inbound_only", "outbound_only"):
            raise ValueError(
                f"direction must be bidirectional/inbound_only/outbound_only "
                f"(got {self.direction!r})"
            )
        if self.direction in ("bidirectional", "inbound_only") and not self.inbound_topics:
            raise ValueError(
                "inbound_topics must be non-empty for bidirectional/inbound_only"
            )
        if self.direction in ("bidirectional", "outbound_only") and not self.outbound_topics:
            raise ValueError(
                "outbound_topics must be non-empty for bidirectional/outbound_only"
            )
        if self.tls and not Path(self.tls.ca_cert).exists():
            raise ValueError(f"TLS ca_cert not found: {self.tls.ca_cert}")
