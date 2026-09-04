#!/usr/bin/env python3
"""
The ducks wander — a consumer that produces, and the first of its kind here.

    python3 -m spatialdds_demo.duck_mover

**It writes nothing to the model topics.** It reads the model the way any
client does, decides what it would like to be different, and asks the
authority on the command lane. That is the single-writer rule from Part 2 with
a second party actually exercising it: until now the only things asking were
operator scripts run by a person, and a script is easy to special-case in your
head. A service doing it continuously is the real shape.

**Everything it needs is in the latch**, so it is restart-safe by
construction. There is no state file, no seeded duck list, no configured
bounds: on start it reads the model and finds out what the world contains.
Kill it and start it again and it carries on from wherever the ducks are.

**It selects by type, not by id.** Anything typed as a rubber duck gets
wandered, so publishing a fourth duck makes it swim without touching this
file. A hardcoded id list would have made the demo's "the model is the
interface" claim false in the one place a reader would check.

**It reads the pond's bounds from the model too**, and re-reads them whenever
the pond changes. Shrink the pond and the ducks crowd into the smaller water
with no duck-to-water logic anywhere but the clamp below, which knows only
"stay inside the box the model currently says". That is the whole trick and
there is deliberately nothing else to it.
"""

import argparse
import math
import random
import signal
import sys
import time
from typing import Dict, List, Optional, Tuple

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.model_service import TYPE_RUBBER_DUCK
from spatialdds_demo.qos_profiles import MODEL_COMMAND, MODEL_LATCHED
from spatialdds_demo.topics import TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity, ModelCommand
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

REQUESTER_ID = "svc:mover:demo/ducks"

# The pond entity whose bounds are followed. Named rather than discovered by
# type, because "which water are these ducks on" is a question the model
# cannot answer yet: there is no `on` relationship, and Part 3 leaves
# computed containment alone until Relationship can carry a basis.
DEFAULT_BOUNDS_ENTITY = "ent:pond:littlefield"

# Kept off the declared edge. The pond's bounds are where the water stops, and
# a duck is not a point -- placing one exactly on the boundary puts half of it
# on the rim. One metre is about a duck.
INSET_M = 1.0

# How far a duck travels per move, and how often any duck moves. Two moves a
# second across the whole pond is a slow drift at human scale, which is what
# ducks do; it is also gentle on the command lane and on the browser.
STEP_M = 0.9
MOVES_PER_SECOND = 2.0


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now % 1) * 1e9))


def clamp_into(x: float, y: float, bounds: Aabb3, inset: float = INSET_M
               ) -> Tuple[float, float]:
    """
    Keep a point inside the box the model currently declares.

    The only geometry in this service. It knows nothing about ponds, water or
    ducks -- it knows that something published a box and that a thing should
    be in it, which is why shrinking the pond needs no code here at all.

    A box smaller than twice the inset would invert; in that case the inset is
    given up rather than the duck being placed outside. Better a duck on the
    rim of a pond too small to hold it than a duck in the plaza.
    """
    lo_x, hi_x = bounds.min_xyz[0] + inset, bounds.max_xyz[0] - inset
    lo_y, hi_y = bounds.min_xyz[1] + inset, bounds.max_xyz[1] - inset
    if lo_x > hi_x:
        lo_x = hi_x = (bounds.min_xyz[0] + bounds.max_xyz[0]) / 2
    if lo_y > hi_y:
        lo_y = hi_y = (bounds.min_xyz[1] + bounds.max_xyz[1]) / 2
    return min(max(x, lo_x), hi_x), min(max(y, lo_y), hi_y)


def heading_quaternion(dx: float, dy: float, fallback: List[float]) -> List[float]:
    """
    Point the duck where it is going.

    Identity orientation faces east in this frame (see the seeder's comments),
    so the yaw is measured counter-clockwise from +x and the quaternion is the
    plain rotation about z. A step of zero keeps whatever it was facing rather
    than snapping it to east.
    """
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return list(fallback)
    yaw = math.atan2(dy, dx)
    return [0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)]


