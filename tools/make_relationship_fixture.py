#!/usr/bin/env python3
"""
Write web/tests/fixtures/relationship_events.json: the relationship lane's
N.5 events as the binding sends them.

Each sample is projected the way `bridges/web_bridge/binding.py`'s pump
projects one (`Pump._project`): serialized by CycloneDDS, decoded by the
gateway's own XCDR2 reader against the extension's schema record, then
projected to N.1. So the browser's reader (`web/src/claims.ts`) is held to
what the binding emits, not to a hand-written approximation of it.

The edges are the ones the venue asserts, at fixed times so the file does
not change on every run: both zone watches' claims about one proposition,
the near fuser's derived edge with its lineage, a declared part-of, and
duck_mover's enforced containment. Then the replay marker, then one zone
watch's edge retiring with its reason, then the other zone watch killed.

    python3 tools/make_relationship_fixture.py
"""
import dataclasses
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from spatialdds.dds.types.spatial.common import KV  # noqa: E402
from spatialdds.dds.types.spatial.owm import Basis  # noqa: E402
from spatialdds.server import gateway, webjson, xcdr2  # noqa: E402

from bridges.web_bridge import binding  # noqa: E402
from spatialdds_demo import claims  # noqa: E402
from spatialdds_demo.duck_mover import REQUESTER_ID as MOVER  # noqa: E402
from spatialdds_demo.model_service import SOURCE_ID as VENUE  # noqa: E402
from spatialdds_demo.near_fuser import SOURCE_ID as NEAR  # noqa: E402
from spatialdds_demo.zone_watch import SOURCE_IDS as ZONE  # noqa: E402
from spatialdds_demo_idl18.oarc_owm import (  # noqa: E402
    Relationship, RelationshipLifecycle)

OUT = REPO / "web" / "tests" / "fixtures" / "relationship_events.json"
FQN = "oarc_owm.Relationship"
ROBOT, FOUNTAIN, DUCK = ("ent:robot:tb3", "ent:fountain:littlefield",
                         "ent:duck:east")
T0 = 1792960000.0


def edge(asserter, subject, predicate, obj, basis, valid_from=None,
         derived_from=(), meta=()):
    return Relationship(
        id=claims.edge_id(asserter, subject, predicate, obj),
        subject_ref=subject, predicate=predicate, object_ref=obj, basis=basis,
        asserter=asserter, has_confidence=False, confidence=0.0,
        derived_from=list(derived_from), stamp=claims.to_time(T0 + 0.5),
        has_valid_from=valid_from is not None,
        valid_from=claims.to_time(valid_from) if valid_from else claims.ZERO,
        has_valid_until=False, valid_until=claims.ZERO,
        meta=[KV(key=k, value=v) for k, v in meta],
        state=RelationshipLifecycle.CREATED, state_reason="")


def main() -> int:
    st = binding.build_state()
    dec = xcdr2.Decoder(st.model)
    proj = webjson.Projector(st.model, webjson.tables())

    def project(r):
        return proj.project(FQN, dec.decode(FQN, r.serialize()))

    pose_lane = "spatialdds/ut-austin-littlefield/world/model_pose/v1"
    latched = edge(ZONE["latched"], ROBOT, claims.SF_WITHIN, FOUNTAIN,
                   Basis.OBSERVED, T0 + 0.1,
                   meta=[("oarc.claim.evidence", "latched")])
    fast = edge(ZONE["fast"], ROBOT, claims.SF_WITHIN, FOUNTAIN,
                Basis.OBSERVED, T0 - 0.6,
                meta=[("oarc.claim.evidence", "fast")])
    near = edge(NEAR, ROBOT, claims.near(15), DUCK, Basis.DERIVED, T0 - 10,
                derived_from=[
                    claims.evidence(pose_lane, ROBOT, claims.to_time(T0 - 10)),
                    claims.evidence(pose_lane, DUCK, claims.to_time(T0 - 10.2))],
                meta=[("oarc.near.threshold_m", "15")])
    part = edge(VENUE, "ent:pond:littlefield", claims.PART_OF, FOUNTAIN,
                Basis.DECLARED)
    duck = edge(MOVER, DUCK, claims.SF_WITHIN, "ent:shallows:littlefield",
                Basis.DECLARED,
                meta=[("oarc.claim.enforced_by", "duck_mover's clamp")])
    terminal = dataclasses.replace(
        fast, state=RelationshipLifecycle.RETIRED, has_valid_until=True,
        valid_until=claims.to_time(T0 + 20),
        state_reason=f"{claims.NO_LONGER_HOLDS}: {ROBOT} left {FOUNTAIN}'s "
                     f"extent")

    topic = claims.TOPIC
    replay = [latched, fast, near, part, duck]
    events = [{"event": "sample", "topic": topic, "key": e.id,
               "data": project(e)} for e in replay]
    events.append({"event": "end_of_replay", "topic": topic,
                   "replayed": len(replay)})
    events.append({"event": "sample", "topic": topic, "key": fast.id,
                   "data": project(terminal)})
    events.append({"event": "dispose", "topic": topic, "key": fast.id,
                   "reason": terminal.state_reason})
    # The latched zone watch killed: its writer's liveliness ends, in the
    # gateway's own event shape, and nothing is disposed.
    events.append(gateway.liveliness(topic, latched.id, False))
    OUT.write_text(json.dumps({
        "$comment": "Written by tools/make_relationship_fixture.py; see its "
                    "docstring. Do not edit by hand.",
        "events": events}, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(REPO)}: {len(events)} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
