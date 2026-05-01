"""Shared envelope publish/subscribe helpers for every SpatialDDS bridge.

The MCAP, ROS 2, and web bridges all need a CycloneDDS endpoint on
``spatialdds/envelope/v1`` with QoS that doesn't drop bursts on the floor.
The repo's default ``EnvelopeTransport`` uses BEST_EFFORT + KEEP_LAST(1),
which is fine for live consumers at sensor rates but loses messages when a
publisher emits multiple writes per microsecond (DeepSense does this — see
the MCAP bridge's commit history). For *recording*/*replaying*/*relaying*
we want RELIABLE + KEEP_ALL.

DDS imports are deferred until first use so importing this module on a
host without ``cyclonedds`` installed (Tier-1 unit-test env) doesn't fail.
``EnvelopePublisher`` / ``EnvelopeSubscriber`` are self-contained — no
dependencies on the MCAP bridge's internals.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


def _import_envelope_idl():
    """Import the envelope IDL struct + topic name from the existing demo
    transport. Deferred so this module imports cleanly without DDS."""
    # Walk up to the repo root so ``nuscenes.dds_envelope_transport`` is reachable.
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from nuscenes.dds_envelope_transport import (  # type: ignore
        SpatialDDSEnvelope,
        TOPIC_DDS_ENVELOPE_V1,
    )
    return SpatialDDSEnvelope, TOPIC_DDS_ENVELOPE_V1


def _make_reader(domain_id: int):
    """Build a CycloneDDS reader on the envelope topic with RELIABLE +
    KEEP_ALL QoS so a burst of writes within one poll interval isn't
    collapsed to just the most recent sample."""
    from cyclonedds.core import Policy, Qos
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.sub import DataReader, Subscriber
    from cyclonedds.topic import Topic

    SpatialDDSEnvelope, TOPIC = _import_envelope_idl()
    qos = Qos(
        Policy.Reliability.Reliable(0),
        Policy.History.KeepAll,
        Policy.Durability.Volatile,
    )
    participant = DomainParticipant(domain_id)
    topic = Topic(participant, TOPIC, SpatialDDSEnvelope)
    subscriber = Subscriber(participant)
    reader = DataReader(subscriber, topic, qos=qos)
    # Hold strong refs so the participant/topic don't GC out from under us.
    reader._participant = participant
    reader._topic = topic
    reader._subscriber = subscriber
    return reader


def _make_writer(domain_id: int):
    """Mirror of ``_make_reader`` for the publish path."""
    from cyclonedds.core import Policy, Qos
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.pub import DataWriter, Publisher
    from cyclonedds.topic import Topic

    SpatialDDSEnvelope, TOPIC = _import_envelope_idl()
    qos = Qos(
        Policy.Reliability.Reliable(0),
        Policy.History.KeepAll,
        Policy.Durability.Volatile,
    )
    participant = DomainParticipant(domain_id)
    topic = Topic(participant, TOPIC, SpatialDDSEnvelope)
    publisher = Publisher(participant)
    writer = DataWriter(publisher, topic, qos=qos)
    writer._participant = participant
    writer._topic = topic
    writer._publisher = publisher
    return writer, SpatialDDSEnvelope


# ---------- Publisher --------------------------------------------------------

class EnvelopePublisher:
    """Lossless DDS writer that ships SpatialDDS envelope dicts.

    Holds one CycloneDDS participant + writer over the lifetime of the
    bridge node. Thread-safe writes (the underlying CycloneDDS writer is).
    """

    def __init__(self, domain_id: int):
        writer_pair = _make_writer(domain_id)
        self._writer, self._EnvelopeCls = writer_pair

    def publish(self, logical_topic: str, msg_type: str,
                payload: Dict[str, Any], request_id: str = "",
                stamp_ns: Optional[int] = None) -> None:
        envelope = self._EnvelopeCls(
            msg_type=msg_type,
            logical_topic=logical_topic,
            payload_json=json.dumps(payload),
            stamp_ns=int(stamp_ns) if stamp_ns is not None else int(time.time_ns()),
            request_id=request_id or "",
        )
        self._writer.write(envelope)

    def close(self) -> None:
        # Allow RELIABLE peers a moment to ack outstanding samples.
        time.sleep(0.2)
        try:
            del self._writer
        except Exception:
            pass


# ---------- Subscriber -------------------------------------------------------

EnvelopeCallback = Callable[[str, str, Dict[str, Any], int], None]
"""Callback signature: ``(msg_type, logical_topic, payload_dict, stamp_ns) -> None``."""


class EnvelopeSubscriber:
    """Lossless DDS reader that hands decoded envelope payloads to a callback.

    Runs a daemon polling thread (matching ``EnvelopeTransport``'s pattern
    but with RELIABLE+KEEP_ALL QoS so bursts don't get collapsed).
    """

    def __init__(self, domain_id: int, callback: EnvelopeCallback,
                 poll_interval_s: float = 0.05):
        self._reader = _make_reader(domain_id)
        self._callback = callback
        self._poll_interval = float(poll_interval_s)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _poll(self) -> None:
        while not self._stop.is_set():
            samples = self._reader.take(N=512)
            if samples:
                for sample in samples:
                    if sample is None or not hasattr(sample, "payload_json"):
                        continue
                    try:
                        payload = json.loads(getattr(sample, "payload_json", "") or "{}")
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    msg_type = getattr(sample, "msg_type", "") or ""
                    topic = getattr(sample, "logical_topic", "") or ""
                    stamp_ns = int(getattr(sample, "stamp_ns", 0) or 0)
                    try:
                        self._callback(msg_type, topic, payload, stamp_ns)
                    except Exception as exc:
                        print(f"[envelope] callback error on {topic}: {exc}",
                              file=sys.stderr)
            else:
                self._stop.wait(timeout=self._poll_interval)
