"""
The SpatialDDS 1.7 QoS profiles (spec §3.3.3), as CycloneDDS Qos objects.

`TopicMeta.qos_profile` is advertised in every announce and manifest. This
table is what makes that advertisement true on the wire rather than
decorative: every endpoint the demo creates goes through
``spatialdds_demo.typed_transport``, which resolves the profile name here.

Spec §3.3.3 defines each profile as a reliability / ordering / deadline
combination, and §3.3.2 marks some types as latched. Durability therefore
comes from the type table rather than the QoS table, and is folded in below
for the profiles whose types are described as "Latched; TRANSIENT_LOCAL".

Two places where the spec and DDS do not line up exactly, both flagged rather
than quietly resolved:

* **RADAR_RT reliability is "Partial".** DDS offers only BEST_EFFORT and
  RELIABLE; there is no partial-reliability kind in DDS-RTPS. Mapped to
  BEST_EFFORT, which is the behaviour "partial" describes for a real-time
  sensor lane that must not apply backpressure. Worth raising with the WG:
  either name a DDS kind or describe the intended mechanism.
* **Ordering is "Ordered" for every profile.** DDS orders per instance by
  default (DestinationOrder BY_RECEPTION_TIMESTAMP), so no policy is set for
  it; the spec's column is describing the default rather than requesting
  BY_SOURCE_TIMESTAMP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from cyclonedds import qos, util


@dataclass(frozen=True)
class QosProfile:
    """One row of spec §3.3.3, plus the durability §3.3.2 implies."""

    name: str
    reliable: bool
    deadline_ms: Optional[int]
    latched: bool = False          # TRANSIENT_LOCAL, per the §3.3.2 type notes
    keep_last: Optional[int] = None  # None means KEEP_ALL
    note: str = ""

    def to_qos(self, lifespan_sec: Optional[float] = None) -> qos.Qos:
        policies = []

        policies.append(
            qos.Policy.Reliability.Reliable(util.duration(seconds=1))
            if self.reliable else qos.Policy.Reliability.BestEffort
        )
        policies.append(
            qos.Policy.Durability.TransientLocal if self.latched
            else qos.Policy.Durability.Volatile
        )
        policies.append(
            qos.Policy.History.KeepLast(self.keep_last) if self.keep_last
            else qos.Policy.History.KeepAll
        )
        if self.deadline_ms is not None:
            policies.append(
                qos.Policy.Deadline(util.duration(milliseconds=self.deadline_ms))
            )
        # Announce.ttl_sec and friends map onto Lifespan, so the middleware
        # drops a stale sample instead of every consumer re-deriving staleness.
        if lifespan_sec:
            policies.append(qos.Policy.Lifespan(util.duration(seconds=lifespan_sec)))
        return qos.Qos(*policies)


# --- spec §3.3.3 ------------------------------------------------------------
PROFILES: Dict[str, QosProfile] = {
    p.name: p for p in (
        QosProfile("GEOM_TILE",     reliable=True,  deadline_ms=200),
        QosProfile("VIDEO_LIVE",    reliable=False, deadline_ms=33, keep_last=1),
        QosProfile("VIDEO_ARCHIVE", reliable=True,  deadline_ms=200),
        QosProfile("RADAR_RT",      reliable=False, deadline_ms=20, keep_last=1,
                   note="spec says 'Partial'; DDS has no partial reliability"),
        QosProfile("RF_BEAM_RT",    reliable=False, deadline_ms=20, keep_last=1),
        QosProfile("RADIO_SCAN_RT", reliable=False, deadline_ms=500, keep_last=1),
        QosProfile("SEG_MASK_RT",   reliable=False, deadline_ms=33, keep_last=1),
        QosProfile("DESC_BATCH",    reliable=True,  deadline_ms=100),
        QosProfile("MAP_META",      reliable=True,  deadline_ms=1000, latched=True, keep_last=1),
        QosProfile("ZONE_META",     reliable=True,  deadline_ms=1000, latched=True, keep_last=1),
        QosProfile("EVENT_RT",      reliable=True,  deadline_ms=100),
        QosProfile("POSE_RT",       reliable=False, deadline_ms=33, keep_last=1),
        QosProfile("VPS_REQ",       reliable=True,  deadline_ms=500),
        QosProfile("VPS_RESP",      reliable=True,  deadline_ms=500),
    )
}

# --- deployment-specific extensions (§3.3.2 allows these, documented) -------
# Anchor deltas have no registered type or QoS profile in 1.7. Reliable and
# latched so a late joiner sees the current anchor set.
PROFILES["ANCHOR_DELTA"] = QosProfile(
    "ANCHOR_DELTA", reliable=True, deadline_ms=1000, latched=True, keep_last=1,
    note="deployment-specific extension; no registered profile for anchors in 1.7",
)

# --- discovery ---------------------------------------------------------------
# Announce is keyed on service_id, so KEEP_LAST(1) is per service: a late
# joiner gets the current announce of every live service, and dispose on the
# instance means "this service is gone".
DISCOVERY_ANNOUNCE = QosProfile(
    "DISCOVERY_ANNOUNCE", reliable=True, deadline_ms=None, latched=True, keep_last=1,
    note="RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1) per key",
)
# Queries and their responses are request/reply: nothing to latch, and losing
# one is a failed request rather than a stale view.
DISCOVERY_QUERY = QosProfile(
    "DISCOVERY_QUERY", reliable=True, deadline_ms=None, latched=False, keep_last=None,
    note="RELIABLE + VOLATILE + KEEP_ALL",
)
PROFILES[DISCOVERY_ANNOUNCE.name] = DISCOVERY_ANNOUNCE
PROFILES[DISCOVERY_QUERY.name] = DISCOVERY_QUERY


class UnknownQosProfile(KeyError):
    """Raised for a profile name that is neither registered nor a documented extension."""


def get(profile_name: str) -> QosProfile:
    try:
        return PROFILES[profile_name]
    except KeyError:
        raise UnknownQosProfile(
            f"{profile_name!r} is not a SpatialDDS 1.7 QoS profile. "
            f"Known: {', '.join(sorted(PROFILES))}"
        ) from None


def qos_for(profile_name: str, lifespan_sec: Optional[float] = None) -> qos.Qos:
    """The CycloneDDS Qos for a spec profile name."""
    return get(profile_name).to_qos(lifespan_sec=lifespan_sec)


# Spec backstop: consumers should not use an announce beyond stamp + ttl_sec.
# The HTTP announce cache doubles it to tolerate clock skew between publisher
# and gateway. Kept here, next to the Lifespan mapping, so the two halves of
# one policy are visibly one decision rather than two accidents:
#   - the bus drops the sample at ttl_sec        (Lifespan, above)
#   - the HTTP cache drops it at 2 x ttl_sec     (TTL_BACKSTOP_MULTIPLIER)
# A service therefore disappears from the bus first and from HTTP search
# shortly after, which is the intended ordering for a cache in front of a bus.
TTL_BACKSTOP_MULTIPLIER = 2.0
