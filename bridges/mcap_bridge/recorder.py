#!/usr/bin/env python3
"""Record SpatialDDS envelope traffic to an MCAP file.

Subscribes to the single `spatialdds/envelope/v1` topic that every demo
publisher uses, then writes each envelope to MCAP keyed by its
`logical_topic` (channel) and `msg_type` (schema). The payload is already
JSON in the envelope, so we write its bytes verbatim — no per-dataclass
serialization needed.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

# Resolve sibling modules whether invoked as a script or via -m
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
# nuscenes/dds_envelope_transport lives at repo_root/nuscenes/
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcap.writer import Writer  # noqa: E402

from schema_registry import build_schema_table, default_schema  # noqa: E402


def _topic_matches(topic: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(topic, p) for p in patterns)


class _ChannelTable:
    """Lazy MCAP-channel/schema registration keyed by (logical_topic, msg_type).

    We don't know up front which logical_topics will appear (each demo invents
    its own operator namespaces). On first sight of a topic, register a channel
    that points at the schema for its msg_type; on first sight of a msg_type,
    register a schema.
    """

    def __init__(self, writer: Writer, schemas: Dict[str, dict]):
        self._writer = writer
        self._schemas = schemas
        self._schema_ids: Dict[str, int] = {}
        self._channel_ids: Dict[str, int] = {}

    def channel_id(self, logical_topic: str, msg_type: str) -> int:
        ch_id = self._channel_ids.get(logical_topic)
        if ch_id is not None:
            return ch_id
        schema_id = self._schema_id(msg_type)
        ch_id = self._writer.register_channel(
            schema_id=schema_id,
            topic=logical_topic,
            message_encoding="json",
            metadata={
                "spatialdds_msg_type": msg_type,
                "spatialdds_version": "1.7",
            },
        )
        self._channel_ids[logical_topic] = ch_id
        return ch_id

    def _schema_id(self, msg_type: str) -> int:
        sid = self._schema_ids.get(msg_type)
        if sid is not None:
            return sid
        schema = self._schemas.get(msg_type) or default_schema(msg_type)
        sid = self._writer.register_schema(
            name=msg_type,
            encoding="jsonschema",
            data=json.dumps(schema).encode("utf-8"),
        )
        self._schema_ids[msg_type] = sid
        return sid


def _make_lossless_reader(domain_id: int):
    """Build a CycloneDDS reader on the envelope topic with QoS tuned for
    recording: RELIABLE + KEEP_ALL so a burst of writes within one poll
    interval doesn't get collapsed to just the most recent sample (which
    is what `EnvelopeTransport`'s default best-effort + KEEP_LAST(1) does
    for live consumers).
    """
    from cyclonedds.core import Policy, Qos
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.sub import DataReader, Subscriber
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
    subscriber = Subscriber(participant)
    reader = DataReader(subscriber, topic, qos=qos)
    # Hold strong refs so they don't GC out from under the reader.
    reader._participant = participant
    reader._topic = topic
    reader._subscriber = subscriber
    return reader


def record(
    output_path: str,
    domain_id: int = 0,
    topics: Optional[Iterable[str]] = None,
    duration_sec: Optional[float] = None,
    schema_overrides: Optional[Dict[str, dict]] = None,
) -> Dict[str, int]:
    """Record envelopes from `domain_id` to `output_path`.

    Args:
        output_path: path to the .mcap file to create.
        domain_id: CycloneDDS domain to subscribe on.
        topics: optional list of logical_topic glob patterns to keep
            (e.g. ``["spatialdds/operator_a/*"]``). None = record everything.
        duration_sec: stop after this many seconds of wall time. None = run
            until SIGINT (Ctrl-C) or SIGTERM.
        schema_overrides: optional {msg_type: jsonschema-dict} to extend
            the default permissive schema table.

    Returns:
        ``{logical_topic: message_count}`` for diagnostics.
    """
    schemas = build_schema_table(schema_overrides)
    counts: Dict[str, int] = {}
    stop_event = threading.Event()

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    f = open(output_path, "wb")
    writer = Writer(f)
    writer.start(profile="x-jsonschema", library="spatialdds-mcap-bridge")
    table = _ChannelTable(writer, schemas)

    def _ingest(env: object) -> None:
        logical_topic = getattr(env, "logical_topic", "") or ""
        msg_type = getattr(env, "msg_type", "") or ""
        if topics and not _topic_matches(logical_topic, topics):
            return
        payload_json = getattr(env, "payload_json", "") or "{}"
        stamp_ns = int(getattr(env, "stamp_ns", 0) or time.time_ns())
        try:
            ch_id = table.channel_id(logical_topic, msg_type)
            writer.add_message(
                channel_id=ch_id,
                log_time=stamp_ns,
                publish_time=stamp_ns,
                data=payload_json.encode("utf-8"),
            )
        except Exception as exc:
            print(f"[recorder] write failed for {logical_topic}: {exc}", file=sys.stderr)
            return
        counts[logical_topic] = counts.get(logical_topic, 0) + 1

    reader = _make_lossless_reader(domain_id)

    def _sigint(_sig, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    print(f"[recorder] domain={domain_id} → {output_path}", file=sys.stderr)
    if topics:
        print(f"[recorder] filter: {list(topics)}", file=sys.stderr)
    print("[recorder] running; Ctrl-C to stop", file=sys.stderr)

    deadline = (time.monotonic() + duration_sec) if duration_sec else None
    try:
        while not stop_event.is_set():
            samples = reader.take(N=512)
            if samples:
                for sample in samples:
                    if sample is None or not hasattr(sample, "payload_json"):
                        continue
                    _ingest(sample)
            else:
                # No samples ready: short wait so SIGINT stays responsive.
                stop_event.wait(timeout=0.05)
            if deadline is not None and time.monotonic() >= deadline:
                break
    finally:
        # Drain anything still queued before closing the file.
        try:
            tail = reader.take(N=4096) or []
            for sample in tail:
                if sample is None or not hasattr(sample, "payload_json"):
                    continue
                _ingest(sample)
        except Exception:
            pass
        writer.finish()
        f.close()

    total = sum(counts.values())
    print(f"[recorder] wrote {total} messages across {len(counts)} topics → {output_path}", file=sys.stderr)
    for topic in sorted(counts):
        print(f"  {counts[topic]:>6}  {topic}", file=sys.stderr)
    return counts


def _main() -> int:
    parser = argparse.ArgumentParser(description="Record SpatialDDS envelope traffic to MCAP")
    parser.add_argument("output", help="Output .mcap file path")
    parser.add_argument("--domain", type=int, default=int(os.getenv("SPATIALDDS_DDS_DOMAIN", "0")))
    parser.add_argument(
        "--topic",
        action="append",
        dest="topics",
        help="Logical-topic glob to keep (repeatable). Default: keep everything.",
    )
    parser.add_argument("--duration", type=float, default=None, help="Stop after N seconds")
    args = parser.parse_args()
    record(
        args.output,
        domain_id=args.domain,
        topics=args.topics,
        duration_sec=args.duration,
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
