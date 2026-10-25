#!/usr/bin/env python3
import argparse
import json
import signal
import sys
import threading
import time
from typing import Any, Dict

import spatialdds as sd
from spatialdds.dds import from_json, to_json
from spatialdds.dds.types.spatial.argeo import VpsRequest, VpsResponse
from spatialdds.dds.types.spatial.disco import (
    CoverageResponse as TypedCoverageResponse,
)

from spatialdds_demo.lanes import (
    SCENE, TOPIC_SOURCE_ANNOUNCE_PREVIEW, TOPIC_SOURCE_FALLBACK,
    TOPIC_SOURCE_MANIFEST, TOPIC_SOURCE_REQUEST, TOPIC_SOURCE_SPEC,
    TOPIC_VPS_QUERY, TOPIC_VPS_RESULT, blob_reader,
)
from spatialdds_demo.service_bus import CoverageService, VpsService
from spatialdds_demo.stream import announce_document
from spatialdds_test import (
    SpatialDDSLogger,
    VPSServiceV15,
    _index_manifest_topics,
    _load_manifest,
    _select_topic,
)


def _topic_source_for(manifest_topics: Dict[str, str], role: str, logical_topic: str) -> str:
    if manifest_topics.get(role) == logical_topic:
        return TOPIC_SOURCE_MANIFEST
    if logical_topic in (TOPIC_VPS_QUERY, TOPIC_VPS_RESULT):
        return TOPIC_SOURCE_FALLBACK
    return TOPIC_SOURCE_SPEC


class _BlobInbox:
    """The blob lane, read continuously and reassembled: the last few whole
    blobs, by id. Chunk CRC32s are checked as they arrive."""

    def __init__(self, participant: "sd.dds.Participant", keep: int = 8):
        self._reader = blob_reader(participant)
        self._reassembler = sd.dds.Reassembler()
        self._done: Dict[str, bytes] = {}
        self._keep = keep

    def poll(self) -> None:
        for ev in self._reader.take():
            if not isinstance(ev, sd.Sample):
                continue
            try:
                whole = self._reassembler.feed(ev.data)
            except sd.dds.CorruptChunk as error:
                print(f"blob lane: {error}")
                continue
            if whole is not None:
                self._done[ev.data.blob_id] = whole
                while len(self._done) > self._keep:
                    self._done.pop(next(iter(self._done)))

    def get(self, blob_id: str):
        return self._done.get(blob_id)


