"""Claims about the venue: the one writer every relationship asserter uses.

The OWM 0.2 relationship prototype (docs/owm-02-census.md, and the ruling
recorded there). An edge is `oarc.relationship`, a demo-owned extension in
the 0.2 design's shape (idl/demo18/oarc_owm02.idl). This module holds what
every asserter must do the same way, so that each convention has one
enforcement point rather than one per service:

* **Proposition identity.** One asserter, one (subject, predicate, object),
  one id. Holding the same proposition again with a changed opinion revises
  that instance; another asserter's claim about the same proposition is
  always another edge. `edge_id` is the only place an id is made.
* **Revision is a change of opinion.** A new confidence, `valid_from` or
  qualifier revises the edge. Fresh evidence for an unchanged opinion does
  not: `derived_from` names the records that *established* the claim.
* **Withdrawal says why.** A terminal sample, RETIRED, with the reason in the
  type, then the dispose (§2.14). There are three causes, and a consumer can
  tell them apart by the reason's prefix:
    - `no longer holds`, when the world changed. `valid_until` is set to when
      the evidence first showed it.
    - `subject unobserved`, when the subject stopped being seen.
      `valid_until` is the last evidence that showed the claim true, which is
      a lower bound on when it stopped being true.
    - `asserter stopped`, on a clean stop: the writer's `on_exit="withdraw"`,
      and `valid_until` stays unset, because the world was not consulted.

  A killed asserter withdraws nothing: its readers see liveliness end, and
  every edge it held stands, as the SDK does for any latched lane.
* **The asserter is the participant's `service_id`**, the convention until
  transport identity carries more.
"""
from __future__ import annotations

import dataclasses
import time
import uuid
from typing import Dict, Iterable, Optional, Sequence, Tuple

import spatialdds as sd
from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.common import KV
from spatialdds.dds.types.spatial.owm import Basis

from spatialdds_demo.lanes import (
    MODEL_LATCHED, RELATIONSHIP, RELATIONSHIP_STREAM, RELATIONSHIP_TOPIC)
from spatialdds_demo_idl18.oarc_owm import (
    EvidenceRef, Relationship, RelationshipLifecycle)

#: The relationship lane, named in `lanes.py` with the deployment's others.
STREAM = RELATIONSHIP_STREAM
TOPIC = RELATIONSHIP_TOPIC

# -- predicates -----------------------------------------------------------------
# Checked against OGC GeoSPARQL 1.1 (22-047r1), not written from memory: the
# Simple Features relations are defined on geo:SpatialObject, so they "may
# connect features as well as geometries" (Table 2).

#: DE-9IM T*F**F***. The subject's geometry lies inside the object's.
SF_WITHIN = "http://www.opengis.net/ont/geosparql#sfWithin"

#: Wikidata "part of". Mereology rather than geometry: the declared edges say
#: what two boxes cannot, and the entity types already borrow Wikidata.
PART_OF = "http://www.wikidata.org/prop/direct/P361"

#: GeoSPARQL has no nearness relation, only `geof:metricDistance`, so near is
#: the demo's own, on the demo's own domain. Its threshold is in the URI's
#: fragment so that proposition identity stays (subject, predicate, object):
#: near at 15 m and near at 6 m are two propositions, and say so.
NEAR_BASE = "https://demo.spatialdds.org/owm/0.2/predicate/near"


def near(threshold_m: float) -> str:
    """The near predicate for one threshold, `...near#15m`."""
    return f"{NEAR_BASE}#{threshold_m:g}m"


# -- the three retirement causes ----------------------------------------------

NO_LONGER_HOLDS = "no longer holds"
SUBJECT_UNOBSERVED = "subject unobserved"
ASSERTER_STOPPED = "asserter stopped"
#: A party to the edge was withdrawn. The world changed, so it shares the
#: first cause, and names which party in its detail.
PARTY_RETIRED = NO_LONGER_HOLDS


def cause_of(reason: str) -> str:
    """Which of the three causes a retirement reason gives."""
    for cause in (NO_LONGER_HOLDS, SUBJECT_UNOBSERVED, ASSERTER_STOPPED):
        if reason.startswith(cause):
            return cause
    return ""


# -- identity ---------------------------------------------------------------

def edge_id(asserter: str, subject: str, predicate: str, obj: str) -> str:
    """The id for one asserter's opinion about one proposition.

    Deterministic, so a restarted asserter holding the same proposition
    revises its old edge rather than minting a second one. The asserter's
    name leads it, with `/` made `.` so the id stays one URL path segment
    for the binding's per-instance resources.
    """
    digest = uuid.uuid5(uuid.NAMESPACE_URL,
                        "\x1f".join((asserter, subject, predicate, obj)))
    return f"rel:{asserter.replace('/', '.')}:{digest.hex[:16]}"


# -- times ------------------------------------------------------------------

