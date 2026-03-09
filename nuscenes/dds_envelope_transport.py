#!/usr/bin/env python3
"""Local DDS envelope transport for nuscenes demo with robust sample filtering."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional, Set, Tuple

from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct, types
from cyclonedds.pub import DataWriter
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic

TOPIC_DDS_ENVELOPE_V1 = "spatialdds/envelope/v1"


def _idl_string(types_module):
    for name in ("string", "str", "String"):
        if hasattr(types_module, name):
            return getattr(types_module, name)
    return str


def _idl_uint64(types_module):
    for name in ("uint64", "uint64_t", "UInt64"):
        if hasattr(types_module, name):
            return getattr(types_module, name)
    return int


string_type = _idl_string(types)
uint64_type = _idl_uint64(types)


@dataclass
class SpatialDDSEnvelope(IdlStruct):
    msg_type: string_type
    logical_topic: string_type
    payload_json: string_type
    stamp_ns: uint64_type
    request_id: string_type


class EnvelopeTransport:
    def __init__(self, on_message_callback: Callable[[object], None], domain_id: int, local_sender_id: Optional[str] = None):
        self._participant = DomainParticipant(domain_id)
        self._topic = Topic(self._participant, TOPIC_DDS_ENVELOPE_V1, SpatialDDSEnvelope)
        self._writer = DataWriter(self._participant, self._topic)
        self._reader = DataReader(self._participant, self._topic)

        self._callback = on_message_callback
        self._domain_id = domain_id
        self._local_sender_id = local_sender_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._sent_fingerprints: Deque[Tuple[str, str, str, str]] = deque(maxlen=512)
        self._sent_fingerprint_set: Set[Tuple[str, str, str, str]] = set()
        self._sent_msg_types: Set[str] = set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def publish(self, logical_topic: str, msg_type: str, payload_json: str, request_id: str = "") -> None:
        envelope = SpatialDDSEnvelope(
            msg_type=msg_type,
            logical_topic=logical_topic,
            payload_json=payload_json,
            stamp_ns=time.time_ns(),
            request_id=request_id or "",
        )
        self._record_sent(envelope)
        print(f"DDS_TX domain={self._domain_id} msg_type={msg_type} logical_topic={logical_topic}")
        self._writer.write(envelope)

    def _poll(self) -> None:
        while not self._stop.is_set():
            samples = self._reader.take()
            if samples:
                for sample in samples:
                    if sample is None:
                        continue
                    if not hasattr(sample, "payload_json"):
                        continue
                    if self._is_self_echo(sample):
                        continue
                    print(f"DDS_RX domain={self._domain_id} msg_type={sample.msg_type} logical_topic={sample.logical_topic}")
                    try:
                        self._callback(sample)
                    except Exception as exc:
                        print(f"DDS_RX callback error: {exc}")
            time.sleep(0.01)

    def _record_sent(self, envelope: object) -> None:
        if envelope.msg_type:
            self._sent_msg_types.add(envelope.msg_type)
        fingerprint = self._fingerprint(envelope.msg_type, envelope.logical_topic, envelope.request_id, envelope.payload_json)
        if fingerprint in self._sent_fingerprint_set:
            return
        if len(self._sent_fingerprints) == self._sent_fingerprints.maxlen:
            oldest = self._sent_fingerprints.popleft()
            self._sent_fingerprint_set.discard(oldest)
        self._sent_fingerprints.append(fingerprint)
        self._sent_fingerprint_set.add(fingerprint)

    def _is_self_echo(self, envelope: object) -> bool:
        if self._local_sender_id:
            sender = _sender_id_from_payload(envelope.payload_json)
            if sender and sender == self._local_sender_id:
                return True
        fingerprint = self._fingerprint(envelope.msg_type, envelope.logical_topic, envelope.request_id, envelope.payload_json)
        if envelope.msg_type in self._sent_msg_types:
            return fingerprint in self._sent_fingerprint_set
        return False

    @staticmethod
    def _fingerprint(msg_type: str, logical_topic: str, request_id: str, payload_json: str):
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        return (msg_type or "", logical_topic or "", request_id or "", payload_hash)


def _sender_id_from_payload(payload_json: str) -> Optional[str]:
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("from", "source_id", "sender_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None
