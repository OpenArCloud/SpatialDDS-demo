"""
Typed SpatialDDS endpoints: one DDS topic per logical topic, spec types, spec QoS.

This replaces the JSON envelope. The mapping is direct:

    envelope field            typed home
    ---------------------     ---------------------------------------------
    logical_topic             the DDS topic name
    msg_type                  the DDS type
    payload_json              the sample itself
    stamp_ns                  each type's own `stamp` field
    request_id                `query_id` on CoverageQuery / CoverageResponse,
                              or the demo struct's own correlation field

Every endpoint in the demo is created here, so the §3.3.3 profile table is
load-bearing rather than decorative: you cannot create a reader or writer
without naming a QoS profile, and an unregistered name raises.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Type

from cyclonedds.core import InstanceState
from cyclonedds.domain import DomainParticipant
from cyclonedds.pub import DataWriter
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic

from spatialdds_demo import qos_profiles

# One Topic object per (participant, name) — CycloneDDS rejects a second Topic
# for the same name, and callers legitimately want a reader and a writer.
_topic_cache: Dict[Tuple[int, str], Topic] = {}
_topic_lock = threading.Lock()


def get_topic(participant: DomainParticipant, topic_name: str, datatype: Type) -> Topic:
    key = (id(participant), topic_name)
    with _topic_lock:
        topic = _topic_cache.get(key)
        if topic is None:
            topic = Topic(participant, topic_name, datatype)
            _topic_cache[key] = topic
        return topic


def make_writer(
    participant: DomainParticipant,
    topic_name: str,
    datatype: Type,
    qos_profile: str,
    *,
    lifespan_sec: Optional[float] = None,
) -> DataWriter:
    """
    A writer on ``topic_name`` carrying ``datatype`` with ``qos_profile``'s QoS.

    ``lifespan_sec`` maps a payload TTL (e.g. ``Announce.ttl_sec``) onto DDS
    Lifespan, so the middleware expires stale samples instead of every consumer
    re-deriving staleness.
    """
    topic = get_topic(participant, topic_name, datatype)
    return DataWriter(
        participant, topic,
        qos=qos_profiles.qos_for(qos_profile, lifespan_sec=lifespan_sec),
    )


def make_reader(
    participant: DomainParticipant,
    topic_name: str,
    datatype: Type,
    qos_profile: str,
    *,
    lifespan_sec: Optional[float] = None,
) -> DataReader:
    """A reader on ``topic_name`` matching ``make_writer``'s QoS."""
    topic = get_topic(participant, topic_name, datatype)
    return DataReader(
        participant, topic,
        qos=qos_profiles.qos_for(qos_profile, lifespan_sec=lifespan_sec),
    )


# --- instance lifecycle -----------------------------------------------------

def dispose(writer: DataWriter, sample: Any) -> None:
    """
    Dispose the sample's instance: "this thing is gone", not "this topic is".

    Only meaningful on a keyed type. For `Announce` (keyed on `service_id`)
    this is the eviction signal a discovery consumer should act on, and it is
    what the demo could not express while everything rode one unkeyed envelope.
    """
    writer.dispose(sample)


@dataclass
class TypedSample:
    """
    One reader sample, with the instance facts a cache needs.

    ``data`` is None when the instance was disposed or lost its writers: DDS
    delivers that as an InvalidSample carrying no key fields, only the handle.
    Callers therefore track ``instance_handle -> key`` from the alive samples
    they have already seen, which is what :class:`AnnounceCache` does.
    """

    data: Optional[Any]
    alive: bool
    instance_handle: Optional[int]

    @property
    def disposed(self) -> bool:
        return not self.alive


def take_samples(reader: DataReader, *, n: int = 100) -> List[Any]:
    """Valid samples only — for callers that do not care about disposal."""
    return [s.data for s in take_with_state(reader, n=n) if s.data is not None]


def take_with_state(reader: DataReader, *, n: int = 100) -> List[TypedSample]:
    """
    Samples plus their instance state, so callers can act on disposal.

    This is the per-instance signal the demo could not express while
    everything rode one unkeyed envelope: "this service is gone", rather than
    "this topic is gone".
    """
    out: List[TypedSample] = []
    for sample in (reader.take(N=n) or []):
        if sample is None:
            continue
        info = getattr(sample, "sample_info", None)
        handle = getattr(info, "instance_handle", None) if info else None
        valid = getattr(info, "valid_data", True) if info else True
        state = getattr(info, "instance_state", InstanceState.Alive) if info else InstanceState.Alive
        alive = bool(valid) and state == InstanceState.Alive
        out.append(TypedSample(
            data=sample if valid else None,
            alive=alive,
            instance_handle=handle,
        ))
    return out
