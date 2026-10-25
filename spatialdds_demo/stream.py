"""
Discovery-driven typed streaming: the demo's publish/subscribe edge.

The envelope gave consumers one reader that saw everything, at the cost of
putting every stream in one QoS lane behind one opaque JSON string. Typed
topics take that convenience away: a reader is per topic, per type, per
profile, so a consumer has to *learn* what exists before it can read it.

That is what discovery is for, and the spec already carries the answer —
`Announce.topics` is a list of `TopicMeta` rows naming each topic's registered
type and QoS profile. So:

* a publisher declares its lanes from the same announce it publishes, which
  makes "announced but not published" and "published but not announced"
  unrepresentable rather than merely discouraged;
* a subscriber reads announces off the well-known keyed topic, resolves each
  announced type through the §3.3.2 registry, and opens a typed reader per
  lane — including for services that start after it did.

Both sides hand dicts to the callers that want dicts (the dashboards, MCAP,
MQTT). The conversion happens here, at the edge, and the bus carries types.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from cyclonedds.domain import DomainParticipant

import spatialdds as sd

from spatialdds_demo import lanes, topic_types, typed_transport as tt
from spatialdds_demo.discovery_bus import AnnouncePublisher
from spatialdds.dds import from_json, to_json
from spatialdds.dds.types.spatial.disco import Announce


class UndeclaredTopic(KeyError):
    """A publish to a topic no announce declared."""


class StreamPublisher:
    """
    Typed writers for the lanes an announce declares.

    ``publish`` takes the dict the demo's builders already produce and writes a
    real sample. A payload that does not build into the announced type raises
    here, at the publisher — which is the whole point: under the envelope a
    malformed payload was a well-formed string, and only the consumer found
    out, one process and one transport hop later.
    """

    def __init__(self, participant: DomainParticipant, *, validate: bool = True):
        self._participant = participant
        self._announcer = AnnouncePublisher(participant, validate=validate)
        self._writers: Dict[str, tt.TypedDictWriter] = {}
        self._lock = threading.Lock()

    def announce(self, announce: Any) -> Announce:
        """
        Publish an announce and open a writer for each lane it declares.

        Declaring from the announce rather than beside it is deliberate: the
        announce is the contract, so it is also the thing that creates the
        endpoints.

        Accepts the dict the demo's builders produce, or a typed ``Announce``.
        """
        if not isinstance(announce, Announce):
            announce = from_json(Announce, announce)
        self._announcer.publish(announce)
        for row in announce.topics or []:
            self._declare(row.name, row.type, row.qos_profile)
        return announce

    def _declare(self, topic: str, type_name: str, qos_profile: str) -> None:
        with self._lock:
            if topic in self._writers:
                return
        datatype = topic_types.try_resolve(type_name)
        if datatype is None:
            # Refused rather than skipped: a publisher announcing a type it
            # cannot itself resolve has nothing to write, and the announce
            # would promise a lane that never carries a sample.
            raise topic_types.UnknownTopicType(
                f"{topic}: announced type {type_name!r} resolves to no class"
            )
        writer = tt.TypedDictWriter(self._participant, topic, datatype, qos_profile)
        with self._lock:
            self._writers[topic] = writer

    def publish(self, topic: str, payload: Any) -> None:
        with self._lock:
            writer = self._writers.get(topic)
        if writer is None:
            raise UndeclaredTopic(
                f"{topic} was never announced; declared lanes are "
                f"{', '.join(sorted(self._writers)) or '(none)'}"
            )
        writer.write(payload)

    @property
    def topics(self) -> List[str]:
        with self._lock:
            return sorted(self._writers)

    def close(self) -> None:
        """Depart every announced service, disposing its instance."""
        self._announcer.close()


# ``(type_name, topic, payload_dict, stamp_ns)`` — the shape the demo's
# consumers already route on. ``type_name`` is now the announced §3.3.2 type
# rather than the envelope's demo-private ``msg_type`` label.
StreamCallback = Callable[[str, str, Dict[str, Any], int], None]


class StreamSubscriber:
    """
    Reads announces, opens a typed reader per announced lane, delivers dicts.

    Services that appear later are picked up on the next poll; services that
    depart have their lanes reported through ``on_depart`` so a dashboard can
    grey them out instead of showing a frozen last value forever.

    On SDK readers since the adoption. A departure is either door, the
    Announce instance's dispose or a `Depart` on the spec's VOLATILE lane; a
    service whose writer was merely lost has not departed (§2.14), and its
    lanes stay open, because its records are still the last word on the bus.
    """

    def __init__(self, participant: "sd.dds.Participant",
                 callback: StreamCallback, *,
                 on_announce: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                 on_depart: Optional[Callable[[str], None]] = None,
                 ignore_local: bool = False):
        """
        ``ignore_local`` makes every lane reader skip what this participant's
        own writers published — what a bridge needs so it does not read its
        own republished output back and relay it forever.
        """
        self._participant = participant
        self._callback = callback
        self._on_announce = on_announce
        self._on_depart = on_depart
        self._ignore_local = ignore_local
        # ignore_local here too: a bridge that relays announces must not
        # hear its own relayed copies and carry them back out.
        self._announce_reader = participant.reader(
            "Announce", settle_s=0.3, ignore_local=ignore_local)
        self._depart_reader = participant.reader(
            "Depart", ignore_local=ignore_local)
        self._readers: Dict[str, Tuple["sd.dds.Reader", str]] = {}
        self._service_topics: Dict[str, List[str]] = {}
        #: Announced lanes this build could not open, with the reason. §3.3.2
        #: treats an unregistered type as an extension point, so one is
        #: skipped rather than fatal, and said here rather than nowhere.
        self.skipped: Dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def topics(self) -> List[str]:
        with self._lock:
            return sorted(self._readers)

    def _open(self, row: Dict[str, Any]) -> bool:
        name = row.get("name") or ""
        type_name = row.get("type") or ""
        qos_profile = row.get("qos_profile") or ""
        if not (name and type_name and qos_profile):
            return False
        with self._lock:
            if name in self._readers or name in self.skipped:
                return False
        try:
            reader = lanes.open_reader(
                self._participant, type_name, name, qos_profile=qos_profile,
                settle_s=0.0, ignore_local=self._ignore_local)
        except Exception as error:
            # A type or profile this build does not know, or a name that is
            # not a §3.3.1 topic. Skipped, not fatal, and kept with its reason.
            with self._lock:
                self.skipped[name] = f"{type(error).__name__}: {error}"
            return False
        with self._lock:
            self._readers[name] = (reader, type_name)
        return True

    def _departed(self, service_id: str) -> None:
        if self._service_topics.pop(service_id, None) is not None \
                and self._on_depart:
            self._on_depart(service_id)

    def poll_announces(self) -> List[str]:
        """Apply discovery: returns the topics newly subscribed this poll."""
        added: List[str] = []
        for ev in self._announce_reader.take():
            if isinstance(ev, sd.Dispose):
                self._departed(ev.key)
            elif isinstance(ev, sd.Sample):
                announce = to_json(ev.data)
                rows = announce.get("topics") or []
                added.extend(row["name"] for row in rows if self._open(row))
                self._service_topics[announce["service_id"]] = [
                    row.get("name", "") for row in rows]
                if self._on_announce:
                    self._on_announce(announce["service_id"], announce)
        for ev in self._depart_reader.take():
            if isinstance(ev, sd.Sample):
                self._departed(ev.data.service_id)
        return added

    def poll(self, *, stamp_ns: int = 0) -> int:
        """Deliver every waiting sample. Returns how many were delivered."""
        self.poll_announces()
        with self._lock:
            readers = list(self._readers.items())
        delivered = 0
        for topic, (reader, type_name) in readers:
            for ev in reader.take():
                if isinstance(ev, sd.Sample):
                    self._callback(type_name, topic, to_json(ev.data), stamp_ns)
                    delivered += 1
        return delivered


def build(datatype_name: str, payload: Any) -> Any:
    """Build a payload dict into its announced type. Raises on a bad payload."""
    return from_json(topic_types.resolve(datatype_name), payload)
