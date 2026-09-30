#!/usr/bin/env python3
"""
The robot's northbound half: a sensed pose becomes a model entity.

    python3 -m spatialdds_demo.robot_bridge            # with the built-in sim
    python3 -m spatialdds_demo.robot_bridge --source ros

**This is a ROS node that speaks SpatialDDS, not a protocol converter.** It
subscribes to what a navigating robot already publishes (`/amcl_pose`) and
publishes `oarc_model` directly. Nothing is translated into an envelope type
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
import math
import signal
import sys
import time
from typing import List, Optional, Tuple

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.model_service import venue_frame
from spatialdds_demo.keepout import FREE, build_mask
from spatialdds_demo.plaza import GROUND_Z, ROBOT_START_XY
from spatialdds_demo.qos_profiles import (
    MODEL_COMMAND, MODEL_FAST, MODEL_LATCHED,
)
from spatialdds_demo.tempo import Tempo
from spatialdds_demo.topics import (
    TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1, TOPIC_MODEL_POSE_V1,
)
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import (
    Basis, Entity, LifecycleState, ModelCommand, ModelLayer, ModelPose,
)
from spatialdds_idl.spatial.common import KV
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

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


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now % 1) * 1e9))


def yaw_quaternion(yaw: float) -> list:
    """Identity faces east in this frame; see the seeder's comments."""
    return [0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)]


class KinematicNavigator:
    """The dev fallback: a goal is a point the internal sim walks towards."""

    def __init__(self, sim: "PlazaSim"):
        self._sim = sim

    def available(self) -> Optional[str]:
        return None                         # always ready; there is nothing to be

    def send(self, x: float, y: float) -> str:
        self._sim.goal = (x, y)
        return f"the kinematic sim is heading for ({x:.2f}, {y:.2f})"


