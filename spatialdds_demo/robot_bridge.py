#!/usr/bin/env python3
"""
The robot's northbound half: a sensed pose becomes a model entity.

    python3 -m spatialdds_demo.robot_bridge            # with the built-in sim
    python3 -m spatialdds_demo.robot_bridge --source ros

**This was to have retired on 2026-10-19 in favour of `spatialdds_ros`, and
did not.** The adoption is blocked on one field: the library's `RobotAdapter`
hard-codes `content_refs=[]` with no configuration for it, and this robot's
`catalog:` reference is what makes the page draw a TurtleBot rather than a pin.
There is no demo-side workaround — the library publishes no reference at all,
so no reader can find one. Filed for spatialdds-ros; see
`docs/ros-adoption-blocker.md` for the whole argument and
`docs/ros-adoption-census.md` for the four seams that *are* ready. When
`content_refs` becomes configurable, `RobotBridge`, `already_published`,
`_now` and `yaw_quaternion` retire (247 lines), `PlazaSim` and
`KinematicNavigator` stay as the venue's own geometry, and `_run_kinematic`
shrinks to wiring. The library is already a pinned dependency, so that is one
commit.

**This is a ROS node that speaks SpatialDDS, not a protocol converter.** It
subscribes to what a navigating robot already publishes and publishes
`spatial.owm/0.1` directly.

**What it actually subscribes to, since this said `/amcl_pose` for a while and
meant it as a description of the design rather than of the code.** The ROS
path reads `nav_msgs/Odometry` on `/odom` (see `_run_with_ros`). In the demo's
tier that is map-frame by construction: `robot_tier/plaza_sim_node.py`
publishes `map -> odom` as a *static identity* transform, and says so — "this
sim knows exactly where the robot is, so there is no drift for a localizer to
correct". On a real robot it is not: odom drifts against map, which is the
entire reason AMCL publishes a correction, so reading `/odom` there would
publish a slowly-diverging claim about where the robot sits on the earth with
every number looking plausible. The right input is a map-frame pose — the TF
chain `map -> base_link`, which is what nav2 itself consumes — and that is
what `spatialdds_ros.robot_node` defaults to. This function keeps `/odom`
because it is correct for the simulator it runs in, and retires with the
adoption rather than being half-fixed on its way out. Nothing is translated into an envelope type
and back; there is no new wire mapping in `bridges/ros2_bridge` for this,
because it is a citizen of both worlds rather than a translator between them.

It owns exactly one instance, `ent:robot:tb3`, the way pondwatch owns its
pond, and it writes nothing else. The pose-lane rule relaxes from "the model
service only" to **one writer per key**, which is the rule that was doing the
work all along -- the model service was simply the only owner there was.

**Basis is OBSERVED.** A self-localizing robot is reporting where it has
worked out that it is, from sensors, which is a sighting rather than an
assertion. It is not DERIVED: nothing is fusing other people's claims here,
the robot is looking at the world.

**UNOBSERVED is not absence.** When the upstream feed goes quiet the entity
does not vanish and does not keep claiming its last pose as current: it is
republished with `state = UNOBSERVED` and a reason, so a client can grey it
out and a late joiner learns that this is the last place anyone saw it rather
than where it is. Disposing would say something stronger and false -- that
the robot is gone -- when all we know is that we have stopped hearing from it.

The silence threshold is derived from the observed cadence, not fixed: see
`spatialdds_demo/tempo.py` for why a fixed one produced 94 "it stopped" events
while nothing had stopped.
"""

import argparse
import os
import math
import signal
import sys
import time
from typing import Optional, Tuple

from cyclonedds.domain import DomainParticipant

import spatialdds as sd
from spatialdds_ros.adapter import RobotAdapter, already_published
from spatialdds_ros.config import Config

# The 1.8 frame now, which is the whole of this lane's type-level change: the
# census found one construction site and zero reads of any member, so the four
# members 1.8 added cannot alter what this file does -- they alter what it
# declares, which is that the robot's pose is in a frame that says on the wire
# it is metric.
from spatialdds_demo.model_service import VENUE_FRAME_FQN
from spatialdds_demo.keepout import FREE, build_mask, contributing
from spatialdds_demo.plaza import GROUND_Z, ROBOT_START_XY
# The venue's own scene, the robot being in the Littlefield plaza, and its
# latched profile.
from spatialdds_demo.lanes import MODEL_LATCHED, SCENE

