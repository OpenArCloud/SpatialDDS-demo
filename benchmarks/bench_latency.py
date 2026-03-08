#!/usr/bin/env python3
"""Benchmark 1: SpatialDDS envelope overhead vs raw DDS round-trip latency."""

import argparse
import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from spatialdds_demo.dds_transport import DDSTransport
from spatialdds_demo.topics import TOPIC_VPS_QUERY_V1, TOPIC_VPS_RESULT_V1

from common import (
    DDS_DOMAIN_MAIN,
    DEFAULT_ITERATIONS,
    Stats,
    WARMUP_ITERATIONS,
    log,
    parse_csv_ints,
    warmup,
    write_csv,
)


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


class SpatialRoundTrip:
    def __init__(self, domain_id: int) -> None:
        self._responses: "queue.Queue[object]" = queue.Queue()
        self._client = DDSTransport(
            on_message_callback=self._on_client_message,
            domain_id=domain_id,
            local_sender_id="bench-latency-client",
        )
        self._server = DDSTransport(
            on_message_callback=self._on_server_message,
            domain_id=domain_id,
            local_sender_id="bench-latency-server",
        )
        self._client.start()
        self._server.start()

    def close(self) -> None:
        self._client.stop()
        self._server.stop()

    def run_once(self, payload: str, iteration: int) -> int:
        req_id = f"lat-{iteration}-{uuid.uuid4().hex[:8]}"
        request = {
            "request_id": req_id,
            "operator_id": "bench-operator",
            "frame_id": iteration,
            "sequence": iteration,
            "blob": payload,
            "from": "bench-latency-client",
        }
        start_ns = time.perf_counter_ns()
        self._client.publish(TOPIC_VPS_QUERY_V1, "LOCALIZE_REQUEST", json.dumps(request), req_id)
        deadline_ns = start_ns + 5_000_000_000
        while time.perf_counter_ns() < deadline_ns:
            try:
                envelope = self._responses.get(timeout=0.1)
            except queue.Empty:
                continue
            if envelope.request_id == req_id:
                return time.perf_counter_ns() - start_ns
        raise TimeoutError("Timeout waiting for LOCALIZE_RESPONSE")

    def _on_server_message(self, envelope: object) -> None:
        if envelope.msg_type != "LOCALIZE_REQUEST":
            return
        payload = json.loads(envelope.payload_json)
        response = {
            "request_id": payload.get("request_id", ""),
            "operator_id": payload.get("operator_id", ""),
            "sequence": payload.get("sequence", 0),
            "blob": payload.get("blob", ""),
            "from": "bench-latency-server",
            "status": "OK",
        }
        self._server.publish(
            TOPIC_VPS_RESULT_V1,
            "LOCALIZE_RESPONSE",
            json.dumps(response),
            envelope.request_id,
        )

    def _on_client_message(self, envelope: object) -> None:
        if envelope.msg_type == "LOCALIZE_RESPONSE":
            self._responses.put(envelope)