class RobotBridge:
    """
    Owns `ent:robot:tb3`. Publishes its latched record and its fast poses.

    No ROS in here: it is handed poses and handed a navigator. In the tier the
    caller feeds it `/odom` and gives it a nav2 action client; in the dev
    fallback the caller feeds it a kinematic sim and gives it that sim's goal
    setter. Both call the same two methods, which keeps the interesting
    behaviour -- cadence, latching, going quiet, declining -- testable without
    a robot or a ROS installation.
    """

    def __init__(self, participant: DomainParticipant, navigator=None):
        self._entities = tt.make_writer(
            participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        self._poses = tt.make_writer(
            participant, TOPIC_MODEL_POSE_V1, ModelPose, MODEL_FAST.name)
        self._commands = tt.make_reader(
            participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
        self._tempo = Tempo()
        self._goal: Optional[Tuple[float, float]] = None
        self._entity: Optional[Entity] = None
        self._silent = False
        self._last_seen = 0.0
        # Whoever actually drives. `None` means the bridge accepts goals and
        # records them, which is what the unit tests exercise.
        self._navigator = navigator

    # --- what it publishes ------------------------------------------------

    def _record(self, pose: PoseSE3, state: LifecycleState,
                reason: str = "") -> Entity:
        return Entity(
            entity_id=ENTITY_ID,
            basis=Basis.OBSERVED,
            type_uris=[TYPE_MOBILE_ROBOT],
            layer=ModelLayer.FAST,
            frame_ref=venue_frame(),
            has_pose=True,
            pose=pose,
            has_extent=False,
            extent=Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0]),
            properties=[KV(key="demo.label", value="TurtleBot"),
                        KV(key="demo.note",
                           value="A self-localizing robot reporting where it believes it is. OBSERVED because it is looking at the world, not fusing other people's claims about it.")],
            external_refs=[],
            content_refs=[f"catalog:{ROBOT_CONTENT_ID}"],
            state=state,
            state_reason=reason,
            source_id=SOURCE_ID,
            stamp=_now(),
        )

    def observed(self, x: float, y: float, yaw: float,
                 now: Optional[float] = None) -> None:
        """A new pose from upstream. Fast lane always; latch on cadence."""
        now = time.time() if now is None else now
        pose = PoseSE3(t=[x, y, GROUND_Z], q=yaw_quaternion(yaw))
        first = self._entity is None
        was_silent = self._silent
        self._entity = self._record(pose, LifecycleState.ACTIVE)
        self._last_seen = now
        self._silent = False

        self._poses.write(ModelPose(entity_id=ENTITY_ID, pose=pose,
                                    source_id=SOURCE_ID, stamp=self._entity.stamp))
        # The record is written on the cadence -- and unconditionally when the
        # robot first appears or comes back, because those are changes of
        # *state*, not of position, and a client waiting four more poses to
        # find out the robot is alive again would be told late.
        if self._tempo.observed(ENTITY_ID, now) or first or was_silent:
            self._entities.write(self._entity)

    # --- what it is asked to do -------------------------------------------

    def handle_command(self, command: ModelCommand) -> Optional[str]:
        """
        `goto`, and nothing else. Returns a line for the log, or None when the
        command was not addressed to us.

        Silence for somebody else's subject, for the same reason the model
        service is silent about the robot: the lane has several owners and a
        refusal from the wrong one reads as the system disagreeing with
        itself. A malformed command *about the robot* is a different matter
        and gets a reason.

        What it reports is what was accepted, not what was sent. A goal the
        robot cannot take -- because nothing is publishing its pose, so there
        is nothing to navigate from -- is declined here rather than queued
        silently for a robot that may never come back.
        """
        if command.subject_id != ENTITY_ID:
            return None
        if command.verb != "goto":
            return None
        if not command.has_pose:
            # A zeroed pose is a well-formed request to drive to the frame
            # origin. Declining is the only reading that cannot be mistaken
            # for obedience -- the same ethic as set_extent's guard.
            return f"declined: goto for {ENTITY_ID} carried no pose"
        if self._entity is None or self._silent:
            return (f"declined: goto for {ENTITY_ID} — no pose from "
                    f"{SOURCE_ID}, so there is nothing to navigate from")

        # The other half of "nothing to navigate from": the robot may be
        # reporting its pose perfectly while the thing that would drive it is
        # not accepting goals. Declining with that reason beats accepting a
        # goal nobody will act on -- the requester would watch a robot sit
        # still with no way to learn why.
        if self._navigator is not None:
            unavailable = self._navigator.available()
            if unavailable:
                return (f"declined: goto for {ENTITY_ID} — {unavailable}")

        x, y = command.pose.t[0], command.pose.t[1]
        self._goal = (x, y)
        if self._navigator is None:
            return (f"accepted goto ({x:.2f}, {y:.2f}) "
                    f"(asked by {command.requester_id})")
        # What the navigator accepted, not what was sent. If nav2 rounds,
        # clamps or refuses, that is what belongs in the log.
        outcome = self._navigator.send(x, y)
        return f"{outcome} (asked by {command.requester_id})"

    def poll_commands(self) -> List[str]:
        """Drain the command lane. The caller decides what to do with the
        notes; the goal is already set."""
        notes = []
        try:
            batch = tt.take_samples(self._commands) or []
        except Exception as error:
            # Untrusted traffic, the same as every other reader here.
            return [f"command lane read failed: {error!r}"]
        for command in batch:
            note = self.handle_command(command)
            if note:
                notes.append(note)
        return notes

    @property
    def goal(self) -> Optional[Tuple[float, float]]:
        return self._goal

    def clear_goal(self) -> None:
        self._goal = None

    def tick(self, now: Optional[float] = None) -> Optional[str]:
        """
        Housekeeping between poses. Returns what it did, for the log.

        Two jobs: flush the latch when the robot stops moving, and declare it
        UNOBSERVED when we stop hearing from it at all.
        """
        now = time.time() if now is None else now
        if self._entity is None:
            return None

        if not self._silent and now - self._last_seen > self.silence_threshold():
            self._silent = True
            quiet_for = now - self._last_seen
            self._entity = self._record(
                self._entity.pose, LifecycleState.UNOBSERVED,
                f"no pose from {SOURCE_ID} for {quiet_for:.1f}s; this is where "
                f"it was last seen, not where it is")
            self._entities.write(self._entity)
            self._tempo.forget(ENTITY_ID)
            return f"unobserved after {quiet_for:.1f}s of silence"

        if not self._silent and self._tempo.idle(now):
            self._entities.write(self._entity)
            return "latch flushed — it stopped moving"
        return None

    def silence_threshold(self) -> float:
        """Derived from the feed's own cadence, with a floor."""
        return self._tempo.threshold(ENTITY_ID)

    @property
    def state(self) -> str:
        return "UNOBSERVED" if self._silent else "ACTIVE"


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
        # The venue's declared keep-out, when the caller has one to give.
        # `None` means "nothing has said where the water is", which is not the
        # same as "there is no water" -- so with no mask this refuses nothing
        # and says so in the log rather than inventing a boundary.
        self.keepout = None

    def blocked(self, x: float, y: float) -> bool:
        """Would standing here break the venue's declaration?"""
        return self.keepout is not None and self.keepout.cell(x, y) != FREE

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
        if self.blocked(x, y):
            self.goal = None
            return self.x, self.y, self.yaw

        self.yaw, self.x, self.y = yaw, x, y
        return self.x, self.y, self.yaw