ENTITY_ID = "ent:robot:tb3"
SOURCE_ID = "svc:robot:demo/tb3"

# "mobile robot" -- an automatic machine capable of movement in any given
# environment, `subclass of` (P279) Q11012 (robot). Verified 2026-09-05
# against the entity data, not a search result: several US patents are also
# labelled "Mobile robot" and would have been the wrong pick.
TYPE_MOBILE_ROBOT = "http://www.wikidata.org/entity/Q4810574"

# The one catalogue row the robot renders from. Asset, not instance -- the
# same split the ducks demonstrate, with a fleet of one.
ROBOT_CONTENT_ID = "dcef7c31-1d5f-54aa-9344-899912f9d34a"

# Where the sim starts it, and where the ground is. Defined in
# spatialdds_demo/plaza.py so the ROS side can read them without importing a
# DDS binding it cannot load; re-exported here under the names this module
# has always used.
START_XY = ROBOT_START_XY

# How often the built-in sim reports. A real AMCL runs faster than this; the
# point of the number is that the bridge derives everything from the observed
# cadence rather than from a constant, so changing it changes nothing else.
SIM_HZ = 5.0


def venue_config(domain_id: int) -> Config:
    """
    How this venue configures `spatialdds_ros`.

    Every venue-specific fact the library needs, in one place, so the
    difference between "the library's behaviour" and "this demo's" is a
    function rather than a diff.

    **Anchorless on purpose.** `model_service` publishes the scene's own
    `FrameTransform` for this frame, and the scene keeps the single claim: a
    second publisher would be two services asserting where the venue sits.
    The library's anchorless mode is exactly this, and it publishes no writer
    on the frames lane at all — see `docs/ros-adoption-census.md` seam 2.

    **`content_refs` is what makes the page draw a robot.** Without this
    reference the client has nothing to resolve and renders a marker, which is
    the honest rendering of a robot that has not said what it looks like — and
    it is why the adoption waited for `spatialdds_ros` 0.2.0.
    """
    return Config(
        scene=SCENE,
        entity_id=ENTITY_ID,
        frame_id=VENUE_FRAME_FQN,
        source_id=SOURCE_ID,
        label="TurtleBot",
        type_uri=TYPE_MOBILE_ROBOT,
        content_refs=(f"catalog:{ROBOT_CONTENT_ID}",),
        external_refs=(("wikidata", "Q4810574"),),
        ground_z=GROUND_Z,
        domain=domain_id,
    )


class KinematicNavigator:
    """The dev fallback: a goal is a point the internal sim walks towards."""

    def __init__(self, sim: "PlazaSim"):
        self._sim = sim

    def available(self) -> Optional[str]:
        return None                         # always ready; there is nothing to be

    def send(self, x: float, y: float) -> str:
        self._sim.goal = (x, y)
        return f"the kinematic sim is heading for ({x:.2f}, {y:.2f})"


# How much room the robot keeps around itself, in metres.
#
# Tied to what is *drawn*, not to a real TurtleBot. The asset is scaled up for
# legibility -- 0.14 m across the plates becomes about 2 m on screen -- and a
# demo whose robot visibly overlaps water it is refusing to enter has lost the
# argument regardless of what the arithmetic says. One metre of drawn radius
# plus a little.
#
# **So this is not a physical fact and must not be read as one.** A real
# TurtleBot is 0.14 m across the plates. On the real-robot path the clearance
# is nav2's footprint and inflation radius, which nav2 already owns and
# computes from the robot it is driving — see `robot_tier/nav2_params.yaml`
# and `robot_tier/keepout_node.py`, where the venue's declaration becomes a
# costmap filter and nav2 renders its own clearance around it. The number
# below belongs to this sim and to the asset it draws, and it does not travel:
# `spatialdds_ros` ruled it out of the library for exactly this reason.
CLEARANCE_M = 1.2


def _extent_of(entity) -> tuple:
    """An entity's extent as something comparable; () when it has none."""
    if not entity.has_extent:
        return ()
    return tuple(entity.extent.min_xyz) + tuple(entity.extent.max_xyz)


