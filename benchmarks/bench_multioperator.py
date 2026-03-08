#!/usr/bin/env python3
"""Benchmark 3: Multi-operator scaling over shared SpatialDDS envelope topic."""

import argparse
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import DDS_DOMAIN_MAIN, Stats, log, parse_csv_ints, write_csv
from spatialdds_demo.topics import TOPIC_DDS_ENVELOPE_V1


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


_DDS_CACHE: Optional[Tuple[object, object, object, object, object, object]] = None
_ENVELOPE_TYPE = None


def _dds_modules():
    global _DDS_CACHE
    if _DDS_CACHE is None:
        from cyclonedds.domain import DomainParticipant
        from cyclonedds.idl import IdlStruct, types
        from cyclonedds.pub import DataWriter
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic

        _DDS_CACHE = (DomainParticipant, IdlStruct, types, DataWriter, DataReader, Topic)
    return _DDS_CACHE


def _envelope_type():
    global _ENVELOPE_TYPE
    if _ENVELOPE_TYPE is None:
        _, IdlStruct, types, _, _, _ = _dds_modules()
        string_type = _idl_string(types)
        uint64_type = _idl_uint64(types)

        @dataclass
        class SpatialDDSEnvelope(IdlStruct):
            msg_type: string_type
            logical_topic: string_type
            payload_json: string_type
            stamp_ns: uint64_type
            request_id: string_type

        _ENVELOPE_TYPE = SpatialDDSEnvelope
    return _ENVELOPE_TYPE


class OperatorPublisher(threading.Thread):
    def __init__(self, domain_id: int, operator_id: str, payload: str, rate_hz: float) -> None:
        super().__init__(daemon=True)
        DomainParticipant, _, _, DataWriter, _, Topic = _dds_modules()
        envelope_type = _envelope_type()
        self._participant = DomainParticipant(domain_id)
        self._topic = Topic(self._participant, TOPIC_DDS_ENVELOPE_V1, envelope_type)
        self._writer = DataWriter(self._participant, self._topic)
        self._operator_id = operator_id
        self._payload = payload
        self._rate_hz = rate_hz
        self._stop_event = threading.Event()
        self._seq = 0
        self._envelope_type = envelope_type

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        period = 1.0 / self._rate_hz
        next_tick = time.perf_counter()
        while not self._stop_event.is_set():
            send_ns = time.perf_counter_ns()
            message = {
                "operator_id": self._operator_id,
                "seq": self._seq,
                "send_ns": send_ns,
                "blob": self._payload,
            }
            self._writer.write(
                self._envelope_type(
                    msg_type="OPERATOR_DATA",
                    logical_topic="spatialdds/operators/data/v1",
                    payload_json=json.dumps(message),
                    stamp_ns=time.time_ns(),
                    request_id="",
                )
            )
            self._seq += 1
            next_tick += period
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.perf_counter()


def benchmark(args: argparse.Namespace) -> None:
    DomainParticipant, _, _, _, DataReader, Topic = _dds_modules()
    envelope_type = _envelope_type()
    operator_counts = parse_csv_ints(args.operators)
    payload = "x" * args.payload_bytes
    rows: List[List[object]] = []

    subscriber_participant = DomainParticipant(args.domain)
    topic = Topic(subscriber_participant, TOPIC_DDS_ENVELOPE_V1, envelope_type)
    reader = DataReader(subscriber_participant, topic)

    for num_operators in operator_counts:
        publishers = [
            OperatorPublisher(args.domain, f"operator-{idx+1}", payload, args.rate_hz)
            for idx in range(num_operators)
        ]

        log(f"[multioperator] start operators={num_operators}")
        for pub in publishers:
            pub.start()

        warmup_deadline = time.perf_counter() + args.warmup_sec
        while time.perf_counter() < warmup_deadline:
            reader.take()
            time.sleep(0.001)

        start = time.perf_counter()
        end = start + args.duration
        latencies: List[int] = []

        while time.perf_counter() < end:
            samples = reader.take()
            if not samples:
                time.sleep(0.001)
                continue
            for sample in samples:
                if not sample or sample.msg_type != "OPERATOR_DATA":
                    continue
                try:
                    payload_obj = json.loads(sample.payload_json)
                    send_ns = int(payload_obj.get("send_ns", 0))
                except Exception:
                    continue
                if send_ns > 0:
                    latencies.append(time.perf_counter_ns() - send_ns)

        for pub in publishers:
            pub.stop()
        for pub in publishers:
            pub.join(timeout=2)

        total_msgs_per_sec = len(latencies) / max(args.duration, 1e-9)
        for i, latency in enumerate(latencies, start=1):
            rows.append([num_operators, i, latency, total_msgs_per_sec])

        stats = Stats.from_values(latencies)
        log(
            "[multioperator] summary "
            f"operators={num_operators} median_ms={stats.median / 1_000_000:.3f} "
            f"throughput_msgs_sec={total_msgs_per_sec:.2f}"
        )

    write_csv(
        args.output,
        ["num_operators", "iteration", "msg_latency_ns", "total_msgs_per_sec"],
        rows,
    )
    log(f"[multioperator] wrote {len(rows)} rows to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark multi-operator scaling")
    parser.add_argument("--operators", default="1,2,5,10,20")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--warmup-sec", type=float, default=3.0)
    parser.add_argument("--payload-bytes", type=int, default=10 * 1024)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--domain", type=int, default=DDS_DOMAIN_MAIN)
    parser.add_argument("--output", default="results/multioperator.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