class DuckMover:
    """Reads the model, asks for moves. Publishes nothing on model topics."""

    def __init__(self, participant: DomainParticipant,
                 bounds_entity: str = DEFAULT_BOUNDS_ENTITY,
                 rng: Optional[random.Random] = None):
        self._reader = tt.make_reader(
            participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        self._commands = tt.make_writer(
            participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
        self._bounds_entity = bounds_entity
        self._rng = rng or random.Random()
        self._entities: Dict[str, Entity] = {}
        self._counter = 0

    # --- reading the world -------------------------------------------------

    def poll(self) -> int:
        """Take whatever the bus has. Late joins and updates arrive the same
        way, so nothing here distinguishes them."""
        applied = 0
        for sample in tt.take_with_state(self._reader):
            if sample.data is None:
                continue          # a dispose; the id is not carried here.
            self._entities[sample.data.entity_id] = sample.data
            applied += 1
        return applied

    def bounds(self) -> Optional[Aabb3]:
        entity = self._entities.get(self._bounds_entity)
        if entity is None or not entity.has_extent:
            return None
        return entity.extent

    def ducks(self) -> List[Entity]:
        """By type. A fourth duck published by anyone starts swimming."""
        return sorted(
            (e for e in self._entities.values()
             if TYPE_RUBBER_DUCK in e.type_uris and e.has_pose
             and e.state.name == "ACTIVE"),
            key=lambda e: e.entity_id)

    # --- asking for a change ----------------------------------------------

    def step_for(self, duck: Entity, bounds: Aabb3) -> PoseSE3:
        """Where this duck should drift to next, clamped into the model's box."""
        angle = self._rng.uniform(0, 2 * math.pi)
        x = duck.pose.t[0] + STEP_M * math.cos(angle)
        y = duck.pose.t[1] + STEP_M * math.sin(angle)
        x, y = clamp_into(x, y, bounds)
        return PoseSE3(
            t=[x, y, duck.pose.t[2]],
            q=heading_quaternion(x - duck.pose.t[0], y - duck.pose.t[1],
                                 duck.pose.q))

    def ask_move(self, entity_id: str, pose: PoseSE3) -> None:
        self._counter += 1
        self._commands.write(ModelCommand(
            command_id=f"mover-{self._counter}",
            verb="move",
            subject_id=entity_id,
            reason="",
            requester_id=REQUESTER_ID,
            has_pose=True,
            pose=pose,
            has_extent=False,
            extent=Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0]),
            stamp=_now()))

    def tick(self) -> Optional[str]:
        """One duck, one step. Returns which, or None if there was nothing
        to do -- no bounds yet, or no ducks."""
        self.poll()
        bounds = self.bounds()
        if bounds is None:
            return None
        ducks = self.ducks()
        if not ducks:
            return None
        duck = ducks[self._counter % len(ducks)]
        self.ask_move(duck.entity_id, self.step_for(duck, bounds))
        return duck.entity_id


def run(domain_id: Optional[int] = None,
        bounds_entity: str = DEFAULT_BOUNDS_ENTITY) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)
    mover = DuckMover(participant, bounds_entity)

    print(f"mover: domain {domain_id}, requester {REQUESTER_ID}")
    print(f"mover: following the bounds of {bounds_entity}, inset {INSET_M} m")
    print(f"mover: {MOVES_PER_SECOND} move(s)/s, {STEP_M} m per step")
    print("mover: writes nothing on the model topics — it asks")

    # Wait for the world rather than assuming it. A mover that starts before
    # the model service has latched anything should idle, not crash.
    deadline = time.time() + 15
    while time.time() < deadline and mover.bounds() is None:
        mover.poll()
        time.sleep(0.1)
    if mover.bounds() is None:
        print(f"mover: no {bounds_entity} on the bus yet — idling until there is",
              flush=True)

    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    interval = 1.0 / MOVES_PER_SECOND
    moved = 0
    while not stop:
        try:
            if mover.tick():
                moved += 1
                if moved % 20 == 0:
                    bounds = mover.bounds()
                    print(f"mover: {moved} moves asked; bounds now "
                          f"x {bounds.min_xyz[0]:.1f}..{bounds.max_xyz[0]:.1f}, "
                          f"y {bounds.min_xyz[1]:.1f}..{bounds.max_xyz[1]:.1f}",
                          flush=True)
        except Exception as error:
            # The lane is untrusted traffic, the same as the service's own
            # command reader. A mover that dies takes the demo's motion with
            # it and says nothing.
            print(f"mover: tick failed: {error!r}", flush=True)
        time.sleep(interval)

    print(f"mover: stopping after {moved} moves. The ducks stay where they are "
          f"— the model service owns their poses, not this process.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wander the ducks inside whatever bounds the model declares")
    parser.add_argument("--domain", type=int, default=None)
    parser.add_argument("--bounds-entity", default=DEFAULT_BOUNDS_ENTITY,
                        help="whose extent to stay inside")
    args = parser.parse_args()
    return run(args.domain, args.bounds_entity)


if __name__ == "__main__":
    sys.exit(main())
