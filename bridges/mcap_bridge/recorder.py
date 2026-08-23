#!/usr/bin/env python3
"""Record SpatialDDS traffic to an MCAP file.

Discovers lanes from announces, opens a typed reader per lane, and writes
each sample as JSON under a channel named by its topic and a schema
generated from its IDL type.

The schema is the substantive change. Under the envelope there was one DDS
topic and every payload was an opaque string, so a recording could say what
its messages were called and nothing about what was in them; every schema
was a permissive `{"type": "object"}`. Now each channel carries the real
shape of its messages, which is what makes an MCAP file readable by someone
who does not have this repo.

JSON is still the on-disk encoding — MCAP tooling (`mcap cat`, Foxglove)
reads it without a plugin, and that is worth more here than byte-for-byte
CDR fidelity. Replay rebuilds the typed sample from it.
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


class _Recorder:
    """
    A typed reader per announced lane, feeding one MCAP writer.

    Recording is discovery-driven for the same reason consuming is: with a
    topic per type there is no single topic to subscribe to, and the
    announce already says which topics exist and what is on them.
    """

    def __init__(self, domain_id: int, on_sample):
        from cyclonedds.domain import DomainParticipant

        from spatialdds_demo.stream import StreamSubscriber

        self._on_sample = on_sample
        self._sub = StreamSubscriber(
            DomainParticipant(domain_id), self._deliver,
            on_announce=self._deliver_announce,
        )

    def _deliver(self, type_name, topic, payload, stamp_ns):
        self._on_sample(type_name, topic, payload, stamp_ns)

    def _deliver_announce(self, service_id, announce):
        # Announces are recorded too: without them a replay has no way to
        # tell a consumer what is on the topics it is about to write to.
        name = announce.get("name") or service_id.removeprefix("svc:")
        self._on_sample(ANNOUNCE_TYPE,
                        f"spatialdds/{name}/discovery/announce/v1",
                        announce, time.time_ns())

    def poll(self):
        self._sub.poll(stamp_ns=time.time_ns())


# Discovery is not a TopicMeta lane, so it has no registry type name.
ANNOUNCE_TYPE = "spatialdds/discovery/announce"


def record(
    output_path: str,
    domain_id: int = 0,
    topics: Optional[Iterable[str]] = None,
    duration_sec: Optional[float] = None,
    schema_overrides: Optional[Dict[str, dict]] = None,
) -> Dict[str, int]:
    """Record typed SpatialDDS traffic from `domain_id` to `output_path`.

    Args:
        output_path: path to the .mcap file to create.
        domain_id: CycloneDDS domain to subscribe on.
        topics: optional list of logical_topic glob patterns to keep
            (e.g. ``["spatialdds/operator_a/*"]``). None = record everything.
        duration_sec: stop after this many seconds of wall time. None = run
            until SIGINT (Ctrl-C) or SIGTERM.
        schema_overrides: optional {type_name: jsonschema-dict} replacing
            the schema generated from that type's IDL.

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

    def _ingest(msg_type: str, logical_topic: str, payload: dict,
                stamp_ns: int) -> None:
        if topics and not _topic_matches(logical_topic, topics):
            return
        stamp_ns = int(stamp_ns or time.time_ns())
        try:
            ch_id = table.channel_id(logical_topic, msg_type)
            writer.add_message(
                channel_id=ch_id,
                log_time=stamp_ns,
                publish_time=stamp_ns,
                data=json.dumps(payload).encode("utf-8"),
            )
        except Exception as exc:
            print(f"[recorder] write failed for {logical_topic}: {exc}", file=sys.stderr)
            return
        counts[logical_topic] = counts.get(logical_topic, 0) + 1

    recorder = _Recorder(domain_id, _ingest)

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
            recorder.poll()
            stop_event.wait(timeout=0.02)
            if deadline is not None and time.monotonic() >= deadline:
                break
    finally:
        # Drain anything still queued before closing the file.
        try:
            recorder.poll()
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
    parser = argparse.ArgumentParser(description="Record SpatialDDS traffic to MCAP")
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
