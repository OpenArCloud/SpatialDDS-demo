#!/usr/bin/env python3
"""
Move one thing in the world model, and watch every connected client follow.

    scripts/move_duck.py ent:duck:west 9.0 -12.0
    scripts/move_duck.py ent:duck:west 9.0 -12.0 -1.5
    scripts/move_duck.py --reset            # everything back to its seeded pose

The pool, measured off the tiles in these coordinates: water is roughly
x 5..20, y -10..-18. Beyond that is rim and plaza -- y=-2 and y=-22 are about
a metre higher -- and x~8 is the sculpture in the middle.

Coordinates are metres in the entity's own frame -- the venue frame the client
localizes into -- so they are the same numbers the publisher seeded with, not
latitude and longitude.

**This tool does not move anything itself.** It publishes a `ModelCommand`
and the model service -- the writer that latches the entity -- applies it.

It used to write the new pose directly, and that worked for exactly as long
as this process lived. TRANSIENT_LOCAL history is scoped to the writer that
published it, so the moved pose died with the tool while the service's seed
stayed latched: a browser open at the time followed the duck, and the next one
to load was handed it back at its starting position. Measured, and recorded in
SPEC_COMPLIANCE as "Two writers, one instance". Retirement hit the same wall
and reached the same answer, so both tools now ask rather than race, and the
repo has one write path instead of two with different durability.

The service still does the read-modify-write on its own latched copy, so a
move changes the pose and the stamp and provably nothing else: an operator
tool that quietly reverted a field someone had edited would be worse than no
tool. Same key, so this is an update to an existing instance, not a second
duck.

Like `retire_entity.py`, this reports what the bus showed, not what it sent.
"""

import argparse
import sys
import time
from typing import Optional, Tuple

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.qos_profiles import MODEL_COMMAND, MODEL_LATCHED
from spatialdds_demo.topics import TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity, ModelCommand
from spatialdds_idl.spatial.core import PoseSE3

REQUESTER_ID = "tool:move_duck"


def read_entity(participant: DomainParticipant, entity_id: str,
                timeout: float = 5.0) -> Optional[Entity]:
    """The current sample for one key, from the writer's latched history."""
    reader = tt.make_reader(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
    deadline = time.time() + timeout
    seen = {}
    while time.time() < deadline:
        for sample in tt.take_samples(reader) or []:
            seen[sample.entity_id] = sample
        if entity_id in seen:
            return seen[entity_id]
        time.sleep(0.05)
    if seen:
        print(f"entities on the bus: {', '.join(sorted(seen))}", file=sys.stderr)
    return None


def send(participant: DomainParticipant, verb: str, entity_id: str = "",
         pose: Optional[PoseSE3] = None) -> None:
    """Ask the service. See the module docstring for why this is not a write."""
    import uuid

    writer = tt.make_writer(
        participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
    now = time.time()
    command = ModelCommand(
        command_id=str(uuid.uuid4()), verb=verb, entity_id=entity_id, reason="",
        requester_id=REQUESTER_ID, has_pose=pose is not None,
        pose=pose or PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0]),
        stamp=Time(sec=int(now), nanosec=int((now % 1) * 1e9)))
    # The command lane is VOLATILE: a writer with nobody attached drops the
    # sample on the floor. The service is long-lived, so this is a discovery
    # wait, not a retry.
    deadline = time.time() + 5.0
    while (time.time() < deadline
           and not writer.get_publication_matched_status().current_count):
        time.sleep(0.05)
    writer.write(command)


def await_pose(participant: DomainParticipant, entity_id: str,
               target: Tuple[float, float], timeout: float = 10.0) -> Optional[Entity]:
    """Watch until the bus agrees, so this reports what happened rather than
    what was asked for."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = read_entity(participant, entity_id, timeout=0.5)
        if current is not None and all(
                abs(a - b) < 0.01 for a, b in zip(current.pose.t[:2], target)):
            return current
        time.sleep(0.1)
    return None


def reset(domain_id: Optional[int] = None) -> int:
    """
    Put everything back where the publisher seeded it.

    Asks the service to re-seed, which republishes under the same keys -- an
    update to the existing instances, not a second set of ducks.
    """
    from spatialdds_demo.model_service import seed_entities

    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)
    send(participant, "restore")

    seeded = {e.entity_id: e for e in seed_entities()
              if e.entity_id.startswith("ent:duck")}
    ok = True
    for entity_id, entity in sorted(seeded.items()):
        landed = await_pose(participant, entity_id,
                            (entity.pose.t[0], entity.pose.t[1]))
        if landed is None:
            print(f"{entity_id}: did not come back to "
                  f"({entity.pose.t[0]:.2f}, {entity.pose.t[1]:.2f})")
            ok = False
        else:
            print(f"{entity_id}: ({landed.pose.t[0]:.2f}, {landed.pose.t[1]:.2f})")
    return 0 if ok else 1


def move(entity_id: str, x: float, y: float, z: Optional[float] = None,
         domain_id: Optional[int] = None) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)

    current = read_entity(participant, entity_id)
    if current is None:
        print(f"{entity_id}: not on the bus — is the model service running?",
              file=sys.stderr)
        return 1
    was = list(current.pose.t)
    height = was[2] if z is None else z

    send(participant, "move", entity_id,
         PoseSE3(t=[x, y, height], q=list(current.pose.q)))
    landed = await_pose(participant, entity_id, (x, y))
    if landed is None:
        print(f"{entity_id}: the service did not move it — does it own this "
              f"entity?", file=sys.stderr)
        return 1
    print(f"{entity_id}: ({was[0]:.2f}, {was[1]:.2f}, {was[2]:.2f}) -> "
          f"({landed.pose.t[0]:.2f}, {landed.pose.t[1]:.2f}, {landed.pose.t[2]:.2f})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move one world-model entity (demo-local oarc_model)")
    parser.add_argument("entity_id", nargs="?", help="e.g. ent:duck:west")
    parser.add_argument("x", type=float, nargs="?", help="metres, entity's own frame")
    parser.add_argument("y", type=float, nargs="?")
    parser.add_argument("z", type=float, nargs="?", default=None,
                        help="defaults to the entity's current height")
    parser.add_argument("--reset", action="store_true",
                        help="put every entity back where the publisher seeded it")
    parser.add_argument("--domain", type=int, default=None)
    args = parser.parse_args()

    if args.reset:
        return reset(args.domain)
    if args.entity_id is None or args.x is None or args.y is None:
        parser.error("give an entity_id with x and y, or --reset")
    return move(args.entity_id, args.x, args.y, args.z, args.domain)


if __name__ == "__main__":
    sys.exit(main())
