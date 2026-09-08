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

**What it reads, and from where.** Two topics, both SpatialDDS:

* `spatialdds/model/entity/v1` -- latched -- for the box it keeps ducks in.
  A declaration, published by the venue's model service.
* `spatialdds/model/pose/v1` -- best-effort, keep-last-one -- for where the
  robot currently is. An observation, published by `robot_bridge` running
  inside the ROS tier, which is the process that reads `/odom` off the ROS
  graph and owns `ent:robot:tb3` on this bus.

That second one is the only place in this demo where a consumer reacts to
another *service's* live measurement rather than to a venue's declaration,
and it is worth being exact about the path: ROS `/odom` -> robot bridge ->
`oarc.model_pose` on the wire -> here. No ROS message type is imported by
this file and no duck is published to ROS. The two buses meet at exactly one
process, which is the one that owns the key.

"""

import argparse
import math
import random
import signal
import sys
import time
import zlib
from typing import Dict, List, Optional, Tuple

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.model_service import TYPE_RUBBER_DUCK
from spatialdds_demo.qos_profiles import (
    MODEL_COMMAND, MODEL_FAST, MODEL_LATCHED)
from spatialdds_demo.topics import (
    TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1, TOPIC_MODEL_POSE_V1)
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity, ModelCommand, ModelPose
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

REQUESTER_ID = "svc:mover:demo/ducks"

# The pond entity whose bounds are followed. Named rather than discovered by
# type, because "which water are these ducks on" is a question the model
# cannot answer yet: there is no `on` relationship, and Part 3 leaves
# computed containment alone until Relationship can carry a basis.
#
# Two entities describe this water: the venue's declared bounds and a fusion
# service's observed ones. They disagree, and the model does not settle it --
# it carries both, each saying who published it and how it was arrived at.
# **Which one to believe is a policy this consumer holds**, which is why it is
# a command-line flag and not a constant. Run the mover against another and
# the ducks roam differently; nothing else changes.
#
# The default is the shallows, and the reason is worth stating because it is
# the same point from the other side. Both pond boxes are claims about the
# *water*, and the water is a U with an island in it, so a rectangle round it
# contains a good deal of dry paving -- a duck walk clamped into the declared
# pond spends about a third of its time on stone. The shallows are the box
# the venue drew for exactly this job. Following the pond is not wrong, it is
# answering a question nobody asked here; `--bounds declared` will show you
# what it looks like.
BOUNDS_ENTITIES = {
    "shallows": "ent:shallows:littlefield",  # where the venue keeps its ducks
    "declared": "ent:pond:littlefield",      # the venue's water
    "derived": "ent:pond:observed",          # a service measured that water
}
DEFAULT_BOUNDS_ENTITY = BOUNDS_ENTITIES["shallows"]

# Kept off the declared edge. The pond's bounds are where the water stops, and
# a duck is not a point -- placing one exactly on the boundary puts half of it
# on the rim. One metre is about a duck.
INSET_M = 1.0

# How far a duck travels per move, and how often any duck moves. Six moves a
# second shared between three ducks is a turn each every half second, at half
# a metre a step -- about 1 m/s, which is roughly how fast a duck swims. Small
# and often reads better than large and rare, and it keeps each entity's
# update interval well inside the idle threshold that decides when the
# service considers it stopped.
STEP_M = 0.5
MOVES_PER_SECOND = 6.0

# The one thing in this service that reacts to something alive.
#
# Everything else here follows a *declaration*: a box the venue published,
# read off the latched entity topic. This reads a **live observation** made
# by a different process -- the robot bridge, in the ROS tier, turning
# `/odom` into poses on `spatialdds/model/pose/v1`. Nothing is shared but the
# bus: no ROS types reach this process and no duck reaches ROS. A duck backs
# off because a robot said where it was, on the same wire the pond's bounds
# came in on.
#
# Nothing here knows what a robot is, either. It is the entity id the demo
# happens to care about, and the radius is in metres because that is what
# poses are in. A second robot published under another id would be ignored,
# which is honest: this service was told about one.
AVOID_ENTITY_ID = "ent:robot:tb3"

# Ten metres, which is a lot for a duck and is set by the geometry rather
# than by taste. The robot is kept out of the shallows and out of the water
# around them, so it *cannot* come near: its route round the south rim runs
# about 4.2 m from the closest a duck is allowed to be, and even cutting
# across the top of the shallows after a reshape it stays about 2.1 m off.
# A radius smaller than the distance the robot can actually achieve is a
# behaviour that never fires -- the first version was 4 m and never once did.
#
# How near the robot has to be to matter at all, and the only geometry in
# the rule: inside this, a duck simply swims away from it; outside, the
# robot may as well not exist.
#
# Fifteen metres, because the thing it has to cover is not "how close the
# robot gets" but "how far a duck can get". The water the ducks may use is
# 8.5 x 6 m, so a duck fleeing to the corner furthest from a robot on the
# south rim ends up about 13.5 m from it. Anything smaller and the push
# stops partway: the duck coasts out of range, wanders back in, gets pushed
# again, and settles a couple of metres short of the corner, milling about.
# That is what a graded falloff produced, and it looked like indecision.
#
# Two earlier attempts are worth recording because both were reasonable and
# both were wrong. A 4 m radius never fired at all -- the robot is kept out
# of the water and cannot come nearer than about 4.2 m. A 12 m radius with a
# squared falloff did fire, and put the ducks at an equilibrium where the
# push balanced their wander, which is a hover rather than a retreat.
AVOID_RADIUS_M = 15.0

# How far from the corner a duck is allowed to settle, so that three of them
# make a group rather than a stack.
CORNER_SPREAD_M = 1.6

# How much of the wander a fleeing duck gives up. Not all of it: with none
# left, three ducks steering for the same corner converge on one point and
# sit there in a stack, which reads as a rendering bug rather than as a
# flock.
AVOID_URGENCY = 0.75

# A pose stops meaning "where it is" once it is old. The fast lane is
# BEST_EFFORT, so samples are dropped rather than queued, and a robot that
# stopped publishing leaves its last one sitting here forever. Ducks should
# not go on avoiding a ghost.
AVOID_STALE_S = 3.0


# How much a duck can turn between steps, in radians.
#
# This matters more than the speed did. A direction drawn uniformly every step
# is a random walk, and a random walk does not go anywhere: expected
# displacement grows with the square root of the number of steps, so three
# ducks stepping half a metre six times a second spend a minute vibrating
# around where they started. It reads as jitter, not as swimming, and the
# demonstration -- that these things live in a world and move through it --
# does not survive looking like a rendering artefact.
#
# Keeping the heading and perturbing it makes the same step size travel. A
# duck holds its course, wanders off it gradually, and crosses the pond.
TURN_RADIANS = 0.45


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now % 1) * 1e9))


def farthest_corner(bounds: Aabb3, away_from: Tuple[float, float],
                    entity_id: str = "", inset: float = INSET_M
                    ) -> Tuple[float, float]:
    """
    The corner of the usable water that is furthest from a point, nudged by
    a stable per-entity offset.

    Usable, so inset the same way `clamp_into` insets: aiming at a corner a
    duck is not allowed to reach would leave it pressed against the boundary
    nearest that corner, which is where the naive version already put it.

    The offset is what stops three ducks steering for one point from ending
    up in a stack -- measured spread was two centimetres, which on screen is
    one duck with a shadow. Derived from the id rather than drawn, so a duck
    always makes for its own bit of the corner and the arrangement is the
    same every time the robot comes back.
    """
    lo_x, hi_x = bounds.min_xyz[0] + inset, bounds.max_xyz[0] - inset
    lo_y, hi_y = bounds.min_xyz[1] + inset, bounds.max_xyz[1] - inset
    if lo_x > hi_x:
        lo_x = hi_x = (bounds.min_xyz[0] + bounds.max_xyz[0]) / 2
    if lo_y > hi_y:
        lo_y = hi_y = (bounds.min_xyz[1] + bounds.max_xyz[1]) / 2
    corners = [(x, y) for x in (lo_x, hi_x) for y in (lo_y, hi_y)]
    corner = max(corners, key=lambda c: math.hypot(c[0] - away_from[0],
                                                   c[1] - away_from[1]))
    if not entity_id:
        return corner
    # Inward from the corner, never outward: an offset that pushes past the
    # boundary gets clamped back to it, and two ducks whose offsets both
    # clamp land on the same point -- which is the stack this exists to
    # avoid, reintroduced by the fix for it.
    seed = zlib.crc32(entity_id.encode())
    inward_x = -1.0 if corner[0] == hi_x else 1.0
    inward_y = -1.0 if corner[1] == hi_y else 1.0
    nudge_x = inward_x * (seed & 0xFF) / 255.0 * CORNER_SPREAD_M
    nudge_y = inward_y * ((seed >> 8) & 0xFF) / 255.0 * CORNER_SPREAD_M
    return (min(max(corner[0] + nudge_x, lo_x), hi_x),
            min(max(corner[1] + nudge_y, lo_y), hi_y))


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

    # A class-level default, because the geometry tests build this with
    # `__new__` to get at `step_for` without a bus, and an attribute that
    # only exists after `__init__` turns "the mover does not need a bus for
    # this" into an AttributeError in a test about headings.
    _avoid: Optional[Tuple[float, float, float]] = None

    def __init__(self, participant: DomainParticipant,
                 bounds_entity: str = DEFAULT_BOUNDS_ENTITY,
                 rng: Optional[random.Random] = None):
        self._reader = tt.make_reader(
            participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        # The fast lane, where things that move say so. Same bus, different
        # QoS: BEST_EFFORT and KEEP_LAST(1), because a stale pose is worse
        # than a missing one.
        self._poses = tt.make_reader(
            participant, TOPIC_MODEL_POSE_V1, ModelPose, MODEL_FAST.name)
        self._commands = tt.make_writer(
            participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
        self._bounds_entity = bounds_entity
        self._rng = rng or random.Random()
        self._entities: Dict[str, Entity] = {}
        self._counter = 0
        self._matched = False
        self._warned_unheard = False
        self._heading: Dict[str, float] = {}
        self._avoid: Optional[Tuple[float, float, float]] = None

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
        for pose in tt.take_samples(self._poses) or []:
            # Anything we already know about gets its live pose applied. This
            # matters more than it looks: the latched entity topic only
            # republishes every few moves, so reading positions from it alone
            # meant every step was computed from where a duck had been up to
            # a couple of seconds ago. The duck still drifted -- each request
            # is an absolute pose and the last one wins -- but at about a
            # fifth of the intended speed, and a *directed* push barely
            # accumulated at all. Ducks avoiding a robot crawled sideways
            # instead of crossing to the far corner, which is exactly what
            # a demonstration of "the model is the interface" must not do.
            known = self._entities.get(pose.entity_id)
            if known is not None and known.has_pose:
                known.pose = pose.pose
            if pose.entity_id == AVOID_ENTITY_ID:
                now = time.time()
                if self._avoid is None:
                    # Once, and it says where the pose came from rather than
                    # that one arrived: the interesting part is that this is
                    # another service's observation, off the same bus.
                    print(f"mover: keeping clear of {AVOID_ENTITY_ID} — poses "
                          f"from {pose.source_id} on {TOPIC_MODEL_POSE_V1}",
                          flush=True)
                self._avoid = (pose.pose.t[0], pose.pose.t[1], now)
        return applied

    def avoiding(self, now: Optional[float] = None
                 ) -> Optional[Tuple[float, float]]:
        """
        Where the thing to keep away from is, if anyone has said recently.

        The staleness check is the whole of the honesty here: this is a
        BEST_EFFORT lane, so silence and "still there" look identical, and a
        robot whose bridge died would otherwise keep three ducks pinned
        against the far side of the shallows for the rest of the afternoon.
        """
        if self._avoid is None:
            return None
        x, y, heard = self._avoid
        if (now or time.time()) - heard > AVOID_STALE_S:
            return None
        return x, y

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
        """
        Where this duck should drift to next, clamped into the model's box.

        It keeps a heading and wanders off it, rather than choosing a fresh
        direction each time. See TURN_RADIANS: the second is a random walk and
        goes nowhere; the first is swimming.

        When the clamp pulls it back -- it reached the edge of whatever water
        it is following -- the heading is turned right around rather than left
        pressed against the boundary, so it leaves the edge instead of
        grinding along it.
        """
        heading = self._heading.get(duck.entity_id)
        if heading is None:
            heading = self._rng.uniform(0, 2 * math.pi)
        wander = self._rng.uniform(-TURN_RADIANS, TURN_RADIANS)
        heading += wander

        # Something alive is nearby, so lean away from it. Blended into the
        # heading rather than replacing it: a duck that swung to exactly
        # away-from-the-robot every step would move like a compass needle,
        # not like a duck deciding it would rather be elsewhere.
        avoid_weight = 0.0
        near = self.avoiding()
        if near is not None:
            distance = math.hypot(duck.pose.t[0] - near[0],
                                  duck.pose.t[1] - near[1])
            if distance < AVOID_RADIUS_M:
                # Not "away from it" -- *as far from it as this water gets*.
                #
                # Swimming directly away is the obvious rule and it does not
                # work, for a reason worth writing down. A duck pushed north
                # reaches the northern edge and stops: the away-vector is
                # then perpendicular to the wall, the clamp eats all of it,
                # and nothing suggests going east or west. The ducks ended up
                # on the near side of the far wall, which is a local answer
                # to a global question. Steering for the furthest corner of
                # the water is the question they were actually being asked.
                target = farthest_corner(bounds, near, duck.entity_id)
                want = math.atan2(target[1] - duck.pose.t[1],
                                  target[0] - duck.pose.t[0])
                delta = (want - heading + math.pi) % (2 * math.pi) - math.pi
                heading += AVOID_URGENCY * delta
                avoid_weight = AVOID_URGENCY
                # Most of the wander goes, but not all of it: three ducks
                # with none left converge on one point and sit there in a
                # stack. A little keeps them a flock in a corner.
                heading -= wander * AVOID_URGENCY

        wanted_x = duck.pose.t[0] + STEP_M * math.cos(heading)
        wanted_y = duck.pose.t[1] + STEP_M * math.sin(heading)
        x, y = clamp_into(wanted_x, wanted_y, bounds)
        clamped = abs(x - wanted_x) > 1e-9 or abs(y - wanted_y) > 1e-9
        # Turning around at the edge is right for a duck that has simply
        # swum into the boundary, and wrong for one that is being pushed
        # into it: reversing sends it straight back at the thing it is
        # avoiding, which it then turns away from again. The result was a
        # duck hovering a couple of metres short of the far corner rather
        # than going to it, because `clamp_into` bounds each axis on its
        # own -- so a heading that points into a wall still slides *along*
        # it, into the corner, if it is left alone.
        if clamped and avoid_weight < 0.5:
            heading += math.pi + self._rng.uniform(-TURN_RADIANS, TURN_RADIANS)
        self._heading[duck.entity_id] = heading % (2 * math.pi)

        return PoseSE3(
            t=[x, y, duck.pose.t[2]],
            q=heading_quaternion(x - duck.pose.t[0], y - duck.pose.t[1],
                                 duck.pose.q))

    def _wait_until_somebody_is_listening(self, timeout: float = 5.0) -> bool:
        """
        The command lane is VOLATILE: a sample written before a reader has
        matched is dropped on the floor, not queued.

        Both operator tools already wait for this and the mover did not, which
        cost nothing in the running demo -- it asks again half a second later,
        forever -- and cost a test everything, because a mover started, asked
        thirty times and had its first commands land in a void. A service
        that silently loses its opening move is the same bug as a tool that
        reports success without looking; it is just quieter about it.

        Once only: after the first match there is a reader, and losing it is
        the service's problem to notice, not this loop's to re-check.
        """
        if self._matched:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._commands.get_publication_matched_status().current_count:
                self._matched = True
                return True
            time.sleep(0.05)
        return False

    def ask_move(self, entity_id: str, pose: PoseSE3) -> None:
        if not self._wait_until_somebody_is_listening():
            # Nothing is reading commands. Saying so once is better than
            # asking into silence for the rest of the process's life.
            if not self._warned_unheard:
                self._warned_unheard = True
                print("mover: nothing is reading the command lane — is the "
                      "model service running?", flush=True)
            return
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
    parser.add_argument("--bounds", choices=sorted(BOUNDS_ENTITIES),
                        default="shallows",
                        help="which box to keep the ducks in (shallows: the "
                             "venue's duck water; declared: the venue's whole "
                             "pond; derived: pondwatch's measurement of it)")
    parser.add_argument("--bounds-entity", default=None,
                        help="or name an entity directly")
    args = parser.parse_args()
    entity = args.bounds_entity or BOUNDS_ENTITIES[args.bounds]
    return run(args.domain, entity)


if __name__ == "__main__":
    sys.exit(main())
