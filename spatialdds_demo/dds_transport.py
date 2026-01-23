import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from spatialdds_demo.topics import TOPIC_DDS_ENVELOPE_V1


class DDSTransport:
    def __init__(self, on_message_callback: Callable[[object], None], domain_id: int):
        try:
            from cyclonedds.domain import DomainParticipant
            from cyclonedds.topic import Topic
            from cyclonedds.sub import DataReader
            from cyclonedds.pub import DataWriter
            from cyclonedds.idl import IdlStruct, types
        except Exception as exc:
            print(f"Failed to import Cyclone DDS Python bindings: {exc}")
            sys.exit(1)

        string_type = _idl_string(types)
        uint64_type = _idl_uint64(types)

        @dataclass
        class SpatialDDSEnvelope(IdlStruct):
            msg_type: string_type
            logical_topic: string_type
            payload_json: string_type
            stamp_ns: uint64_type
            request_id: string_type

        try:
            self._participant = DomainParticipant(domain_id)
            self._envelope_type = SpatialDDSEnvelope
            self._topic = Topic(self._participant, TOPIC_DDS_ENVELOPE_V1, self._envelope_type)
            self._writer = DataWriter(self._participant, self._topic)
            self._reader = DataReader(self._participant, self._topic)
        except Exception as exc:
            print(f"Failed to initialize Cyclone DDS: {exc}")
            sys.exit(1)
        self._callback = on_message_callback
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def publish(
        self, logical_topic: str, msg_type: str, payload_json: str, request_id: str = ""
    ) -> None:
        envelope = self._envelope_type(
            msg_type=msg_type,
            logical_topic=logical_topic,
            payload_json=payload_json,
            stamp_ns=time.time_ns(),
            request_id=request_id or "",
        )
        print(f"DDS_TX msg_type={msg_type} logical_topic={logical_topic}")
        self._writer.write(envelope)

    def _poll(self) -> None:
        while not self._stop.is_set():
            samples = self._reader.take()
            if samples:
                for sample in samples:
                    if sample is None:
                        continue
                    print(
                        "DDS_RX msg_type="
                        f"{sample.msg_type} logical_topic={sample.logical_topic}"
                    )
                    self._callback(sample)
            time.sleep(0.01)


def require_dds_env() -> int:
    transport = os.getenv("SPATIALDDS_TRANSPORT", "mock")
    if transport != "dds":
        print("SPATIALDDS_TRANSPORT is not set to dds; exiting.")
        sys.exit(1)

    uri = os.getenv("CYCLONEDDS_URI", "")
    if uri != "file:///etc/cyclonedds.xml":
        print("CYCLONEDDS_URI must be set to file:///etc/cyclonedds.xml")
        sys.exit(1)

    domain_str = os.getenv("SPATIALDDS_DDS_DOMAIN", "0")
    try:
        domain_id = int(domain_str)
    except ValueError:
        print(f"Invalid SPATIALDDS_DDS_DOMAIN value: {domain_str}")
        sys.exit(1)

    print("DDS transport enabled: Cyclone DDS")
    print(f"Domain: {domain_id}")
    print(f"CYCLONEDDS_URI: {uri}")
    return domain_id


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
