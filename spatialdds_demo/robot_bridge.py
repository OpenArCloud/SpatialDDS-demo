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

# Where the sim starts it: on the plaza, clear of the water, so it has
# somewhere to drive from.
START_XY = (22.0, -8.0)
GROUND_Z = -1.9

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


class RobotBridge:
    """
    Owns `ent:robot:tb3`. Publishes its latched record and its fast poses.

    No ROS in here: it is handed poses. The ROS node calls `observed()` from
    an `/amcl_pose` callback and `tick()` from a timer; the built-in sim calls
    exactly the same two methods. That keeps the interesting behaviour --
    cadence, latching, going quiet -- testable without a robot.
    """

    def __init__(self, participant: DomainParticipant):
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

        x, y = command.pose.t[0], command.pose.t[1]
        self._goal = (x, y)
        return (f"accepted goto ({x:.2f}, {y:.2f}) "
                f"(asked by {command.requester_id})")

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

    def step(self, dt: float) -> Tuple[float, float, float]:
        if self.goal is None:
            # Idle: a slow patrol along the plaza's edge, so there is
            # something to watch before anyone sends it anywhere.
            self.yaw += 0.25 * dt
            self.x += self.speed * dt * math.cos(self.yaw)
            self.y += self.speed * dt * math.sin(self.yaw)
            return self.x, self.y, self.yaw

        gx, gy = self.goal
        dx, dy = gx - self.x, gy - self.y
        distance = math.hypot(dx, dy)
        if distance < 0.15:
            self.goal = None
            return self.x, self.y, self.yaw
        self.yaw = math.atan2(dy, dx)
        step = min(self.speed * dt, distance)
        self.x += step * math.cos(self.yaw)
        self.y += step * math.sin(self.yaw)
        return self.x, self.y, self.yaw


def run(domain_id: Optional[int] = None, source: str = "sim",
        hz: float = SIM_HZ) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)
    bridge = RobotBridge(participant)

    print(f"robot: domain {domain_id}, source {SOURCE_ID}")
    print(f"robot: owns {ENTITY_ID} — OBSERVED/FAST, one writer per key")
    print(f"robot: renders from catalog:{ROBOT_CONTENT_ID}")
    if source != "sim":
        print("robot: --source ros is not wired yet; the ROS node lands with "
              "the robot tier. Falling back to the built-in sim.", flush=True)

    sim = PlazaSim()
    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    interval = 1.0 / hz
    reported = 0
    while not stop:
        for note in bridge.poll_commands():
            print(f"robot: {note}", flush=True)
        if bridge.goal is not None and sim.goal != bridge.goal:
            sim.goal = bridge.goal
        if sim.goal is None and bridge.goal is not None:
            bridge.clear_goal()
        x, y, yaw = sim.step(interval)
        bridge.observed(x, y, yaw)
        note = bridge.tick()
        if note:
            print(f"robot: {note}", flush=True)
        reported += 1
        if reported % (int(hz) * 20) == 0:
            print(f"robot: {reported} poses; at ({x:.2f}, {y:.2f}), "
                  f"silence threshold {bridge.silence_threshold():.1f}s",
                  flush=True)
        time.sleep(interval)

    print("robot: stopping. The entity stays as it was, and goes UNOBSERVED "
          "once whoever is reading notices the silence.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a robot's sensed pose into the world model")
    parser.add_argument("--domain", type=int, default=None)
    parser.add_argument("--source", choices=("sim", "ros"), default="sim")
    parser.add_argument("--hz", type=float, default=SIM_HZ)
    args = parser.parse_args()
    return run(args.domain, args.source, args.hz)


if __name__ == "__main__":
    sys.exit(main())
