#!/usr/bin/env python3
"""Replay an MCAP file recorded by ``recorder.py`` back onto a DDS domain.

Reads messages in log-time order, sleeps to preserve relative spacing
(scaled by ``--speed``), and republishes each message as a SpatialDDS
envelope using the same `logical_topic` and `msg_type` it was recorded with.
The payload is the raw JSON bytes from the MCAP message.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcap.reader import make_reader  # noqa: E402


def _make_lossless_writer(domain_id: int):
    """RELIABLE + KEEP_ALL writer on the envelope topic so a burst replay
    isn't collapsed to KEEP_LAST(1) at the writer's history cache. Mirror
    of recorder._make_lossless_reader. Both sides need the deeper QoS for
    the bridge to be fully lossless."""
    from cyclonedds.core import Policy, Qos
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.pub import DataWriter, Publisher
    from cyclonedds.topic import Topic

    from nuscenes.dds_envelope_transport import (
        SpatialDDSEnvelope,
        TOPIC_DDS_ENVELOPE_V1,
    )

    qos = Qos(
        Policy.Reliability.Reliable(0),
        Policy.History.KeepAll,
        Policy.Durability.Volatile,
    )
    participant = DomainParticipant(domain_id)
    topic = Topic(participant, TOPIC_DDS_ENVELOPE_V1, SpatialDDSEnvelope)
    publisher = Publisher(participant)
    writer = DataWriter(publisher, topic, qos=qos)
    writer._participant = participant
    writer._topic = topic
    writer._publisher = publisher
    return writer, SpatialDDSEnvelope


def replay(
    mcap_path: str,
    domain_id: int = 0,
    speed: float = 1.0,
    loop: bool = False,
    sender_id: Optional[str] = "mcap-replayer",
) -> int:
    """Replay `mcap_path` to DDS domain `domain_id`.

    Args:
        mcap_path: path to a .mcap file produced by the recorder.
        domain_id: CycloneDDS domain to publish on.
        speed: playback speed multiplier (2.0 = double speed). 0 or None
            falls through to 1.0; non-positive values are clamped.
        loop: if True, replay indefinitely until SIGINT.
        sender_id: identity used for envelope self-echo filtering.

    Returns:
        Total number of messages published.
    """
    speed = max(float(speed or 1.0), 0.001)
    writer, EnvelopeCls = _make_lossless_writer(domain_id)
    total = 0

    try:
        while True:
            with open(mcap_path, "rb") as f:
                reader = make_reader(f)
                summary = reader.get_summary()
                stats = summary.statistics if summary else None
                expected = stats.message_count if stats else None
                print(
                    f"[replayer] {mcap_path}: "
                    f"{expected if expected is not None else '?'} messages",
                    file=sys.stderr,
                )

                first_log_time: Optional[int] = None
                wall_start = time.monotonic()
                published = 0
                for schema, channel, message in reader.iter_messages(log_time_order=True):
                    if first_log_time is None:
                        first_log_time = message.log_time

                    target_offset = (message.log_time - first_log_time) / 1e9 / speed
                    sleep_for = target_offset - (time.monotonic() - wall_start)
                    if sleep_for > 0:
                        time.sleep(sleep_for)

                    msg_type = (schema.name if schema is not None else "") or ""
                    if channel is not None and channel.metadata:
                        msg_type = channel.metadata.get("spatialdds_msg_type", msg_type)
                    logical_topic = channel.topic if channel is not None else ""
                    payload = message.data.decode("utf-8")
                    envelope = EnvelopeCls(
                        msg_type=msg_type,
                        logical_topic=logical_topic,
                        payload_json=payload,
                        stamp_ns=int(message.log_time),
                        request_id="",
                    )
                    writer.write(envelope)
                    published += 1

                total += published
                print(f"[replayer] published {published} messages", file=sys.stderr)

            if not loop:
                break
            print("[replayer] looping…", file=sys.stderr)
    except KeyboardInterrupt:
        print("[replayer] interrupted", file=sys.stderr)
    finally:
        # Give RELIABLE peers a moment to ack before tearing down.
        time.sleep(0.5)
        del writer

    return total


def _main() -> int:
    parser = argparse.ArgumentParser(description="Replay an MCAP file to a SpatialDDS domain")
    parser.add_argument("input", help="Input .mcap file path")
    parser.add_argument("--domain", type=int, default=int(os.getenv("SPATIALDDS_DDS_DOMAIN", "0")))
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    replay(args.input, domain_id=args.domain, speed=args.speed, loop=args.loop)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
