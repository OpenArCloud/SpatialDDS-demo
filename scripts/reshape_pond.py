#!/usr/bin/env python3
"""
Shrink the pond, and watch the ducks crowd into what is left.

    python3 scripts/reshape_pond.py --shrink        # to a quarter of the water
    python3 scripts/reshape_pond.py --shrink 0.5    # or any fraction
    python3 scripts/reshape_pond.py --bounds 11 -16 15 -12
    python3 scripts/reshape_pond.py --restore       # back to the declared water

It reshapes the pond by default and any box the service owns on request.
The venue declares two, and they drive different consumers:

    # the water. A robot is kept out of it, so this re-routes the robot.
    python3 scripts/reshape_pond.py --shrink 0.4

    # the shallows. Ducks are clamped into it, so this crowds the ducks.
    python3 scripts/reshape_pond.py --entity ent:shallows:littlefield --shrink 0.4

`set_extent` was never pond-specific -- the model service applies it to
whatever it owns -- so neither is this. Only `--restore` is: it knows the
venue's own seeded bounds and refuses anything else rather than guessing
what a stranger's seed was.

**Nothing here knows anything about ducks.** This sends one `set_extent`
command and stops. The ducks move because the mover reads the pond's bounds
off the model on every update and clamps into whatever it currently says --
so the entire mechanism connecting "the venue changed its mind about the
water" to "the ducks are somewhere else" is one box, published once, read by
whoever cares.

That is the point of the demonstration, and it is worth being clear about
what would be unremarkable: an application that moved the pond *and* the
ducks would prove nothing. Here the pond is told, and the ducks follow
because they are in a world rather than in a scene graph.

Like the other operator tools, this asks the authority and then reports what
the bus showed -- not what it sent.
"""

import argparse
import sys
import time
import uuid
from typing import List, Optional, Tuple

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.duck_mover import INSET_M
from spatialdds_demo.model_service import (
    POND_MAX, POND_MIN, SHALLOWS_MAX, SHALLOWS_MIN)
from spatialdds_demo.qos_profiles import MODEL_COMMAND, MODEL_LATCHED
from spatialdds_demo.topics import TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity, ModelCommand
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

POND_ID = "ent:pond:littlefield"
SHALLOWS_ID = "ent:shallows:littlefield"
REQUESTER_ID = "tool:reshape_pond"

# Below twice the mover's inset the clamp gives up the inset rather than the
# duck -- better a duck on the rim of a pond too small to hold it than a duck
# in the plaza. That degradation is deliberate and tested, but it is not the
# thing the demo is showing, and a viewer watching ducks sit on an edge would
# reasonably conclude the crowding had failed. So this warns before crossing.
MIN_USEFUL_SPAN_M = 2 * INSET_M


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now % 1) * 1e9))