def run_server(show_message_content: bool, detailed_content: bool) -> int:
    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content

    service = VPSServiceV15(logger)
    announce = service.create_announce()
    manifest, _ = _load_manifest(announce)
    manifest_topics = _index_manifest_topics(manifest) if manifest else {}

    def serve_coverage(coverage: CoverageService) -> None:
        """Typed CoverageQuery -> CoverageResponse, replying where asked."""
        for query in coverage.take_queries():
            data = to_json(query)
            logger.log_message(
                "COVERAGE_QUERY", "RECV", "Client",
                f"VPS:{service.service_name}", data,
                coverage.topic, TOPIC_SOURCE_SPEC,
                show_message_content,
            )
            response = service.handle_coverage_query(data)
            coverage.reply(query.reply_topic,
                           from_json(TypedCoverageResponse, response))
            logger.log_message(
                "COVERAGE_RESPONSE", "SEND", f"VPS:{service.service_name}",
                "Client", response, query.reply_topic,
                TOPIC_SOURCE_REQUEST, show_message_content,
            )

    def _check_query_image(data: Dict[str, Any]) -> None:
        """
        Verify the imagery a request references actually arrived, and intact.

        `query_blobs` carries a `BlobRef` — id, role and SHA-256 — and the
        bytes ride the blob lane as `BlobChunk`. Until now nothing published
        those bytes and nothing looked for them, so the reference pointed at
        data that had never existed. A responder that never checks is how that
        goes unnoticed.

        Chunk CRC32 is verified during reassembly; the SHA-256 here is the
        end-to-end check that what was reassembled is what the sender
        advertised. The pixels are still ignored — this is a mock — but the
        transfer is now real.
        """
        for ref in data.get("query_blobs") or []:
            blob_id = ref.get("blob_id", "")
            payload = blobs.get(blob_id)
            if payload is None:
                # The blob lane is TRANSIENT_LOCAL, so a late reader still gets
                # the chunks; give them a moment rather than assuming loss.
                deadline = time.time() + 2.0
                while payload is None and time.time() < deadline:
                    blobs.poll()
                    payload = blobs.get(blob_id)
                    if payload is None:
                        time.sleep(0.02)
            if payload is None:
                print(f"query image {blob_id}: NOT RECEIVED "
                      f"(role={ref.get('role')})")
                continue
            actual = sd.dds.blob.checksum(payload)
            ok = actual == ref.get("checksum")
            print(f"query image {blob_id}: {len(payload)} bytes, "
                  f"sha256 {'ok' if ok else 'MISMATCH'} (role={ref.get('role')})")

    def serve_localize(vps: VpsService) -> None:
        """Typed VpsRequest -> VpsResponse on the registered vps topics."""
        for request in vps.take_requests():
            data = to_json(request)
            _check_query_image(data)
            logger.log_message(
                "LOCALIZE_REQUEST", "RECV", "Client", f"VPS:{service.service_name}",
                data, TOPIC_VPS_QUERY,
                _topic_source_for(manifest_topics, "vps_query", TOPIC_VPS_QUERY),
                show_message_content,
            )
            response = service.process_localize_request(data)
            vps.reply(from_json(VpsResponse, response))
            logger.log_message(
                "LOCALIZE_RESPONSE", "SEND", f"VPS:{service.service_name}", "Client",
                response, TOPIC_VPS_RESULT,
                _topic_source_for(manifest_topics, "vps_response", TOPIC_VPS_RESULT),
                show_message_content,
            )

    participant = sd.participant(SCENE, service_id=service.service_id)
    # Reads the blob lane continuously, so query imagery is in hand by the
    # time the request that references it is processed.
    blobs = _BlobInbox(participant)
    # Named, so a request addressed to another VPS on the same well-known topic
    # is left for that service to answer.
    vps = VpsService(participant, service.service_id)
    coverage = CoverageService(participant)
    # The participant's own announce, listing the query lane the VPS reads
    # beside the response lane it writes, as OpenVPS's does (spatialdds 0.7.3,
    # S11). Refreshed by tick() inside its TTL, and departed by close().
    announce_document(participant, announce, vps.endpoints)
    print(f"announce topic: {sd.dds.registry.well_known('Announce').topic}")
    print("announce qos: DISCOVERY_ANNOUNCE "
          "(RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1), keyed on service_id)")
    logger.log_message(
        "ANNOUNCE",
        "SEND",
        f"VPS:{service.service_name}",
        "DDS_NETWORK",
        announce,
        sd.dds.registry.well_known("Announce").topic,
        TOPIC_SOURCE_ANNOUNCE_PREVIEW,
        show_message_content,
    )

    # SIGTERM matters as much as SIGINT here: `docker stop` and most process
    # supervisors send SIGTERM, and a background process started from a
    # non-interactive shell ignores SIGINT entirely. Without this the service
    # would vanish without disposing, and consumers would wait out its TTL.
    stopping = threading.Event()

    def _stop(signum, _frame):
        print(f"signal {signum}: departing {service.service_id}")
        stopping.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # An Announce is a lease, not a birth certificate: `ttl_sec` says how long
    # it stays good, and a consumer honouring it drops the service when that
    # lapses. Publishing once at startup and never again means a service that
    # is running perfectly disappears from every cache after its TTL — which
    # is what happened here, silently, once the demo had been up ten minutes.
    # tick() re-publishes it every ttl/3.
    try:
        while not stopping.is_set():
            participant.tick()
            blobs.poll()
            serve_localize(vps)
            serve_coverage(coverage)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    # Graceful shutdown: dispose the instance (spec MUST) and publish Depart.
    participant.close(reason="the mock VPS stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SpatialDDS DDS Server Demo")
    parser.add_argument("--summary-only", action="store_true", help="Show only headers")
    parser.add_argument("--detailed", action="store_true", help="Show detailed content")
    args = parser.parse_args()

    show_content = not args.summary_only
    detailed = args.detailed and not args.summary_only
    return run_server(show_content, detailed)


if __name__ == "__main__":
    sys.exit(main())
