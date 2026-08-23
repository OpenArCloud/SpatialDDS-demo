"""
Typed request/reply for the demo's service flows: VPS localization and catalogue.

Both are request/reply over DDS, correlated by a key the request carries
(`request_id`, `query_id`) rather than by the envelope's old `request_id`
string. Server, client and web bridge all go through here, so the three cannot
drift in topic name, QoS profile or correlation rule.

Why these payloads are demo-owned:

* **VPS request** — 3.3.2 registers the topic type `vps_query` but the IDL
  defines no struct for it. The *response pose* is a spec type
  (`argeo::NodeGeo`); only the request, the correlation and the quality
  reporting around it are demo-shaped.
* **Catalogue** — 1.7 has `ContentAnnounce` for advertising content but no
  query/response pair for asking a catalogue what is in an area.

Both are catalogued as spec gaps in ar_demo/SPEC_COMPLIANCE.md.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.topics import (
    TOPIC_CATALOG_QUERY_V1,
    TOPIC_VPS_QUERY_V1,
    TOPIC_VPS_RESULT_V1,
)
from spatialdds_idl.oarc_demo import (
    CatalogQuery,
    CatalogResponse,
    VpsRequest,
    VpsResponse,
)

# Registered QoS profiles for these lanes (3.3.3).
VPS_REQ_PROFILE = "VPS_REQ"
VPS_RESP_PROFILE = "VPS_RESP"
# The catalogue is a demo protocol; it borrows the VPS request/reply profiles,
# which are the reliable ordered lanes its semantics call for.
CATALOG_REQ_PROFILE = "VPS_REQ"
CATALOG_RESP_PROFILE = "VPS_RESP"


class VpsService:
    """Server side of the localization exchange."""

    def __init__(self, participant: DomainParticipant):
        self._reader = tt.make_reader(
            participant, TOPIC_VPS_QUERY_V1, VpsRequest, VPS_REQ_PROFILE)
        self._writer = tt.make_writer(
            participant, TOPIC_VPS_RESULT_V1, VpsResponse, VPS_RESP_PROFILE)

    def take_requests(self) -> List[VpsRequest]:
        return tt.take_samples(self._reader)

    def reply(self, response: VpsResponse) -> None:
        self._writer.write(response)


class VpsClient:
    """Client side: publish a request, wait for the reply that matches it."""

    def __init__(self, participant: DomainParticipant):
        self._writer = tt.make_writer(
            participant, TOPIC_VPS_QUERY_V1, VpsRequest, VPS_REQ_PROFILE)
        self._reader = tt.make_reader(
            participant, TOPIC_VPS_RESULT_V1, VpsResponse, VPS_RESP_PROFILE)

    def request(self, request: VpsRequest, timeout: float = 10.0) -> Optional[VpsResponse]:
        self._writer.write(request)
        return self.await_reply(request.request_id, timeout=timeout)

    def await_reply(self, request_id: str, timeout: float = 10.0) -> Optional[VpsResponse]:
        """
        Correlation is the request_id the response mirrors — no envelope needed.

        Replies for other requests are ignored rather than consumed-and-dropped
        where possible; at demo concurrency a single in-flight request is the
        normal case.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            for response in tt.take_samples(self._reader):
                if response.request_id == request_id:
                    return response
            time.sleep(0.02)
        return None


class CatalogService:
    """Server side of the catalogue exchange. Replies on the query's reply_topic."""

    def __init__(self, participant: DomainParticipant):
        self._participant = participant
        self._reader = tt.make_reader(
            participant, TOPIC_CATALOG_QUERY_V1, CatalogQuery, CATALOG_REQ_PROFILE)
        self._reply_writers: Dict[str, object] = {}

    def take_queries(self) -> List[CatalogQuery]:
        return tt.take_samples(self._reader)

    def reply(self, reply_topic: str, response: CatalogResponse) -> None:
        writer = self._reply_writers.get(reply_topic)
        if writer is None:
            writer = tt.make_writer(
                self._participant, reply_topic, CatalogResponse, CATALOG_RESP_PROFILE)
            self._reply_writers[reply_topic] = writer
        writer.write(response)


class CatalogClient:
    """Client side: ask on the well-known topic, listen on a private reply topic."""

    def __init__(self, participant: DomainParticipant, reply_topic: str):
        self._reply_topic = reply_topic
        self._writer = tt.make_writer(
            participant, TOPIC_CATALOG_QUERY_V1, CatalogQuery, CATALOG_REQ_PROFILE)
        self._reader = tt.make_reader(
            participant, reply_topic, CatalogResponse, CATALOG_RESP_PROFILE)

    @property
    def reply_topic(self) -> str:
        return self._reply_topic

    def query(self, query: CatalogQuery, timeout: float = 6.0) -> Optional[CatalogResponse]:
        self._writer.write(query)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for response in tt.take_samples(self._reader):
                if response.query_id == query.query_id:
                    return response
            time.sleep(0.02)
        return None