class PlazaSim:
    """
    A stand-in for a navigating robot, until the ROS tier lands.

    Deliberately kinematic: no physics, no collisions. Part 4's pre-flight
    established that Gazebo has no arm64 build, and that simulating rigid-body
    physics for a flat plaza would be emulating a great deal to reproduce very
    little. What the demo needs from a robot is a pose that moves plausibly and
    a feed that can be cut off; this provides both.
    """

    def __init__(self, x: float = START_XY[0], y: float = START_XY[1],
                 yaw: float = math.pi, speed: float = 0.6):
        self.x, self.y, self.yaw, self.speed = x, y, yaw, speed
        self.goal: Optional[Tuple[float, float]] = None
        #: Why the last goal was dropped, for the caller to report and clear.
        #: `goal is None` alone cannot say: arriving and giving up both clear
        #: it, and a sentinel only one branch sets is not a sentinel.
        self.abandoned: Optional[str] = None
        # The venue's declared keep-out, when the caller has one to give.
        # `None` means "nothing has said where the water is", which is not the
        # same as "there is no water" -- so with no mask this refuses nothing
        # and says so in the log rather than inventing a boundary.
        self.keepout = None

    def blocked(self, x: float, y: float) -> bool:
        """
        Would standing here break the venue's declaration?

        The robot is a disc, not a point, so this asks about its footprint.
        Checking the centre alone let it stop 0.07 m outside the pond's edge
        with a full metre of itself inside the declared water -- legal by the
        letter, and plainly wrong on screen, which is where anyone judges it.

        That is the tier's shoulder lesson arriving here: the law names the
        region, the consumer renders its own clearance. nav2 does it with an
        inflation layer; this does it with eight points on a circle.
        """
        if self.keepout is None:
            return False
        if self.keepout.cell(x, y) != FREE:
            return True
        for i in range(8):
            angle = i * math.pi / 4
            if self.keepout.cell(x + CLEARANCE_M * math.cos(angle),
                                 y + CLEARANCE_M * math.sin(angle)) != FREE:
                return True
        return False

    def step(self, dt: float) -> Tuple[float, float, float]:
        if self.goal is None:
            # Idle is *still*. This used to add 0.25 rad/s of yaw while
            # driving at 0.6 m/s and call itself "a slow patrol along the
            # plaza's edge" -- which is a 2.4 m circle, wherever the robot
            # happened to stop, straight through whatever was there. It went
            # unnoticed because the demo is normally run with the nav2 tier,
            # where this class is not used at all.
            return self.x, self.y, self.yaw

        gx, gy = self.goal
        dx, dy = gx - self.x, gy - self.y
        distance = math.hypot(dx, dy)
        if distance < 0.15:
            self.goal = None
            return self.x, self.y, self.yaw

        yaw = math.atan2(dy, dx)
        step = min(self.speed * dt, distance)
        x = self.x + step * math.cos(yaw)
        y = self.y + step * math.sin(yaw)

        # It refuses to enter; it does not plan around. That distinction is
        # the honest difference between this and the tier: nav2 reads the same
        # declaration and finds a way round it, which is the demonstration.
        # This only declines to break it, which is enough to stop the dev
        # fallback contradicting the venue it is standing in.
        #
        # **And it says so.** Dropping the goal silently was the one thing
        # `handle_command` refuses to do -- it declines a goal nobody will act
        # on precisely so "the requester would [not] watch a robot sit still
        # with no way to learn why" -- and then this abandoned accepted goals
        # without a word. A web client got its 202 and a motionless robot. The
        # reason cannot be known at accept time, because it depends on where
        # the robot is when it meets the boundary, so it has to be reported
        # from here.
        if self.blocked(x, y):
            self.abandoned = (
                f"goal ({gx:.2f}, {gy:.2f}) abandoned at "
                f"({self.x:.2f}, {self.y:.2f}) — a straight line from here "
                f"enters the venue's declared keep-out, and this kinematic "
                f"fallback refuses to enter rather than planning around it. "
                f"Send a waypoint clear of the water first, or run the nav2 "
                f"tier (scripts/run_robot_tier.sh), which plans around the "
                f"same declaration")
            self.goal = None
            return self.x, self.y, self.yaw

        self.yaw, self.x, self.y = yaw, x, y
        return self.x, self.y, self.yaw


def _library_version() -> str:
    import spatialdds_ros
    return spatialdds_ros.__version__


