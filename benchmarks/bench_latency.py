#!/usr/bin/env python3
"""Benchmark 1: round-trip latency across three transports.

Three arms, so the comparison says something:

* **spatialdds_envelope** — the old shape. One struct on one topic with the
  payload as a JSON string inside it. Kept as the baseline; nothing in the
  demo publishes this way any more.
* **spatialdds_typed** — what the demo does now. Real IDL types on their own
  spec-named topics with their §3.3.3 QoS profiles. These are the first
  honest SpatialDDS numbers this repo has produced: every previous
  measurement was of the envelope, which is not what the spec describes.
* **raw_dds** — a minimal hand-written struct with default QoS. The floor:
  what CycloneDDS costs before any SpatialDDS semantics.

The typed arm is expected to sit between the other two and much closer to
raw: it pays CDR serialisation of a real struct instead of JSON encoding
plus a string copy, and it pays whatever the profile's QoS costs.
"""

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


# Both SpatialDDS arms poll a reader rather than blocking on it, and each
# had its own hardcoded interval — 10 ms in the envelope transport, 20 ms in
# the service clients. Left alone, this benchmark would be comparing two
# poll loops rather than two transports. Both are set to the same value here,
# matching the raw arm's, so what is measured is serialisation and delivery.
POLL_INTERVAL = 0.001


class SpatialRoundTrip:
    def __init__(self, domain_id: int) -> None:
        self._responses: "queue.Queue[object]" = queue.Queue()
        self._client = DDSTransport(
            on_message_callback=self._on_client_message,
            domain_id=domain_id,
            local_sender_id="bench-latency-client",
            poll_interval=POLL_INTERVAL,
        )
        self._server = DDSTransport(
            on_message_callback=self._on_server_message,
            domain_id=domain_id,
            local_sender_id="bench-latency-server",
            poll_interval=POLL_INTERVAL,
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


class TypedRoundTrip:
    """
    The demo's actual transport: typed samples, spec topics, profile QoS.

    Uses the VPS request/reply pair, which is the spec's own request/response
    flow — `VPS_REQ` and `VPS_RESP` are registered profiles, and the reply
    correlates on the `request_id` it mirrors rather than on any envelope
    field. The payload rides in `VpsRequest.image_blob_id`, a string field
    the type already has, so the three arms move comparable bytes.
    """

    def __init__(self, domain_id: int) -> None:
        from cyclonedds.domain import DomainParticipant

        from spatialdds_demo.service_bus import VpsClient, VpsService
        from spatialdds_test import SpatialDDSClientV15, SpatialDDSLogger, VPSServiceV15

        self._builder = SpatialDDSClientV15(SpatialDDSLogger())
        # A prebuilt reply. VPSServiceV15.process_localize_request sleeps
        # 50-150 ms to simulate localization work, which would swamp the
        # measurement and make this arm look slow for a reason that has
        # nothing to do with transport. The envelope arm's responder does no
        # such work either, so this keeps the three comparable.
        self._reply_template = VPSServiceV15(
            SpatialDDSLogger()).create_localize_response_template()
        self._stop = threading.Event()
        self._client = VpsClient(DomainParticipant(domain_id))
        self._service = VpsService(DomainParticipant(domain_id))
        self._thread = threading.Thread(target=self._server_loop, daemon=True)
        self._thread.start()
        # Let request/reply discovery settle before the first timed write.
        time.sleep(2.0)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _request(self, req_id: str, payload: str):
        """
        A real `VpsRequest`, built by the demo's own builder.

        The benchmark payload rides in the request's `VisionFrame` blob id —
        which is where a VPS query image actually goes, by reference. Hand
        -rolling the struct here would only be benchmarking a shape nothing
        publishes.
        """
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.oarc_demo import VpsRequest

        request = self._builder.create_localize_request("bench-vps")
        request["request_id"] = req_id
        request["vision_frame"]["hdr"]["blobs"][0]["blob_id"] = payload
        return from_json(VpsRequest, request)

    def run_once(self, payload: str, iteration: int) -> int:
        req_id = f"typed-{iteration}-{uuid.uuid4().hex[:8]}"
        request = self._request(req_id, payload)
        start_ns = time.perf_counter_ns()
        response = self._client.request(request, timeout=5.0,
                                        poll_interval=POLL_INTERVAL)
        if response is None:
            raise TimeoutError("Timeout waiting for typed VpsResponse")
        return time.perf_counter_ns() - start_ns

    def _server_loop(self) -> None:
        from spatialdds_demo.json_mapping import from_json
        from spatialdds_idl.oarc_demo import VpsResponse

        while not self._stop.is_set():
            for request in self._service.take_requests():
                reply = dict(self._reply_template)
                reply["request_id"] = request.request_id
                self._service.reply(from_json(VpsResponse, reply))
            time.sleep(0.001)


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

    arms = [
        ("spatialdds_envelope", SpatialRoundTrip(args.domain)),
        ("spatialdds_typed", TypedRoundTrip(args.domain)),
        ("raw_dds", RawRoundTrip(args.domain)),
    ]

    try:
        for size in payload_sizes:
            payload = payloads[size]
            medians = {}
            for name, arm in arms:
                log(f"[latency] payload={size}B arm={name} warmup={args.warmup}")
                warmup(lambda: arm.run_once(payload, -1), n=args.warmup)

                log(f"[latency] payload={size}B arm={name} "
                    f"iterations={args.iterations}")
                samples: List[int] = []
                for i in range(1, args.iterations + 1):
                    latency_ns = arm.run_once(payload, i)
                    rows.append([name, size, i, latency_ns])
                    samples.append(latency_ns)
                medians[name] = Stats.from_values(samples).median

            log("[latency] summary payload=" + str(size) + "B " + " ".join(
                f"{name}_median_ms={median / 1_000_000:.3f}"
                for name, median in medians.items()))
    finally:
        for _name, arm in arms:
            arm.close()

    write_csv(args.output, ["path", "payload_bytes", "iteration", "latency_ns"], rows)
    log(f"[latency] wrote {len(rows)} rows to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Round-trip latency: envelope vs typed vs raw DDS")
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
