"""CLI entry point for the MQTT ↔ SpatialDDS bridge.

Usage:

  python -m bridges.mqtt_bridge --config bridges/mqtt_bridge/config.example.yaml

Or with environment overrides for the most common knobs:

  MQTT_BROKER=localhost MQTT_PORT=1883 SPATIALDDS_DDS_DOMAIN=1 \
      python -m bridges.mqtt_bridge --config <path>
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from bridge import MqttDdsBridge  # noqa: E402
from config import BridgeConfig  # noqa: E402


def _apply_env_overrides(cfg: BridgeConfig) -> None:
    if "MQTT_BROKER" in os.environ:
        cfg.mqtt_broker = os.environ["MQTT_BROKER"]
    if "MQTT_PORT" in os.environ:
        cfg.mqtt_port = int(os.environ["MQTT_PORT"])
    if "MQTT_USERNAME" in os.environ:
        cfg.mqtt_username = os.environ["MQTT_USERNAME"]
    if "MQTT_PASSWORD" in os.environ:
        cfg.mqtt_password = os.environ["MQTT_PASSWORD"]
    if "SPATIALDDS_DDS_DOMAIN" in os.environ:
        cfg.dds_domain_id = int(os.environ["SPATIALDDS_DDS_DOMAIN"])
    if "MQTT_BRIDGE_ID" in os.environ:
        cfg.bridge_id = os.environ["MQTT_BRIDGE_ID"]


def main() -> int:
    parser = argparse.ArgumentParser(description="SpatialDDS ↔ MQTT bridge")
    parser.add_argument("--config", required=True, help="Path to YAML config")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    cfg = BridgeConfig.from_yaml(args.config)
    _apply_env_overrides(cfg)
    cfg.validate()

    bridge = MqttDdsBridge(cfg)

    def _handle_sigint(_sig, _frame):
        bridge.stop()
        # bridge.start() loop will return after stop_event flips
    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    bridge.start(block=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
