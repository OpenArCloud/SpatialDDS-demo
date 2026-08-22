#!/usr/bin/env python3
"""Benchmark 2: Spatial discovery time vs number of announced services."""

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import DDS_DOMAIN_MAIN, Stats, WARMUP_ITERATIONS, log, parse_csv_ints, write_csv
from spatialdds_demo.topics import TOPIC_DISCOVERY_ANNOUNCE_V1
from spatialdds_validation import SpatialDDSValidator, create_coverage_bbox_earth_fixed


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


_DDS_CACHE: Optional[Tuple[object, object, object, object, object, object, object, object]] = None
_ENVELOPE_TYPE = None


def _dds_modules():
    global _DDS_CACHE
    if _DDS_CACHE is None:
        from cyclonedds import qos, util
        from cyclonedds.domain import DomainParticipant
        from cyclonedds.idl import IdlStruct, types
        from cyclonedds.pub import DataWriter
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic

        _DDS_CACHE = (qos, util, DomainParticipant, IdlStruct, types, DataWriter, DataReader, Topic)
    return _DDS_CACHE


def _envelope_type():
    global _ENVELOPE_TYPE
    if _ENVELOPE_TYPE is None:
        _, _, _, IdlStruct, types, _, _, _ = _dds_modules()
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


def _announce_qos(ttl_sec: int):
    qos, util, _, _, _, _, _, _ = _dds_modules()
    ttl = max(1, int(ttl_sec))
    return qos.Qos(
        qos.Policy.Durability.TransientLocal,
        qos.Policy.Reliability.Reliable(util.duration(seconds=1)),
        qos.Policy.History.KeepLast(1),
        qos.Policy.Lifespan(util.duration(seconds=ttl)),
    )


class ServiceAnnouncer:
    def __init__(self, domain_id: int, service_index: int, ttl_sec: int = 300) -> None:
        _, _, DomainParticipant, _, _, DataWriter, _, Topic = _dds_modules()
        envelope_type = _envelope_type()
        self._participant = DomainParticipant(domain_id)
        self._topic = Topic(self._participant, TOPIC_DISCOVERY_ANNOUNCE_V1, envelope_type)
        self._writer = DataWriter(self._participant, self._topic, qos=_announce_qos(ttl_sec))
        self._message = self._build_announce(service_index)
        self._envelope_type = envelope_type

    def publish_all(self) -> None:
        stamp_ns = time.time_ns()
        self._writer.write(
            self._envelope_type(
                msg_type="ANNOUNCE",
                logical_topic=TOPIC_DISCOVERY_ANNOUNCE_V1,
                payload_json=self._message,
                stamp_ns=stamp_ns,
                request_id="",
            )
        )

    @staticmethod
    def _build_announce(index: int) -> str:
        west = -97.80 + (index % 20) * 0.002
        south = 30.20 + (index // 20) * 0.002
        east = west + 0.0015
        north = south + 0.0015
        coverage_frame_ref, coverage = create_coverage_bbox_earth_fixed(west, south, east, north)
        announce = {
            "service_id": f"svc:vps:bench/{index}",
            "name": f"BenchService-{index}",
            "kind": "VPS",
            "manifest_uri": f"spatialdds://bench.local/zone:austin-{index}/manifest:vps",
            "coverage": [coverage],
            "coverage_frame_ref": coverage_frame_ref,
            # 1.7 registered type / QoS names (spec 3.3.2, 3.3.3).
            "topics": [
                {"name": "spatialdds/vps/query/v1", "type": "vps_query",
                 "version": "v1", "qos_profile": "VPS_REQ"},
                {"name": "spatialdds/vps/result/v1", "type": "geopose",
                 "version": "v1", "qos_profile": "VPS_RESP"},
            ],
            "caps": {
                "supported_profiles": [
                    {"name": "spatial.core", "major": 1, "min_minor": 7, "max_minor": 7},
                    {"name": "spatial.discovery", "major": 1, "min_minor": 7, "max_minor": 7},
                ],
                "preferred_profiles": ["spatial.discovery/1.7", "spatial.core/1.7"],
                "features": [],
            },
            "ttl_sec": 300,
            "stamp": SpatialDDSValidator.now_time(),
        }
        return json.dumps(announce)


def _measure_first_announce(domain_id: int, timeout_sec: float = 5.0) -> int:
    _, _, DomainParticipant, _, _, _, DataReader, Topic = _dds_modules()
    envelope_type = _envelope_type()
    start_ns = time.perf_counter_ns()
    participant = DomainParticipant(domain_id)
    topic = Topic(participant, TOPIC_DISCOVERY_ANNOUNCE_V1, envelope_type)
    reader = DataReader(participant, topic, qos=_announce_qos(300))

    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        samples = reader.take()
        if samples:
            for sample in samples:
                if sample and sample.msg_type == "ANNOUNCE":
                    return time.perf_counter_ns() - start_ns
        time.sleep(0.001)
    raise TimeoutError("Timed out waiting for ANNOUNCE")


def benchmark(args: argparse.Namespace) -> None:
    num_services_list = parse_csv_ints(args.services)
    rows: List[List[object]] = []

    for num_services in num_services_list:
        log(f"[discovery] preparing {num_services} services")
        announcers = [ServiceAnnouncer(args.domain, idx) for idx in range(num_services)]
        for announcer in announcers:
            announcer.publish_all()

        warmups = min(args.warmup, max(1, args.iterations // 2))
        log(f"[discovery] warmup services={num_services} iterations={warmups}")
        for _ in range(warmups):
            _measure_first_announce(args.domain)

        samples: List[int] = []
        log(f"[discovery] running services={num_services} iterations={args.iterations}")
        for i in range(1, args.iterations + 1):
            elapsed_ns = _measure_first_announce(args.domain)
            rows.append([num_services, i, elapsed_ns])
            samples.append(elapsed_ns)

        stats = Stats.from_values(samples)
        log(
            "[discovery] summary "
            f"services={num_services} median_ms={stats.median / 1_000_000:.3f} "
            f"p95_ms={stats.p95 / 1_000_000:.3f}"
        )

    write_csv(args.output, ["num_services", "iteration", "discovery_time_ns"], rows)
    log(f"[discovery] wrote {len(rows)} rows to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark discovery ANNOUNCE latency")
    parser.add_argument("--services", default="1,5,10,25,50,100")
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=WARMUP_ITERATIONS)
    parser.add_argument("--domain", type=int, default=DDS_DOMAIN_MAIN)
    parser.add_argument("--output", default="results/discovery.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
