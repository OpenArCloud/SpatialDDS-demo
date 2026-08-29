#!/usr/bin/env python3
"""Replay an MCAP file recorded by ``recorder.py`` back onto a DDS domain.

Reads messages in log-time order, sleeps to preserve relative spacing
(scaled by ``--speed``), and republishes each one as a typed sample on the
topic it was recorded from, rebuilt from the recorded JSON.

Rebuilding rather than relaying bytes is what makes a replay indistinguishable
from the live system to a consumer: it reads the same types on the same
topics with the same QoS profiles. It also means a corrupt or hand-edited
recording fails here, loudly, instead of being pushed onto the bus.

Announces are replayed too — a consumer that discovers its topics needs the
announce before the data, so the replay leads with it.
"""

from __future__ import annotations

import argparse
import json
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


# The lane each type rides when nothing announced one. Imported rather than
# restated: this file used to carry its own copy, and it drifted from the
# announces it replays.
from spatialdds_demo.topic_types import (  # noqa: E402
    DEFAULT_PROFILE, PROFILE_FOR_TYPE as REPLAY_PROFILES,
)
ANNOUNCE_TYPE = "spatialdds/discovery/announce"


class _ReplayWriters:
    """Typed writers, created per topic on first message for that topic."""

    def __init__(self, domain_id: int):
        from cyclonedds.domain import DomainParticipant

        self._participant = DomainParticipant(domain_id)
        self._writers = {}
        self._skipped = set()
        self._announced_profiles = {}

    def write(self, topic: str, type_name: str, payload: dict) -> bool:
        """True if written. False if this build cannot type the topic."""
        writer = self._ensure(topic, type_name)
        if writer is None:
            return False
        writer.write(payload)
        return True

    def learn_profiles(self, announce: dict) -> None:
        """
        Take each lane's QoS profile from the announce that declares it.

        A recording's channel metadata carries the message type but not the
        profile, so this replayer used to write from a table of its own. That
        table is a second source of truth for something the recording already
        contains — the announces are in the file, and every `TopicMeta` names
        its `qos_profile` — and it drifted: `fused_track` was announced on
        POSE_RT and replayed on DET_RT. Deadline is request/offered QoS, so
        33 ms requested against 100 ms offered simply does not match, and the
        sample vanished with no error on either side.

        Reading the profile from the announce means a replay offers what the
        original publisher offered, whatever the deployment chose.
        """
        for meta in announce.get("topics") or []:
            name, profile = meta.get("name"), meta.get("qos_profile")
            if name and profile:
                self._announced_profiles[name] = profile

    def _profile_for(self, topic: str, type_name: str) -> str:
        if type_name == ANNOUNCE_TYPE:
            return "DISCOVERY_ANNOUNCE"
        announced = self._announced_profiles.get(topic)
        if announced:
            return announced
        # Nothing announced this lane — a partial recording, or data captured
        # before its announce. Fall back to the type's usual profile.
        return REPLAY_PROFILES.get(type_name, DEFAULT_PROFILE)

    def _ensure(self, topic: str, type_name: str):
        from spatialdds_demo import topic_types, typed_transport as tt

        writer = self._writers.get(topic)
        if writer is None:
            if topic in self._skipped:
                return None
            datatype = (topic_types.WELL_KNOWN["spatialdds/discovery/announce/v1"]
                        if type_name == ANNOUNCE_TYPE
                        else topic_types.try_resolve(type_name))
            if datatype is None:
                # A recording may legitimately carry a type this build has
                # never heard of; skip that topic and say so once.
                self._skipped.add(topic)
                print(f"[replayer] skipping {topic}: unknown type "
                      f"{type_name!r}", file=sys.stderr)
                return None
            writer = tt.TypedDictWriter(
                self._participant,
                # Announces go back on the well-known keyed topic; the
                # per-service name in the recording is the bridges' edge
                # convention, not a DDS topic.
                ("spatialdds/discovery/announce/v1"
                 if type_name == ANNOUNCE_TYPE else topic),
                datatype,
                self._profile_for(topic, type_name))
            self._writers[topic] = writer
        return writer

    def prepare(self, channels) -> None:
        """
        Build every writer before the first message goes out.

        DDS endpoint discovery is not instantaneous, and most of the spec's
        profiles are BEST_EFFORT — a writer created at the moment its first
        sample is due loses that sample, and the next few. Creating them all
        up front means discovery happens during the lead-in instead of
        during the replay.
        """
        for topic, type_name in channels:
            try:
                self._ensure(topic, type_name)
            except Exception as exc:
                print(f"[replayer] cannot prepare {topic}: {exc}", file=sys.stderr)