def read_entity(participant: DomainParticipant, entity_id: str,
                timeout: float = 5.0) -> Optional[Entity]:
    reader = tt.make_reader(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sample in tt.take_samples(reader) or []:
            if sample.entity_id == entity_id:
                return sample
        time.sleep(0.05)
    return None


def send(participant: DomainParticipant, entity_id: str,
         extent: Aabb3) -> None:
    writer = tt.make_writer(
        participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
    deadline = time.time() + 5.0
    while (time.time() < deadline
           and not writer.get_publication_matched_status().current_count):
        time.sleep(0.05)
    writer.write(ModelCommand(
        command_id=str(uuid.uuid4()), verb="set_extent", subject_id=entity_id,
        reason="", requester_id=REQUESTER_ID,
        has_pose=False, pose=PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=True, extent=extent, stamp=_now()))


def shrunk(current: Aabb3, fraction: float) -> Aabb3:
    """A smaller box about the same centre. Height is left alone: the demo is
    about the water's edges, and a shallower pond would look like nothing."""
    out_min, out_max = [], []
    for axis in range(3):
        lo, hi = current.min_xyz[axis], current.max_xyz[axis]
        centre, half = (lo + hi) / 2, (hi - lo) / 2
        scale = 1.0 if axis == 2 else fraction
        out_min.append(centre - half * scale)
        out_max.append(centre + half * scale)
    return Aabb3(min_xyz=out_min, max_xyz=out_max)


def describe(extent: Aabb3) -> str:
    return (f"x {extent.min_xyz[0]:.1f}..{extent.max_xyz[0]:.1f}, "
            f"y {extent.min_xyz[1]:.1f}..{extent.max_xyz[1]:.1f}")


def spans(extent: Aabb3) -> Tuple[float, float]:
    return (extent.max_xyz[0] - extent.min_xyz[0],
            extent.max_xyz[1] - extent.min_xyz[1])


def apply(participant: DomainParticipant, entity_id: str, target: Aabb3) -> int:
    before = read_entity(participant, entity_id)
    if before is None:
        print(f"reshape: no {entity_id} on the bus — is the model service "
              f"running, and does it own that id?", file=sys.stderr)
        return 1

    span_x, span_y = spans(target)
    # Only the shallows have ducks clamping into them. Warning about any
    # other box's span would be a caution about a consequence that cannot
    # happen.
    if entity_id == SHALLOWS_ID and min(span_x, span_y) < MIN_USEFUL_SPAN_M:
        print(f"reshape: warning — {span_x:.1f} x {span_y:.1f} m is below the "
              f"{MIN_USEFUL_SPAN_M:.1f} m the mover needs for its {INSET_M} m "
              f"inset.")
        print("reshape: the ducks will sit on the edge rather than inside it. "
              "That is the clamp preferring the rim to the plaza, not a "
              "failure — but it does not read as crowding.")

    print(f"reshape: {entity_id}")
    print(f"reshape: {describe(before.extent)}  ->  {describe(target)}")
    send(participant, entity_id, target)

    deadline = time.time() + 10
    while time.time() < deadline:
        current = read_entity(participant, entity_id, timeout=0.5)
        if current is not None and all(
                abs(a - b) < 0.01
                for a, b in zip(current.extent.min_xyz + current.extent.max_xyz,
                                target.min_xyz + target.max_xyz)):
            print(f"reshape: the model now says {describe(current.extent)}")
            if entity_id == SHALLOWS_ID:
                print("reshape: nothing here told a duck anything — the mover "
                      "reads these bounds and clamps into them.")
            else:
                print("reshape: nothing here told a planner anything — the "
                      "keep-out node reads declared bounds and rebuilds its "
                      "mask from them.")
            return 0
        time.sleep(0.2)
    print(f"reshape: the bounds did not change — does the service own "
          f"{entity_id}?", file=sys.stderr)
    return 1


# What `--restore` knows how to put back. A tool that restored by guessing
# would be worse than one that declines: the seed is the venue's, not ours.
SEEDED = {POND_ID: (POND_MIN, POND_MAX),
          SHALLOWS_ID: (SHALLOWS_MIN, SHALLOWS_MAX)}


def run(shrink: Optional[float], restore: bool, bounds: Optional[List[float]],
        entity_id: str = POND_ID, domain_id: Optional[int] = None) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)

    if restore:
        seed = SEEDED.get(entity_id)
        if seed is None:
            print(f"reshape: no seeded bounds known for {entity_id}; give "
                  f"--bounds explicitly", file=sys.stderr)
            return 1
        return apply(participant, entity_id,
                     Aabb3(min_xyz=list(seed[0]), max_xyz=list(seed[1])))

    current = read_entity(participant, entity_id)
    if current is None:
        print(f"reshape: no {entity_id} on the bus", file=sys.stderr)
        return 1
    if bounds is not None:
        # Heights come from what the box already claims, so moving a footprint
        # cannot silently flatten a thing that stands up.
        return apply(participant, entity_id, Aabb3(
            min_xyz=[bounds[0], bounds[1], current.extent.min_xyz[2]],
            max_xyz=[bounds[2], bounds[3], current.extent.max_xyz[2]]))
    return apply(participant, entity_id, shrunk(current.extent, shrink or 0.5))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Change a declared box and let the model do the rest")
    parser.add_argument("--shrink", type=float, nargs="?", const=0.5, default=None,
                        metavar="FRACTION",
                        help="scale the current bounds about their centre (default 0.5)")
    parser.add_argument("--restore", action="store_true",
                        help="back to the bounds the venue seeded")
    parser.add_argument("--bounds", type=float, nargs=4,
                        metavar=("MIN_X", "MIN_Y", "MAX_X", "MAX_Y"),
                        help="explicit bounds in venue-frame metres")
    parser.add_argument("--entity", default=POND_ID, metavar="ENTITY_ID",
                        help=f"whose bounds to change (default {POND_ID})")
    parser.add_argument("--domain", type=int, default=None)
    args = parser.parse_args()
    if args.shrink is None and not args.restore and args.bounds is None:
        parser.error("give --shrink, --bounds or --restore")
    return run(args.shrink, args.restore, args.bounds, args.entity, args.domain)


if __name__ == "__main__":
    sys.exit(main())