def run(domain_id: Optional[int] = None, source: str = "kinematic",
        hz: float = SIM_HZ) -> int:
    # The library's lanes are on a raw participant, because spatialdds_ros
    # 0.3.0 takes one: a labelled workaround until the ROS rebase (census
    # filing S7). Its domain is read the way the SDK reads it, and refused the
    # same way when it is not set, rather than defaulting to 0.
    if domain_id is None:
        if not os.environ.get(sd.dds.DOMAIN_ENV):
            print(f"robot: no domain: pass --domain or set "
                  f"{sd.dds.DOMAIN_ENV}", file=sys.stderr)
            return 2
        domain_id = int(os.environ[sd.dds.DOMAIN_ENV])
    participant = DomainParticipant(domain_id)

    if source == "ros":
        # **Retired 2026-10-19 in favour of the library's own node**, which
        # does the same job and defaults its pose input to the TF chain
        # `map -> base_link` rather than `/odom`. That difference is the
        # point: this function read `/odom`, which is map-frame only because
        # `robot_tier/plaza_sim_node.py` publishes `map -> odom` as a static
        # identity, and on a localizing robot it would have published a
        # slowly-diverging claim about where the robot sits on the earth.
        # Retiring the function retires the defect, rather than fixing code
        # on its way out.
        print("robot: --source ros has moved to the library's own node, which "
              "reads TF map->base_link instead of /odom:\n"
              "  python3 -m spatialdds_ros.robot_node --ros-args \\\n"
              f"      -p scene:={SCENE} -p entity_id:={ENTITY_ID} \\\n"
              f"      -p frame_id:={VENUE_FRAME_FQN} \\\n"
              f"      -p content_refs:='[catalog:{ROBOT_CONTENT_ID}]'\n"
              "robot_tier/_bringup.sh already runs that.", file=sys.stderr)
        return 2

    config = venue_config(domain_id)
    existing = already_published(participant, config)
    if existing is not None:
        print(f"robot: {ENTITY_ID} is already being published by {existing}. "
              f"Refusing to start: one writer per key, and two of us would "
              f"put one robot in two places.", file=sys.stderr)
        return 1

    return _run_kinematic(participant, domain_id, hz)


