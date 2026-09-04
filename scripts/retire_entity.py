#!/usr/bin/env python3
"""
Retire an entity: tombstone with a reason, then dispose.

    python3 scripts/retire_entity.py ent:duck:east "taken in for the winter"
    python3 scripts/retire_entity.py --restore

**This tool does not retire anything itself, and that is the whole design.**

The obvious implementation -- read the entity, publish a sample with
`state = RETIRED`, dispose the instance -- works for about as long as this
process lives. TRANSIENT_LOCAL history is scoped to the writer that published
it, so when the tool exits its tombstone and its dispose go with it, while the
model service's original ACTIVE sample is still latched. A browser open at the
time would see the retirement; the next one to load would be handed a live
duck. The same effect was measured for moves and written up in SPEC_COMPLIANCE
as "Two writers, one instance", where the conclusion was that an operator tool
should ask the authority rather than race it. This is that conclusion built.

So the tool publishes a `ModelCommand` and the service acts. It then watches
the entity topic and reports what actually happened, rather than reporting
that it sent a request -- a tool that says "retired" when it means "asked"
is how you end up debugging the wrong process.
"""

import argparse
import sys
import time
import uuid
from typing import Optional

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.qos_profiles import MODEL_COMMAND, MODEL_LATCHED
from spatialdds_demo.topics import TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity, ModelCommand
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

REQUESTER_ID = "tool:retire_entity"


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now % 1) * 1e9))


def send(participant: DomainParticipant, verb: str,
         entity_id: str = "", reason: str = "") -> ModelCommand:
    writer = tt.make_writer(
        participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
    command = ModelCommand(
        command_id=str(uuid.uuid4()), verb=verb, subject_id=entity_id,
        reason=reason, requester_id=REQUESTER_ID,
        # Retirement carries no pose. The member is guarded rather than
        # optional, so it is present and ignored.
        has_pose=False,
        pose=PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=False,
        extent=Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0]),
        stamp=_now())
    # A VOLATILE writer with nobody attached drops the sample on the floor.
    # The service is long-lived, so this is a discovery wait, not a retry.
    deadline = time.time() + 5.0
    while time.time() < deadline and not writer.get_publication_matched_status().current_count:
        time.sleep(0.05)
    writer.write(command)
    return command


def watch(participant: DomainParticipant, entity_id: str,
          timeout: float = 20.0) -> dict:
    """
    Follow the entity until it is gone, and report the order it happened in.

    Both events matter and so does their sequence: the tombstone says why, the
    dispose is the entity leaving. A dispose with no tombstone in front of it
    is an entity that vanished without explanation.
    """
    reader = tt.make_reader(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
    seen = {"tombstone": None, "disposed": False, "order": []}
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sample in tt.take_with_state(reader):
            if sample.data is None:
                seen["disposed"] = True
                seen["order"].append("disposed")
            elif sample.data.entity_id == entity_id:
                if sample.data.state.name == "RETIRED":
                    seen["tombstone"] = sample.data.state_reason
                    seen["order"].append("tombstone")
        if seen["disposed"] and seen["tombstone"] is not None:
            break
        time.sleep(0.05)
    return seen


def dispose_edge(rel_id: str, reason: str, domain_id: Optional[int] = None) -> int:
    """
    Ask the service to drop one edge.

    There is no tombstone to watch for: `Relationship` has no lifecycle state,
    so an edge cannot announce its own removal or say why. This reports what
    it asked and what the bus shows afterwards -- absence, which is all the
    edge is able to say.
    """
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)
    print(f"retire: asking the model service to dispose {rel_id}")
    print(f"retire: reason {reason!r} — kept in the log only; an edge has "
          f"nowhere to carry it")
    send(participant, "dispose_edge", rel_id, reason)
    time.sleep(2.0)
    print(f"retire: asked. The edge can only stop being there, so absence "
          f"from /v1/model is the whole of the evidence.")
    return 0


def run(entity_id: str, reason: str, restore: bool,
        domain_id: Optional[int] = None) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)

    if restore:
        send(participant, "restore")
        print("retire: asked the model service to re-seed")
        print("retire: a retired id is not burned — the same id can be published again")
        return 0

    print(f"retire: asking the model service to retire {entity_id}")
    send(participant, "retire", entity_id, reason)
    seen = watch(participant, entity_id)

    if seen["tombstone"] is None and not seen["disposed"]:
        print("retire: nothing happened — is the model service running, and "
              "does it own this entity?")
        return 1
    if seen["tombstone"] is not None:
        print(f"retire: tombstone — {seen['tombstone']!r}")
    else:
        print("retire: disposed with no tombstone seen (the reason was lost)")
    if seen["disposed"]:
        print("retire: disposed — the instance is gone")
    print(f"retire: order {' -> '.join(seen['order']) or 'nothing'}")
    return 0 if (seen["tombstone"] is not None and seen["disposed"]) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ask the model service to retire an entity, with a reason")
    parser.add_argument("entity_id", nargs="?", help="e.g. ent:duck:east")
    parser.add_argument("reason", nargs="?", default="",
                        help="carried into state_reason and shown to clients")
    parser.add_argument("--restore", action="store_true",
                        help="ask the service to re-seed the venue")
    parser.add_argument("--edge", metavar="REL_ID", default=None,
                        help="dispose one relationship instead of an entity")
    parser.add_argument("--domain", type=int, default=None)
    args = parser.parse_args()
    if args.edge:
        if not args.entity_id:
            parser.error("give a reason with --edge, e.g. --edge rel:x \"why\"")
        return dispose_edge(args.edge, args.entity_id, args.domain)
    if not args.restore and not (args.entity_id and args.reason):
        parser.error("give an entity_id and a reason, or --restore")
    return run(args.entity_id or "", args.reason, args.restore, args.domain)


if __name__ == "__main__":
    sys.exit(main())
