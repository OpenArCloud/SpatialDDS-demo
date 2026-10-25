"""
Typed request/reply for the demo's service flows: VPS localization, bootstrap
and discovery coverage.

Each is request/reply over DDS, correlated by the `query_id` the request
carries and the response mirrors, rather than by the envelope's old
`request_id` string. Server, client and web bridge all go through here, so the
three cannot drift in topic name, QoS profile or correlation rule.

Type ownership:

* **VPS** — now fully spec types. `argeo::VpsRequest` / `VpsResponse`
  (registered as `vps_query` / `vps_response`, §3.3.2) carry the query imagery
  by `BlobRef` reference and the result in `argeo::NodeGeo`. The `oarc_demo`
  VPS copies are retired.
* **Catalogue** — no longer request/reply, and no longer here. It moved onto
  1.8's own shape: a latched `ContentAnnounce` per item
  (`discovery_bus.ContentAnnouncePublisher` / `ContentInventory`) naming a
  content manifest behind the resolver. Its demo-owned query pair retired;
  what 1.8 has no field for is in docs/final-migration-census.md §1.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

import spatialdds as sd

from spatialdds_demo.lanes import (
    COVERAGE_REPLY, TOPIC_BOOTSTRAP_QUERY, TOPIC_BOOTSTRAP_RESPONSE,
    TOPIC_VPS_QUERY, TOPIC_VPS_RESULT,
)
from spatialdds_demo_idl18.oarc_demo import (
    BootstrapQuery,
    BootstrapResponse,
)
from spatialdds.dds.types.spatial.argeo import VpsRequest, VpsResponse
from spatialdds.dds.types.spatial.disco import CoverageQuery, CoverageResponse

# How often a waiting client re-checks its reply reader. 20 ms is a fine
# default for a demo at demo rates, and it is also a hard latency floor: a
# reply that arrives in 2 ms is still not seen for up to 20. Callers that
# care — the latency benchmark, chiefly — pass something tighter, which is
# why it is a parameter rather than a constant in four loops.
DEFAULT_POLL_INTERVAL = 0.02

# The VPS lanes' profiles are their §3.3.2 rows' own, VPS_REQ and VPS_RESP,
# which the SDK applies by default. Their names are spatialdds/vps/*, which the
# spec gives only by example (spatialdds-py candidates 9.5), so they are opened
# with `topic=`.


def _samples(reader: "sd.dds.Reader") -> list:
    return [ev.data for ev in reader.take() if isinstance(ev, sd.Sample)]


def service_id_base(service_id: str) -> str:
    """
    A service id without its revision suffix.

    `VpsRequest.service_id` names a service, optionally pinning a revision as
    `<id>;v=<revision>` — OpenVPS uses it for the map a request wants. Routing
    is by the part before the suffix; what the revision means is the service's
    business.
    """
    return (service_id or "").split(";", 1)[0]


class VpsService:
    """Server side of the localization exchange."""

    def __init__(self, participant: "sd.dds.Participant", service_id: str = ""):
        self._reader = participant.reader("vps_query", topic=TOPIC_VPS_QUERY)
        self._writer = participant.writer("vps_response",
                                          topic=TOPIC_VPS_RESULT)
        self._service_id = service_id_base(service_id)

    def addressed_to_us(self, service_id: str) -> bool:
        """
        Whether a request naming ``service_id`` is this service's to answer.

        An empty name means "whoever is listening", which is what a client that
        has not discovered anything sends. A name that is not ours belongs to
        someone else on the same well-known topic.

        Without this a responder answers every request on the lane, including
        ones addressed elsewhere, and because the client correlates on
        ``query_id`` alone the first reply wins. Two VPS services on one bus
        then make discovery pointless: a request addressed to the VPS that
        holds the map is answered by whichever is nearer, with a pose from a
        different map and nothing in the response saying so. Measured on AWS,
        a co-located stand-in beat a real localizer to every request in 0.4 s.
        A service constructed without an id keeps the old answer-everything
        behaviour, since single-service deployments have nothing to confuse.
        """
        if not self._service_id or not service_id:
            return True
        return service_id_base(service_id) == self._service_id

    def take_requests(self) -> List[VpsRequest]:
        return [request for request in _samples(self._reader)
                if self.addressed_to_us(getattr(request, "service_id", ""))]

    def reply(self, response: VpsResponse) -> None:
        self._writer.write(response)


class VpsClient:
    """Client side: publish a request, wait for the reply that matches it."""

    def __init__(self, participant: "sd.dds.Participant"):
        self._writer = participant.writer("vps_query", topic=TOPIC_VPS_QUERY)
        self._reader = participant.reader("vps_response",
                                          topic=TOPIC_VPS_RESULT)

    def request(self, request: VpsRequest, timeout: float = 10.0,
                poll_interval: float = DEFAULT_POLL_INTERVAL
                ) -> Optional[VpsResponse]:
        self._writer.write(request)
        return self.await_reply(request.query_id, timeout=timeout,
                                poll_interval=poll_interval,
                                expect_service_id=getattr(request, "service_id", ""))

    def await_reply(self, query_id: str, timeout: float = 10.0,
                    poll_interval: float = DEFAULT_POLL_INTERVAL,
                    expect_service_id: str = "") -> Optional[VpsResponse]:
        """
        Correlation is the query_id the response mirrors — no envelope needed.

        When the request named a service, the responder has to be that service:
        a reply carrying someone else's ``service_id`` answers a question this
        client did not ask. Both ends need the check — a well-behaved responder
        stays quiet, but one that answers everything would otherwise win the
        race on latency alone, and the pose it returns is from another map.
        Revision suffixes are ignored on both sides, so a service that pins the
        map it answered still matches the request that asked for it.

        Replies for other requests are ignored rather than consumed-and-dropped
        where possible; at demo concurrency a single in-flight request is the
        normal case.
        """
        expected = service_id_base(expect_service_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for response in _samples(self._reader):
                if response.query_id != query_id:
                    continue
                if expected and service_id_base(
                        getattr(response, "service_id", "")) != expected:
                    continue
                return response
            time.sleep(poll_interval)
        return None


# --- Coverage query: "who covers this area?" --------------------------------
# A spec flow, unlike VPS: `CoverageQuery` and `CoverageResponse`
# are disco types, and C.5 puts the query on a well-known topic with the reply
# going to a topic the query names. Results are compact `ServiceSummary` rows —
# a consumer selects on the summary and takes detail from the retained
# Announce it already holds, or by resolving `manifest_uri`.
# The query is the spec's well-known topic, opened by its message name, so its
# QoS is the spec's (RELIABLE + VOLATILE + KEEP_ALL). Replies go to the lane
# the asker names, on lanes.COVERAGE_REPLY. Both were ZONE_META,
# TRANSIENT_LOCAL, until the SDK adoption, which latched queries for services
# that joined later.


class CoverageService:
    """Server side: read queries on the well-known topic, reply where asked."""

    def __init__(self, participant: "sd.dds.Participant"):
        self._participant = participant
        self._reader = participant.reader("CoverageQuery")
        self._reply_writers: Dict[str, object] = {}

    @property
    def topic(self) -> str:
        """The well-known query topic this service reads."""
        return self._reader.topic

    def take_queries(self) -> List[CoverageQuery]:
        return _samples(self._reader)

    def reply(self, reply_topic: str, response: CoverageResponse) -> bool:
        """Answer on the lane the asker named. False if it cannot be named.

        The name is the asker's, so it is held to §3.3.1 when the writer is
        opened: a malformed one is refused there, with the reason, rather
        than becoming a topic nobody else would recognise. One asker's bad
        name is that asker's problem, so it is reported and the service keeps
        serving everyone else.
        """
        writer = self._reply_writers.get(reply_topic)
        if writer is None:
            try:
                writer = self._participant.writer(
                    CoverageResponse, type_name="coverage_response",
                    topic=reply_topic, qos_profile=COVERAGE_REPLY)
            except sd.dds.ParticipantRefused as refused:
                print(f"coverage: not replying to {reply_topic!r}: {refused}")
                return False
            self._reply_writers[reply_topic] = writer
        writer.write(response)
        return True


class CoverageClient:
    """Client side: ask on the well-known topic, listen on a private reply topic."""

    def __init__(self, participant: "sd.dds.Participant", reply_topic: str):
        self._reply_topic = reply_topic
        self._writer = participant.writer("CoverageQuery")
        # KEEP_ALL, not the profile's KEEP_LAST(1): every service that covers
        # the queried area answers this one topic, and `CoverageResponse` has
        # no `@key`, so all of their replies share a single instance. At depth
        # 1 the last writer wins and the querier concludes one service exists
        # where several do — with nothing to indicate loss.
        self._reader = participant.reader(
            CoverageResponse, type_name="coverage_response", topic=reply_topic,
            qos_profile=COVERAGE_REPLY, history="KEEP_ALL")

    @property
    def reply_topic(self) -> str:
        return self._reply_topic

    def query(self, query: CoverageQuery, timeout: float = 10.0,
              poll_interval: float = DEFAULT_POLL_INTERVAL
              ) -> Optional[CoverageResponse]:
        self._writer.write(query)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for response in _samples(self._reader):
                if response.query_id == query.query_id:
                    return response
            time.sleep(poll_interval)
        return None

    def gather(self, query: CoverageQuery, window: float = 1.5,
               poll_interval: float = DEFAULT_POLL_INTERVAL
               ) -> List[CoverageResponse]:
        """
        Every response to one query, not just the first.

        Coverage discovery is one-to-many: the query goes to a well-known
        topic that every service reads, and each answers separately on the
        reply topic the query named. :meth:`query` returns as soon as one
        replies, which is right for a request/reply flow with a single
        responder and wrong here — it would find whichever service happened to
        answer first and silently miss the rest.

        There is no completion signal to wait for, because a querier cannot
        know how many services exist. So this collects for a fixed window.
        """
        self._writer.write(query)
        responses: List[CoverageResponse] = []
        seen_services: set = set()
        deadline = time.time() + window
        while time.time() < deadline:
            for response in _samples(self._reader):
                if response.query_id != query.query_id:
                    continue
                # A service that re-announces mid-window could answer twice.
                key = tuple(sorted(r.service_id for r in (response.results or [])))
                if key in seen_services and key != ():
                    continue
                seen_services.add(key)
                responses.append(response)
            time.sleep(poll_interval)
        return responses


# --- Bootstrap: "which domain and which manifests?" -------------------------
# Demo-owned. 1.7 has no bootstrap exchange — a participant is assumed to
# already know its domain id and QoS profile, which is exactly what a fresh
# device does not. Catalogued in ar_demo/SPEC_COMPLIANCE.md.
# MAP_META, registered: RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1). The types
# are this repository's `oarc.bootstrap_*` extensions, so the profile object is
# given explicitly, and each keyed lane says what a clean stop does to it.
BOOTSTRAP_PROFILE = sd.dds.registry.profile("MAP_META")


class BootstrapService:
    """Server side: answer a client asking how to join."""

    def __init__(self, participant: "sd.dds.Participant"):
        self._reader = participant.reader(
            "oarc.bootstrap_query", topic=TOPIC_BOOTSTRAP_QUERY,
            qos_profile=BOOTSTRAP_PROFILE)
        # A clean stop keeps the answers: a client that asked is told how to
        # join whether or not the server is still up.
        self._writer = participant.writer(
            "oarc.bootstrap_response", topic=TOPIC_BOOTSTRAP_RESPONSE,
            qos_profile=BOOTSTRAP_PROFILE, on_exit="keep")

    def take_queries(self) -> List[BootstrapQuery]:
        return _samples(self._reader)

    def reply(self, response: BootstrapResponse) -> None:
        self._writer.write(response)


class BootstrapClient:
    """Client side: ask, and wait for the answer addressed to us."""

    def __init__(self, participant: "sd.dds.Participant"):
        # A clean stop withdraws the question: a client that left is no longer
        # asking.
        self._writer = participant.writer(
            "oarc.bootstrap_query", topic=TOPIC_BOOTSTRAP_QUERY,
            qos_profile=BOOTSTRAP_PROFILE, on_exit="withdraw")
        self._reader = participant.reader(
            "oarc.bootstrap_response", topic=TOPIC_BOOTSTRAP_RESPONSE,
            qos_profile=BOOTSTRAP_PROFILE)

    def request(self, query: BootstrapQuery, timeout: float = 5.0,
                retry_every: float = 1.0,
                poll_interval: float = DEFAULT_POLL_INTERVAL
                ) -> Optional[BootstrapResponse]:
        """
        Ask until answered or out of time.

        Retried because bootstrap is the one exchange that cannot assume
        discovery has settled: it is what a participant does *before* it
        knows anything about the deployment.
        """
        deadline = time.time() + timeout
        next_send = 0.0
        while time.time() < deadline:
            now = time.time()
            if now >= next_send:
                self._writer.write(query)
                next_send = now + retry_every
            for response in _samples(self._reader):
                if response.client_id == query.client_id:
                    return response
            time.sleep(poll_interval)
        return None
