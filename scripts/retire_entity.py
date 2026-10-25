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

import spatialdds as sd

from spatialdds_demo.lanes import MODEL_COMMAND, MODEL_LATCHED, SCENE
from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.owm import ModelCommand
from spatialdds.dds.types.spatial.core import Aabb3, PoseSE3

REQUESTER_ID = "tool:retire_entity"


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now % 1) * 1e9))


def send(participant: "sd.dds.Participant", verb: str,
         entity_id: str = "", reason: str = "") -> ModelCommand:
    writer = participant.writer("model_command", qos_profile=MODEL_COMMAND)
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
    writer.wait_matched(timeout=5.0)
    writer.write(command)
    return command


def watch(participant: "sd.dds.Participant", entity_id: str,
          timeout: float = 20.0) -> dict:
    """
    Follow the entity until it is gone, and report the order it happened in.

    Both events matter and so does their sequence: the tombstone says why, the
    dispose is the entity leaving. A dispose with no tombstone in front of it
    is an entity that vanished without explanation.
    """
    reader = participant.reader("model_entity", qos_profile=MODEL_LATCHED,
                                settle_s=0.3)
    seen = {"tombstone": None, "disposed": False, "order": []}
    deadline = time.time() + timeout
    while time.time() < deadline:
        for ev in reader.take():
            # A dispose names its key now. This counted *any* dispose as this
            # entity's until the SDK adoption, because the old read could not
            # say which instance one was for.
            if isinstance(ev, sd.Dispose) and ev.key == entity_id:
                seen["disposed"] = True
                seen["order"].append("disposed")
            elif isinstance(ev, sd.Sample) and ev.key == entity_id:
                # The withdrawal re-writes the terminal sample before its
                # dispose, so the tombstone may arrive twice; it is one fact.
                if ev.data.state.name == "RETIRED" \
                        and seen["tombstone"] is None:
                    seen["tombstone"] = ev.data.state_reason
                    seen["order"].append("tombstone")
        if seen["disposed"] and seen["tombstone"] is not None:
            break
        time.sleep(0.05)
    return seen


def run(entity_id: str, reason: str, restore: bool,
        domain_id: Optional[int] = None) -> int:
    participant = sd.participant(SCENE, domain=domain_id,
                                 service_id=REQUESTER_ID)

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
    # `--edge REL_ID` is gone with the type. It sent the `dispose_edge` verb,
    # which is not one of the five `spatial.owm/0.1` defines ("retire",
    # "move", "set_extent", "restore", "goto") -- the module carries no
    # relationship type for it to act on. A reader who reaches for the old
    # flag gets argparse's unrecognised-argument error, which is a refusal
    # with a name in it; see docs/owm-1.8-census.md for where the capability
    # went.
    parser.add_argument("--domain", type=int, default=None)
    args = parser.parse_args()
    if not args.restore and not (args.entity_id and args.reason):
        parser.error("give an entity_id and a reason, or --restore")
    return run(args.entity_id or "", args.reason, args.restore, args.domain)


if __name__ == "__main__":
    sys.exit(main())
