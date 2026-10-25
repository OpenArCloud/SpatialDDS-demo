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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import spatialdds as sd

from spatialdds_demo import lanes
from spatialdds.dds import to_json


class UndeclaredTopic(KeyError):
    """A publish to a topic no announce declared."""


class StreamPublisher:
    """
    Typed writers for the lanes an announce declares, and the announce itself.

    ``publish`` takes the dict the demo's builders already produce and writes a
    real sample. A payload that does not build into the announced type raises
    here, at the publisher — which is the whole point: under the envelope a
    malformed payload was a well-formed string, and only the consumer found
    out, one process and one transport hop later.

    On the SDK since the adoption. The announce is the participant's own
    (`p.announce`), so its topic rows are the writers' own `TopicMeta` and its
    departure is the SDK's: both doors on `close()`. One participant may speak
    for several services (a synthetic publisher's operators); each announce
    names its own lanes.
    """

    def __init__(self, participant: "sd.dds.Participant"):
        self._participant = participant
        self._writers: Dict[str, "sd.dds.Writer"] = {}
        self._lock = threading.Lock()

    def announce(self, announce: Dict[str, Any]) -> Dict[str, Any]:
        """
        Open a writer for each lane an announce declares, then announce them.

        Declaring from the announce rather than beside it is deliberate: the
        announce is the contract, so it is also the thing that creates the
        endpoints. Takes the N.1 dict the demo's builders produce; returns what
        was announced, as the SDK built it.
        """
        writers = [self._declare(row["name"], row["type"], row["qos_profile"])
                   for row in announce.get("topics") or []]
        return self._participant.announce(
            announce["kind"], coverage=announce.get("coverage") or [],
            ttl_sec=int(announce.get("ttl_sec") or 300),
            name=announce.get("name", ""), org=announce.get("org", ""),
            version=announce.get("version", ""),
            hints={h["key"]: h["value"] for h in announce.get("hints") or []},
            caps=announce.get("caps"),
            manifest_uri=announce.get("manifest_uri") or None,
            transforms=announce.get("transforms") or [],
            service_id=announce["service_id"], topics=writers,
            auth_hint=announce.get("auth_hint", ""),
            coverage_frame_ref=announce.get("coverage_frame_ref"))

    def announce_lanes(self, service_id: str, kind: str,
                       lanes_: Sequence[Tuple[str, str, str]], *,
                       coverage: Any = (), manifest_uri: Optional[str] = None,
                       **announce_kw: Any) -> Dict[str, Any]:
        """Announce a service by its (topic, type, profile) rows, opening a
        writer for each: for a publisher whose lanes are a table rather than
        an announce document. `announce_kw` is `p.announce`'s own."""
        writers = [self._declare(*lane) for lane in lanes_]
        return self._participant.announce(
            kind, coverage=list(coverage), service_id=service_id,
            topics=writers, manifest_uri=manifest_uri, **announce_kw)

    def _declare(self, topic: str, type_name: str, qos_profile: str):
        with self._lock:
            if topic in self._writers:
                return self._writers[topic]
        # Refused rather than skipped: a publisher announcing a lane it cannot
        # open has nothing to write, and the announce would promise a lane
        # that never carries a sample. The SDK says why.
        writer = lanes.open_writer(self._participant, type_name, topic,
                                   qos_profile=qos_profile)
        with self._lock:
            return self._writers.setdefault(topic, writer)

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
        """Depart every announced service: the participant's close does it,
        with both of §2.14's doors, and withdraws what its writers published."""
        self._participant.close()


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