def _run_kinematic(participant: DomainParticipant, domain_id: int,
                   hz: float) -> int:
    """
    The dev fallback: no ROS, no nav2, a straight line to the goal.

    Kept because iteration should not pay the nav2 start-up tax, and because
    everything north of the navigator -- cadence, latching, UNOBSERVED,
    declines -- is identical in both modes. It drives in straight lines
    rather than planning, which is why the tier is still the demonstration --
    but it reads the venue's declaration off the same bus and declines to
    cross it, so the fallback no longer contradicts the world it stands in.
    """
    config = venue_config(domain_id)
    sim = PlazaSim()
    # **The library owns the lanes; this module owns the geometry.** The seam
    # is the `Navigator` protocol plus `observed()`/`refused()`, so the sim,
    # its mask, its clearance and its abandonment message never cross into
    # the library — see `docs/ros-adoption-census.md` seam 1. Behaviour is
    # identical because it is the same code, on both sides of the seam.
    adapter = RobotAdapter(participant, config,
                           navigator=KinematicNavigator(sim))

    # The venue's law, read off the latched topic the way any consumer reads
    # it -- so a reshape reaches this robot too, with nothing here that knows
    # what a pond is.
    # **The library's transport, not this repository's: one convention per
    # topic, for a lane the library owns.** Both apply the same §3.3.3
    # profile and the two `QosProfile.to_qos()` results are identical, so
    # either would match on the wire. Reading a library-owned lane with the
    # library's own transport keeps that from being something to re-check.
    #
    # It was a hard collision until spatialdds-ros 0.2.1. The library put the
    # profile on its `Topic` objects as well as its endpoints, and a topic's
    # QoS must agree among every live topic object for that name, so this
    # reader was refused with `DDS_RETCODE_INCONSISTENT_POLICY`. The library
    # now creates topics with default QoS, as `typed_transport.get_topic`
    # always has, and the conventions agree.
    #
    # Since the SDK adoption the venue's lane is read through the SDK, on an
    # SDK participant of its own beside the library's: the SDK's Entity class
    # and the library's are two Python classes for one topic, and two
    # classes in one participant is that same collision. Two participants in
    # one process interoperate (measured, docs/sdk-adoption-census.md), and the
    # profile's columns are the library's.
    venue = sd.participant(SCENE, domain=domain_id)
    entities_reader = venue.reader("model_entity", qos_profile=MODEL_LATCHED)
    known: dict = {}
    # The contributing extents as last rasterised; None until the first read,
    # which is a different statement from "nothing contributes" (an empty
    # tuple) and has to stay distinguishable from it.
    law = None

    def refresh_keepout():
        for ev in entities_reader.take():
            if isinstance(ev, sd.Dispose):
                known.pop(ev.key, None)       # a withdrawn thing keeps no one out
                continue
            if not isinstance(ev, sd.Sample) or ev.key == ENTITY_ID:
                continue                      # never keep yourself out
            known[ev.key] = ev.data

        # **Only a contributing extent can change the law.** Diffing every
        # entity's extent reported a change on almost every tick: the venue
        # republishes `ent:pond:observed` continuously, and a DERIVED
        # observation of the water is exactly what must *not* be able to move
        # a keep-out. Measured on one session before this: 455 "the law
        # changed" lines, all of them reporting the same 41463 cells, against
        # 112 pose heartbeats. A real reshape would have been invisible in it.
        #
        # The signature is taken from `contributing()` -- the same function
        # the rasteriser uses -- so the thing being watched for change and the
        # thing being rasterised cannot drift apart. It also catches what the
        # extent diff could not: an entity that stops contributing at all,
        # because it was retired or its basis changed, alters the signature
        # without any extent moving.
        nonlocal law
        signature = tuple(sorted(
            (e.entity_id, _extent_of(e))
            for e in contributing(list(known.values()),
                                  exclude_ids=(ENTITY_ID,))))
        if signature == law:
            return None
        law = signature
        sim.keepout = build_mask(list(known.values()), exclude_ids=(ENTITY_ID,))
        if sim.keepout is None:
            return "no declared keep-out on the bus — refusing nothing"
        return (f"keep-out from {', '.join(sim.keepout.contributors)} "
                f"({sim.keepout.occupied_cells} cells)")

    for note in config.startup_notes():
        print(f"robot: {note}", flush=True)
    print(f"robot: owns {ENTITY_ID} through spatialdds_ros "
          f"{_library_version()}; straight lines, declines declared cells",
          flush=True)
    # The library's anchorless note says another participant may own the
    # georeference. Here one does, and saying so is the demo's business
    # rather than the library's — it cannot see who else is publishing.
    print(f"robot: the venue publishes the georeference for "
          f"{VENUE_FRAME_FQN}; this robot runs anchorless so the scene keeps "
          f"the single claim", flush=True)

    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    interval = 1.0 / hz
    reported = 0
    while not stop:
        note = refresh_keepout()
        if note:
            print(f"robot: {note}", flush=True)
        for note in adapter.poll_commands():
            print(f"robot: {note}", flush=True)
        x, y, yaw = sim.step(interval)
        abandoned = sim.abandoned
        if abandoned:
            print(f"robot: {abandoned}", flush=True)
            sim.abandoned = None
        adapter.observed(x, y, yaw)
        if abandoned:
            # After `observed`, so the amended record is the one carrying the
            # current pose rather than being overwritten by it a line later.
            adapter.refused(abandoned)
        note = adapter.tick()
        if note:
            print(f"robot: {note}", flush=True)
        reported += 1
        if reported % (int(hz) * 20) == 0:
            print(f"robot: {reported} poses; at ({x:.2f}, {y:.2f})", flush=True)
        time.sleep(interval)
    print("robot: stopping.")
    venue.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a robot's sensed pose into the world model")
    parser.add_argument("--domain", type=int, default=None)
    parser.add_argument("--source", choices=("ros", "kinematic"),
                        default="kinematic",
                        help="kinematic: the venue's own straight-line sim, "
                             "which honours the declared keep-out. ros: "
                             "moved to spatialdds_ros.robot_node; this prints "
                             "the invocation and exits.")
    parser.add_argument("--hz", type=float, default=SIM_HZ)
    args = parser.parse_args()
    return run(args.domain, args.source, args.hz)


if __name__ == "__main__":
    sys.exit(main())