class RawRoundTrip:
    def __init__(self, domain_id: int) -> None:
        from cyclonedds.domain import DomainParticipant
        from cyclonedds.idl import IdlStruct, types
        from cyclonedds.pub import DataWriter
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic

        string_type = _idl_string(types)
        uint64_type = _idl_uint64(types)

        @dataclass
        class RawRequest(IdlStruct):
            request_id: string_type
            payload: string_type
            sent_ns: uint64_type

        @dataclass
        class RawResponse(IdlStruct):
            request_id: string_type
            payload: string_type

        self._stop = threading.Event()
        self._req_participant = DomainParticipant(domain_id)
        self._resp_participant = DomainParticipant(domain_id)
        self._client_participant = DomainParticipant(domain_id)

        self._request_topic_server = Topic(
            self._req_participant, "benchmarks/raw/request/v1", RawRequest
        )
        self._response_topic_server = Topic(
            self._resp_participant, "benchmarks/raw/response/v1", RawResponse
        )
        self._request_topic_client = Topic(
            self._client_participant, "benchmarks/raw/request/v1", RawRequest
        )
        self._response_topic_client = Topic(
            self._client_participant, "benchmarks/raw/response/v1", RawResponse
        )

        self._server_reader = DataReader(self._req_participant, self._request_topic_server)
        self._server_writer = DataWriter(self._resp_participant, self._response_topic_server)

        self._client_writer = DataWriter(self._client_participant, self._request_topic_client)
        self._client_reader = DataReader(self._client_participant, self._response_topic_client)

        self._request_type = RawRequest
        self._response_type = RawResponse

        self._thread = threading.Thread(target=self._server_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def run_once(self, payload: str, iteration: int) -> int:
        req_id = f"raw-{iteration}-{uuid.uuid4().hex[:8]}"
        start_ns = time.perf_counter_ns()
        self._client_writer.write(
            self._request_type(request_id=req_id, payload=payload, sent_ns=start_ns)
        )
        deadline_ns = start_ns + 5_000_000_000
        while time.perf_counter_ns() < deadline_ns:
            samples = self._client_reader.take()
            if not samples:
                time.sleep(0.001)
                continue
            for sample in samples:
                if sample and sample.request_id == req_id:
                    return time.perf_counter_ns() - start_ns
        raise TimeoutError("Timeout waiting for raw DDS response")

    def _server_loop(self) -> None:
        while not self._stop.is_set():
            samples = self._server_reader.take()
            if samples:
                for sample in samples:
                    if not sample:
                        continue
                    self._server_writer.write(
                        self._response_type(request_id=sample.request_id, payload=sample.payload)
                    )
            time.sleep(0.001)


def benchmark(args: argparse.Namespace) -> None:
    payload_sizes = parse_csv_ints(args.payload_sizes)
    payloads: Dict[int, str] = {size: ("x" * size) for size in payload_sizes}
    rows: List[List[object]] = []

    spatial = SpatialRoundTrip(args.domain)
    raw = RawRoundTrip(args.domain)

    try:
        for size in payload_sizes:
            payload = payloads[size]
            log(f"[latency] payload={size}B warmup spatial={args.warmup} raw={args.warmup}")
            warmup(lambda: spatial.run_once(payload, -1), n=args.warmup)
            warmup(lambda: raw.run_once(payload, -1), n=args.warmup)

            spatial_samples: List[int] = []
            raw_samples: List[int] = []

            log(f"[latency] payload={size}B running spatial iterations={args.iterations}")
            for i in range(1, args.iterations + 1):
                latency_ns = spatial.run_once(payload, i)
                rows.append(["spatialdds_envelope", size, i, latency_ns])
                spatial_samples.append(latency_ns)

            log(f"[latency] payload={size}B running raw iterations={args.iterations}")
            for i in range(1, args.iterations + 1):
                latency_ns = raw.run_once(payload, i)
                rows.append(["raw_dds", size, i, latency_ns])
                raw_samples.append(latency_ns)

            spatial_stats = Stats.from_values(spatial_samples)
            raw_stats = Stats.from_values(raw_samples)
            log(
                "[latency] summary "
                f"payload={size}B spatial_median_ms={spatial_stats.median / 1_000_000:.3f} "
                f"raw_median_ms={raw_stats.median / 1_000_000:.3f}"
            )
    finally:
        spatial.close()
        raw.close()

    write_csv(args.output, ["path", "payload_bytes", "iteration", "latency_ns"], rows)
    log(f"[latency] wrote {len(rows)} rows to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark SpatialDDS envelope latency overhead")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmup", type=int, default=WARMUP_ITERATIONS)
    parser.add_argument("--payload-sizes", default="1024,10240,102400,512000")
    parser.add_argument("--domain", type=int, default=DDS_DOMAIN_MAIN)
    parser.add_argument("--output", default="results/latency.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
