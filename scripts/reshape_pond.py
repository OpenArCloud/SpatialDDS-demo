#!/usr/bin/env python3
"""
Shrink the pond, and watch the ducks crowd into what is left.

    python3 scripts/reshape_pond.py --shrink        # to a quarter of the water
    python3 scripts/reshape_pond.py --shrink 0.5    # or any fraction
    python3 scripts/reshape_pond.py --bounds 11 -16 15 -12
    python3 scripts/reshape_pond.py --restore       # back to the declared water

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
from spatialdds_demo.model_service import POND_MAX, POND_MIN
from spatialdds_demo.qos_profiles import MODEL_COMMAND, MODEL_LATCHED
from spatialdds_demo.topics import TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity, ModelCommand
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

POND_ID = "ent:pond:littlefield"
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


def read_pond(participant: DomainParticipant, timeout: float = 5.0
              ) -> Optional[Entity]:
    reader = tt.make_reader(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sample in tt.take_samples(reader) or []:
            if sample.entity_id == POND_ID:
                return sample
        time.sleep(0.05)
    return None


def send(participant: DomainParticipant, extent: Aabb3) -> None:
    writer = tt.make_writer(
        participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
    deadline = time.time() + 5.0
    while (time.time() < deadline
           and not writer.get_publication_matched_status().current_count):
        time.sleep(0.05)
    writer.write(ModelCommand(
        command_id=str(uuid.uuid4()), verb="set_extent", subject_id=POND_ID,
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


def apply(participant: DomainParticipant, target: Aabb3) -> int:
    before = read_pond(participant)
    if before is None:
        print(f"reshape: no {POND_ID} on the bus — is the model service running?",
              file=sys.stderr)
        return 1

    span_x, span_y = spans(target)
    if min(span_x, span_y) < MIN_USEFUL_SPAN_M:
        print(f"reshape: warning — {span_x:.1f} x {span_y:.1f} m is below the "
              f"{MIN_USEFUL_SPAN_M:.1f} m the mover needs for its {INSET_M} m "
              f"inset.")
        print("reshape: the ducks will sit on the edge rather than inside it. "
              "That is the clamp preferring the rim to the plaza, not a "
              "failure — but it does not read as crowding.")

    print(f"reshape: {describe(before.extent)}  ->  {describe(target)}")
    send(participant, target)

    deadline = time.time() + 10
    while time.time() < deadline:
        current = read_pond(participant, timeout=0.5)
        if current is not None and all(
                abs(a - b) < 0.01
                for a, b in zip(current.extent.min_xyz + current.extent.max_xyz,
                                target.min_xyz + target.max_xyz)):
            print(f"reshape: the model now says {describe(current.extent)}")
            print("reshape: nothing here told a duck anything — the mover reads "
                  "these bounds and clamps into them.")
            return 0
        time.sleep(0.2)
    print("reshape: the bounds did not change — does the service own the pond?",
          file=sys.stderr)
    return 1


def run(shrink: Optional[float], restore: bool, bounds: Optional[List[float]],
        domain_id: Optional[int] = None) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)

    if restore:
        return apply(participant, Aabb3(min_xyz=list(POND_MIN),
                                        max_xyz=list(POND_MAX)))
    if bounds is not None:
        return apply(participant, Aabb3(
            min_xyz=[bounds[0], bounds[1], POND_MIN[2]],
            max_xyz=[bounds[2], bounds[3], POND_MAX[2]]))

    current = read_pond(participant)
    if current is None:
        print(f"reshape: no {POND_ID} on the bus", file=sys.stderr)
        return 1
    return apply(participant, shrunk(current.extent, shrink or 0.5))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Change the pond's declared bounds and let the model do the rest")
    parser.add_argument("--shrink", type=float, nargs="?", const=0.5, default=None,
                        metavar="FRACTION",
                        help="scale the current bounds about their centre (default 0.5)")
    parser.add_argument("--restore", action="store_true",
                        help="back to the bounds the venue seeded")
    parser.add_argument("--bounds", type=float, nargs=4,
                        metavar=("MIN_X", "MIN_Y", "MAX_X", "MAX_Y"),
                        help="explicit bounds in venue-frame metres")
    parser.add_argument("--domain", type=int, default=None)
    args = parser.parse_args()
    if args.shrink is None and not args.restore and args.bounds is None:
        parser.error("give --shrink, --bounds or --restore")
    return run(args.shrink, args.restore, args.bounds, args.domain)


if __name__ == "__main__":
    sys.exit(main())