def _identify(schema, channel):
    """``(type_name, topic)`` for one recorded message."""
    type_name = (schema.name if schema is not None else "") or ""
    if channel is not None and channel.metadata:
        type_name = channel.metadata.get("spatialdds_msg_type", type_name)
    return type_name, (channel.topic if channel is not None else "")


def _channels(summary):
    """``[(topic, type_name)]`` for every channel in the file."""
    out = []
    if summary is None:
        return out
    for channel in summary.channels.values():
        schema = summary.schemas.get(channel.schema_id)
        type_name = (channel.metadata or {}).get(
            "spatialdds_msg_type", schema.name if schema else "")
        out.append((channel.topic, type_name))
    return out


def _learn_announced_profiles(reader, writers) -> None:
    """
    Scan the recording's announces for each lane's declared QoS profile.

    Read-only, and separate from replaying them, because writers are all built
    up front and a writer's QoS cannot be changed afterwards.
    """
    for schema, channel, message in reader.iter_messages(log_time_order=True):
        type_name, _ = _identify(schema, channel)
        if type_name != ANNOUNCE_TYPE:
            continue
        try:
            writers.learn_profiles(json.loads(message.data.decode("utf-8")))
        except Exception as exc:
            print(f"[replayer] unreadable announce: {exc}", file=sys.stderr)


def _replay_announces(reader, writers) -> int:
    """Write every recorded announce, before any data. Returns how many."""
    count = 0
    for schema, channel, message in reader.iter_messages(log_time_order=True):
        type_name, topic = _identify(schema, channel)
        if type_name != ANNOUNCE_TYPE:
            continue
        announce = json.loads(message.data.decode("utf-8"))
        # Learn each lane's profile before any data writer is built, so the
        # replay offers what the original publisher offered.
        writers.learn_profiles(announce)
        if writers.write(topic, type_name, announce):
            count += 1
    return count


def replay(
    mcap_path: str,
    domain_id: int = 0,
    speed: float = 1.0,
    loop: bool = False,
    sender_id: Optional[str] = "mcap-replayer",
    lead_in_sec: float = 2.0,
) -> int:
    """Replay `mcap_path` to DDS domain `domain_id`.

    Args:
        mcap_path: path to a .mcap file produced by the recorder.
        domain_id: CycloneDDS domain to publish on.
        speed: playback speed multiplier (2.0 = double speed). 0 or None
            falls through to 1.0; non-positive values are clamped.
        loop: if True, replay indefinitely until SIGINT.
        sender_id: retained for CLI compatibility; a typed replay needs no
            self-echo tag, since each sample goes on its own typed topic.
        lead_in_sec: pause after replaying announces, so a discovery-driven
            consumer has its readers up before the data starts.

    Returns:
        Total number of messages published.
    """
    speed = max(float(speed or 1.0), 0.001)
    writers = _ReplayWriters(domain_id)
    total = 0
    prepared = False

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

                if not prepared:
                    prepared = True
                    # Learn the lanes' QoS profiles before building any
                    # writer, because `prepare` builds them all at once and a
                    # writer's QoS is fixed at creation. The announces are
                    # replayed again below for the consumers' benefit; this
                    # pass only reads them.
                    _learn_announced_profiles(reader, writers)
                    writers.prepare(_channels(summary))
                    # Announces first, then let discovery settle. A consumer
                    # that finds its topics through discovery has no reader
                    # for a lane until it has seen the announce, and by the
                    # time it opens one the first samples on a BEST_EFFORT
                    # lane are already gone. The live system has the same
                    # ordering — services announce, then publish.
                    announced = _replay_announces(reader, writers)
                    total += announced
                    if announced:
                        print(f"[replayer] announced {announced} services; "
                              f"waiting {lead_in_sec:.1f}s for discovery",
                              file=sys.stderr)
                        time.sleep(lead_in_sec)

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

                    msg_type, logical_topic = _identify(schema, channel)
                    if msg_type == ANNOUNCE_TYPE:
                        continue          # already replayed in the lead-in
                    payload = json.loads(message.data.decode("utf-8"))
                    if writers.write(logical_topic, msg_type, payload):
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
        del writers

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