ZERO = Time(sec=0, nanosec=0)


def to_time(t: float) -> Time:
    ns = int(round(t * 1e9))
    sec, nanosec = divmod(ns, 1_000_000_000)
    return Time(sec=sec, nanosec=nanosec)


def now_time() -> Time:
    return to_time(time.time())


def evidence(topic: str, key: str, stamp: Time) -> EvidenceRef:
    """One record a claim read: the lane, the instance, and the sample's stamp."""
    return EvidenceRef(topic=topic, key=key,
                       stamp=Time(sec=stamp.sec, nanosec=stamp.nanosec))


# -- the writer ----------------------------------------------------------------

Opinion = Tuple[Optional[float], Optional[Tuple[int, int]], Tuple[Tuple[str, str], ...]]


def _opinion(r: Relationship) -> Opinion:
    return (r.confidence if r.has_confidence else None,
            (r.valid_from.sec, r.valid_from.nanosec) if r.has_valid_from
            else None,
            tuple((kv.key, kv.value) for kv in r.meta))


class ClaimWriter:
    """One asserter's edges, on the scene's relationship lane.

    `on_exit="withdraw"`, so a clean stop retires every held edge with the
    participant's close reason, which an asserter gives as
    `asserter stopped: ...`.
    """

    def __init__(self, participant: "sd.dds.Participant") -> None:
        self.asserter = participant.service_id
        self._w = participant.writer(RELATIONSHIP, stream=STREAM,
                                     qos_profile=MODEL_LATCHED,
                                     on_exit="withdraw")
        self.topic = self._w.topic
        self._held: Dict[str, Relationship] = {}

    def held(self) -> Dict[str, Relationship]:
        return dict(self._held)

    def holds(self, subject: str, predicate: str, obj: str) -> bool:
        return edge_id(self.asserter, subject, predicate, obj) in self._held

    def hold(self, subject: str, predicate: str, obj: str, basis: Basis, *,
             valid_from: Optional[Time] = None,
             confidence: Optional[float] = None,
             derived_from: Sequence[EvidenceRef] = (),
             meta: Iterable[Tuple[str, str]] = ()) -> Optional[Relationship]:
        """Assert a proposition. Returns what was written, or None if the
        opinion is unchanged and nothing needed writing."""
        rid = edge_id(self.asserter, subject, predicate, obj)
        prior = self._held.get(rid)
        edge = Relationship(
            id=rid, subject_ref=subject, predicate=predicate, object_ref=obj,
            basis=basis, asserter=self.asserter,
            has_confidence=confidence is not None,
            confidence=float(confidence) if confidence is not None else 0.0,
            derived_from=list(derived_from), stamp=now_time(),
            has_valid_from=valid_from is not None,
            valid_from=valid_from if valid_from is not None else ZERO,
            has_valid_until=False, valid_until=ZERO,
            meta=[KV(key=k, value=v) for k, v in meta],
            state=RelationshipLifecycle.CREATED, state_reason="")
        if prior is not None:
            if _opinion(prior) == _opinion(edge) and prior.basis == basis:
                return None
            # The evidence that established the claim stays with it; the
            # revision is of the opinion, not of the lineage.
            edge = dataclasses.replace(
                edge, derived_from=list(prior.derived_from) or edge.derived_from,
                state=RelationshipLifecycle.REVISED)
        self._w.write(edge)
        self._held[rid] = edge
        return edge

    def retire(self, subject: str, predicate: str, obj: str, cause: str,
               detail: str, valid_until: Optional[Time] = None
               ) -> Optional[Relationship]:
        """Withdraw a held proposition: terminal sample, then dispose.

        The terminal sample is written here first because it carries
        `valid_until`, which the SDK's `withdraw()` cannot set: it rewrites
        only `state`, `state_reason` and `stamp`, the members it finds by
        name. `withdraw()` then writes it once more, restamped, and disposes.
        """
        rid = edge_id(self.asserter, subject, predicate, obj)
        prior = self._held.pop(rid, None)
        if prior is None:
            return None
        reason = f"{cause}: {detail}"
        terminal = dataclasses.replace(
            prior, stamp=now_time(), state=RelationshipLifecycle.RETIRED,
            state_reason=reason, has_valid_until=valid_until is not None,
            valid_until=valid_until if valid_until is not None else ZERO)
        self._w.write(terminal)
        self._w.withdraw(rid, reason=reason)
        return terminal

    def retire_involving(self, entity_id: str, cause: str, detail: str,
                         valid_until: Optional[Time] = None) -> int:
        """Withdraw every held edge whose subject or object is `entity_id`."""
        n = 0
        for edge in list(self._held.values()):
            if entity_id in (edge.subject_ref, edge.object_ref):
                self.retire(edge.subject_ref, edge.predicate, edge.object_ref,
                            cause, detail, valid_until)
                n += 1
        return n