def already_published(participant: DomainParticipant,
                      settle: float = 2.5) -> Optional[str]:
    """
    Is somebody else already publishing this robot?

    One writer per key is the rule the whole model rests on, and this is the
    one place two processes could plausibly break it: the demo stack can start
    a bridge with its own kinematic sim, and the robot tier starts one driven
    by nav2. Two of them would put one robot in two places -- the exact
    two-writers state Part 2 spent itself closing -- and the symptom would be
    a robot that teleports rather than an error anyone could read.

    So a bridge looks before it writes, and refuses rather than joins.
    """
    reader = tt.make_reader(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
    deadline = time.time() + settle
    while time.time() < deadline:
        for sample in tt.take_samples(reader) or []:
            if sample.entity_id == ENTITY_ID:
                return sample.source_id
        time.sleep(0.05)
    return None


def run(domain_id: Optional[int] = None, source: str = "ros",
        hz: float = SIM_HZ) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)

    existing = already_published(participant)
    if existing is not None:
        print(f"robot: {ENTITY_ID} is already being published by {existing}. "
              f"Refusing to start: one writer per key, and two of us would "
              f"put one robot in two places.", file=sys.stderr)
        return 1

    if source == "ros":
        return _run_with_ros(participant, domain_id)
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
    sim = PlazaSim()
    bridge = RobotBridge(participant, navigator=KinematicNavigator(sim))

    # The venue's law, read off the latched topic the way any consumer reads
    # it -- so a reshape reaches this robot too, with nothing here that knows
    # what a pond is.
    entities_reader = tt.make_reader(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
    known: dict = {}

    def refresh_keepout():
        changed = False
        for sample in tt.take_samples(entities_reader) or []:
            if sample.entity_id == ENTITY_ID:
                continue                      # never keep yourself out
            before = known.get(sample.entity_id)
            known[sample.entity_id] = sample
            if before is None or _extent_of(before) != _extent_of(sample):
                changed = True
        if not changed:
            return None
        sim.keepout = build_mask(list(known.values()), exclude_ids=(ENTITY_ID,))
        if sim.keepout is None:
            return "no declared keep-out on the bus — refusing nothing"
        return (f"keep-out from {', '.join(sim.keepout.contributors)} "
                f"({sim.keepout.occupied_cells} cells)")

    print(f"robot: domain {domain_id}, source {SOURCE_ID} — kinematic mode")
    print(f"robot: owns {ENTITY_ID}; straight lines, declines declared cells")

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
        for note in bridge.poll_commands():
            print(f"robot: {note}", flush=True)
        x, y, yaw = sim.step(interval)
        bridge.observed(x, y, yaw)
        note = bridge.tick()
        if note:
            print(f"robot: {note}", flush=True)
        reported += 1
        if reported % (int(hz) * 20) == 0:
            print(f"robot: {reported} poses; at ({x:.2f}, {y:.2f})", flush=True)
        time.sleep(interval)
    print("robot: stopping.")
    return 0


def _run_with_ros(participant: DomainParticipant, domain_id: int) -> int:
    """
    The tier: one process holding both stacks, end to end.

    It reads `/odom` from ROS, owns `ent:robot:tb3` on the SpatialDDS bus,
    takes `goto` off the command lane, and issues `NavigateToPose` to nav2 --
    natively on both sides, with nothing relayed. `bridges/ros2_bridge` has
    held rclpy and a CycloneDDS participant in one process since long before
    this, and this follows it.

    rclpy is imported here rather than at module scope so the rest of this
    file stays importable on a machine with no ROS, which is where its tests
    run.
    """
    import rclpy
    from geometry_msgs.msg import PoseStamped
    from nav2_msgs.action import NavigateToPose
    from nav_msgs.msg import Odometry
    from rclpy.action import ActionClient
    from rclpy.node import Node

    from spatialdds_demo.plaza import GROUND_Z as PLAZA_Z

    class Nav2Navigator:
        """nav2, asked properly and reported honestly."""

        def __init__(self, node):
            self._node = node
            self._client = ActionClient(node, NavigateToPose, "navigate_to_pose")

        def available(self) -> Optional[str]:
            if not self._client.server_is_ready():
                # One reason, said plainly. "nav2 is down" and "nav2 is up but
                # inactive" look identical from here and lead to the same
                # place: nothing will drive.
                return "nav2 is not accepting goals (no navigate_to_pose server)"
            return None

        def send(self, x: float, y: float) -> str:
            goal = NavigateToPose.Goal()
            goal.pose.header.frame_id = "map"
            goal.pose.header.stamp = self._node.get_clock().now().to_msg()
            goal.pose.pose.position.x = x
            goal.pose.pose.position.y = y
            goal.pose.pose.position.z = PLAZA_Z
            goal.pose.pose.orientation.w = 1.0
            self._client.send_goal_async(goal)
            return f"nav2 accepted a goal at ({x:.2f}, {y:.2f})"

    class RobotBridgeNode(Node):
        def __init__(self):
            super().__init__("spatialdds_robot_bridge")
            self._bridge = RobotBridge(participant,
                                       navigator=Nav2Navigator(self))
            self.create_subscription(Odometry, "/odom", self._on_odom, 20)
            self.create_timer(0.1, self._tick)
            self.get_logger().info(
                f"robot: owns {ENTITY_ID} on SpatialDDS domain {domain_id}, "
                f"reading /odom and driving through nav2 — one process, "
                f"both stacks")

        def _on_odom(self, msg) -> None:
            q = msg.pose.pose.orientation
            yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                             1.0 - 2.0 * (q.y * q.y + q.z * q.z))
            self._bridge.observed(msg.pose.pose.position.x,
                                  msg.pose.pose.position.y, yaw)

        def _tick(self) -> None:
            for note in self._bridge.poll_commands():
                self.get_logger().info(f"robot: {note}")
            note = self._bridge.tick()
            if note:
                self.get_logger().info(f"robot: {note}")

    rclpy.init()
    node = RobotBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a robot's sensed pose into the world model")
    parser.add_argument("--domain", type=int, default=None)
    parser.add_argument("--source", choices=("ros", "kinematic"), default="ros",
                        help="ros: read /odom and drive through nav2 (the "
                             "demo). kinematic: an internal straight-line sim "
                             "that honours no keep-out, for iteration.")
    parser.add_argument("--hz", type=float, default=SIM_HZ)
    args = parser.parse_args()
    return run(args.domain, args.source, args.hz)


if __name__ == "__main__":
    sys.exit(main())
