#!/usr/bin/env python3
"""
Move one thing in the world model, and watch every connected client follow.

    scripts/move_duck.py ent:duck:west 9.0 -12.0
    scripts/move_duck.py ent:duck:west 9.0 -12.0 -1.5

Coordinates are metres in the entity's own frame -- the venue frame the client
localizes into -- so they are the same numbers the publisher seeded with, not
latitude and longitude.

The entity is read off the bus rather than rebuilt from the seed. Both model
topics are TRANSIENT_LOCAL, so the current sample for a key is there for the
taking, and copying it means this changes the pose and the stamp and provably
nothing else: an operator tool that quietly reverted a field someone had
edited would be worse than no tool.

Same key, so this is an update to an existing instance and not a second duck.
"""

import argparse
import sys
import time
from typing import Optional

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.qos_profiles import MODEL_LATCHED
from spatialdds_demo.topics import TOPIC_MODEL_ENTITY_V1
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity
from spatialdds_idl.spatial.core import PoseSE3


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move one world-model entity (demo-local oarc_model)")
    parser.add_argument("entity_id", help="e.g. ent:duck:west")
    parser.add_argument("x", type=float, help="metres, entity's own frame")
    parser.add_argument("y", type=float)
    parser.add_argument("z", type=float, nargs="?", default=None,
                        help="defaults to the entity's current height")
    parser.add_argument("--domain", type=int, default=None)
    args = parser.parse_args()

    domain_id = require_dds_env() if args.domain is None else args.domain
    participant = DomainParticipant(domain_id)

    entity = read_entity(participant, args.entity_id)
    if entity is None:
        print(f"no entity {args.entity_id!r} on the bus — is the publisher running?",
              file=sys.stderr)
        return 1

    was = list(entity.pose.t)
    z = was[2] if args.z is None else args.z
    now = time.time()

    # Only the pose and the stamp. Everything else is the sample as it was.
    entity.pose = PoseSE3(t=[args.x, args.y, z], q=list(entity.pose.q))
    entity.stamp = Time(sec=int(now), nanosec=int((now % 1) * 1e9))

    writer = tt.make_writer(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
    writer.write(entity)
    print(f"{entity.entity_id}: ({was[0]:.2f}, {was[1]:.2f}, {was[2]:.2f}) "
          f"-> ({args.x:.2f}, {args.y:.2f}, {z:.2f})")

    # The write is asynchronous; leaving immediately can close the writer
    # before it has been delivered.
    time.sleep(1.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
