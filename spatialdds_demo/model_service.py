#!/usr/bin/env python3
"""
World model publisher — demo-local `oarc_model`, non-normative.

Seeds a small model of the venue and stays alive holding it. Both topics are
TRANSIENT_LOCAL with KEEP_LAST(1) per key, so the publisher *is* the store: a
client that connects later is handed the current model by the middleware, with
no query, no snapshot request and no replay logic anywhere. Staying alive is
therefore load-bearing, not idle — the writer's history is what late joiners
read.

What the model is for, in one example. The catalogue has a single duck row: an
asset, with a URI and a checksum. This publishes *three* ducks — three
entities, three ids, three poses, all pointing at that one row through
`content_refs`. A catalogue that carries its own pose can say where a duck is
exactly once, which is the limitation the layer exists to remove.

Run it:

    python3 -m spatialdds_demo.model_service

or set SPATIALDDS_MODEL_LAYER=1 and let run_bridge_server_docker.sh start it.
"""

import argparse
import os
import signal
import sys
import time
import uuid
from typing import Dict, List, Optional, Tuple

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.qos_profiles import (
    MODEL_COMMAND, MODEL_FAST, MODEL_LATCHED,
)
from spatialdds_demo.topics import (
    TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1, TOPIC_MODEL_POSE_V1,
    TOPIC_MODEL_RELATIONSHIP_V1,
)
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import (
    Basis, Entity, LifecycleState, ModelCommand, ModelLayer, ModelPose,
    Relationship,
)
from spatialdds_idl.spatial.common import CoordConvention, FrameRef, KV
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

# The frame the venue model is expressed in — the same one the client
# localizes into and the same one the catalogue row names, so an entity pose
# and a catalogue pose are directly comparable.
VENUE_FRAME_FQN = "map/ut-littlefield-fountain"

# The one catalogue row all three ducks render from. Asset, not instance.
DUCK_CONTENT_ID = "89f2d953-076d-5c7d-9b74-1193f71685a6"

SOURCE_ID = "svc:model:demo/venue"

# Between a tombstone and the dispose that follows it. For people, not for
# delivery -- see ModelPublisher.retire.
TOMBSTONE_SETTLE_S = 1.5

# The fast tier's two numbers.
#
# A FAST entity's every move goes out on the pose topic; its latched entity
# record is refreshed every Nth move, so a late joiner is at most N-1 moves
# behind and converges on the next pose that arrives.
LATCH_EVERY_N_MOVES = 5

# And the flush that makes rest state exact. When a thing stops moving, the
# stream stops carrying it and the latch is whatever it was at the last
# refresh -- so a reader arriving after the motion ended would be handed a
# pose from up to four moves before the end, with nothing coming to correct
# it. The every-Nth cadence answers "how stale while moving"; only this
# answers "how stale once stopped", and the answer has to be "not at all".
#
# The general rule, for R7: **the latch converges to the stream on idle.** A
# fast lane may lag the truth while things are moving. It may not leave a
# wrong answer lying around once they have stopped.
#
# **"Idle" is only meaningful relative to an entity's own cadence**, which the
# first version of this got wrong and the demo caught within a minute. A fixed
# one-second threshold against a mover giving each duck a turn every 1.5 s
# meant every duck looked idle in every gap: 94 "it stopped moving" flushes
# while nothing had stopped, and an entity record republished as often as the
# pose it was supposed to be cheaper than. The fast tier had quietly become
# the slow one.
#
# So the threshold is derived: a floor, and a multiple of the interval the
# entity is actually being updated at. A thing moving every 100 ms is idle
# after a beat; a thing moving every ten seconds is not idle after eleven.
IDLE_FLUSH_S = 1.0
IDLE_FLUSH_FACTOR = 3.0
# How much of the observed interval survives each new measurement. Low enough
# to follow a cadence change within a few updates, high enough that one late
# sample does not convince it everything stopped.
GAP_SMOOTHING = 0.7

# Borrowed vocabulary. The layer mints no types of its own: Wikidata's
# canonical entity URIs, in the http:// form Wikidata publishes.
TYPE_FOUNTAIN = "http://www.wikidata.org/entity/Q483453"   # fountain
TYPE_RUBBER_DUCK = "http://www.wikidata.org/entity/Q851478"  # rubber duck

# "basin" -- element of a fountain where water is poured into. Verified
# 2026-09-03, and it corroborates the hierarchy rather than merely fitting it:
# the item carries `part of` (P361) -> Q483453, which is the type already on
# the fountain. The containment this seeder publishes is therefore borrowed
# from the vocabulary, not invented by the demo.
#
# Q1328914 ("reflecting pool") was the near miss: its definition requires
# water undisturbed by fountain jets, which this basin is not.
TYPE_BASIN = "http://www.wikidata.org/entity/Q810524"

# Venue-frame metres. The frame's origin is the OpenVPS map anchor, which sits
# on the plaza north-west of the basin, so the fountain itself is a short walk
# from the origin rather than at it.
FOUNTAIN_CENTRE = (11.708, -5.23, -1.42)

# The basin, coarsely: about 28 m across and 33 m along the mall.
BASIN_HALF_EW, BASIN_HALF_NS = 14.1, 16.25
BASIN_DOWN, BASIN_UP = 1.0, 4.0

# The pond: the water itself, as a thing rather than as a property of the
# fountain. Bounds are the waterline measured off the photorealistic tiles,
# then pulled in past the memorial sculpture at x ~ 8.
#
# Deliberately conservative rather than accurate. The venue *declares* these
# bounds; it has not surveyed them, and a declared boundary that is a little
# smaller than the water is safe in the way that matters -- anything trusting
# it stays wet. Being visibly coarse is also useful: when a second service
# publishes its own observed opinion of the same water, the two disagree for
# a reason a person can see rather than by a rounding error.
POND_MIN = (9.5, -18.0, -2.0)
POND_MAX = (20.0, -10.0, -1.0)

# Three ducks on the water. The first reuses the catalogue row's own pose, so
# switching the client from catalogue placement to model placement does not
# move anything on screen -- the point being demonstrated is the extra two.
#
# The id is the stable key and says where the duck came from; the name is for
# people. They are separate on purpose -- renaming a duck must not create a
# second one, and `ent:duck:catalog-pose` earns its id by sitting exactly
# where the catalogue row would have put it.
DUCKS: List[Tuple[str, str, Tuple[float, float, float], List[float]]] = [
    ("ent:duck:catalog-pose", "Waddles", (11.708, -14.273, -1.423),
     [0.0, 0.0, -0.7071067811865475, 0.7071067811865476]),   # facing south
    # Moved onto the water in Part 3. It had been at (6.5, -8.0) since Part 1,
    # which was fine while nothing said where the water was and wrong the
    # moment the pond declared its bounds -- x 6.5 is past the western edge
    # and y -8.0 is up on the rim. The seed has to be consistent with itself
    # or the mover starts out of bounds and corrects on its first tick, which
    # would look like the demo fixing a mistake it should not have made.
    ("ent:duck:west", "Bobbin", (10.5, -13.0, -1.423),
     [0.0, 0.0, 0.0, 1.0]),                                   # facing east
    ("ent:duck:east", "Skipper", (16.5, -10.5, -1.423),
     [0.0, 0.0, 0.3826834323650898, 0.9238795325112867]),     # facing north-east
]


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now % 1) * 1e9))


def venue_frame() -> FrameRef:
    """UUIDv5 of the fqn, the same derivation the catalogue and its
    announced transform use, so all three name one frame rather than three."""
    return FrameRef(
        uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, VENUE_FRAME_FQN)),
        fqn=VENUE_FRAME_FQN,
        has_coord_convention=True,
        coord_convention=CoordConvention.ENU,
    )


def seed_entities(stamp: Optional[Time] = None) -> List[Entity]:
    stamp = stamp or _now()
    frame = venue_frame()
    zero_extent = Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0])

    cx, cy, cz = FOUNTAIN_CENTRE
    fountain = Entity(
        entity_id="ent:fountain:littlefield",
        # OBSERVED: it is in the LiDAR capture the map was built from.
        basis=Basis.OBSERVED,
        type_uris=[TYPE_FOUNTAIN],
        # STATIC: the fountain is not going anywhere.
        layer=ModelLayer.STATIC,
        frame_ref=frame,
        has_pose=True,
        pose=PoseSE3(t=[cx, cy, cz], q=[0.0, 0.0, 0.0, 1.0]),
        # An Aabb3 is the right shape here, unlike in earth-fixed coverage
        # where it would be degrees in a metres field: this frame is local and
        # metric, which is exactly what Aabb3 is for. Coarse on purpose -- the
        # basin is round and this is its bounding box, which is a claim about
        # where the fountain is, not a model of its geometry.
        has_extent=True,
        extent=Aabb3(min_xyz=[cx - BASIN_HALF_EW, cy - BASIN_HALF_NS, cz - BASIN_DOWN],
                     max_xyz=[cx + BASIN_HALF_EW, cy + BASIN_HALF_NS, cz + BASIN_UP]),
        properties=[KV(key="demo.label", value="Littlefield Fountain"),
                    KV(key="demo.note",
                       value="Memorial fountain at the south entrance to the Main Mall. The map this demo localizes into was built from a phone LiDAR capture of it.")],
        # Verified, not guessed. Both checked 2026-09-02:
        #
        # * `wikidata:Q6652941` -- "Littlefield Fountain", a war memorial in
        #   Austin, Texas, coordinates 30.28389, -97.73969. That is about 6 m
        #   from this entity's pose, well inside a basin 28 m across.
        # * `osm:way/201514442` -- tagged `name=Littlefield Fountain`,
        #   `amenity=fountain`, `wikidata=Q6652941`.
        #
        # The two corroborate each other rather than resting on a name match:
        # OSM points at the Wikidata item, and Wikidata's coordinates land on
        # this venue. `amenity=fountain` is also the concept Q483453 in
        # type_uris names, so the type and the references agree.
        #
        # An identifier that resolves to the wrong thing is worse than none.
        # Add others only with verification, and record the date here.
        external_refs=[KV(key="wikidata", value="Q6652941"),
                       KV(key="osm", value="way/201514442")],
        # The fountain has no asset: it is already in the photorealistic tiles.
        content_refs=[],
        state=LifecycleState.ACTIVE,
        state_reason="",
        source_id=SOURCE_ID,
        stamp=stamp,
    )

    px = [(POND_MIN[i] + POND_MAX[i]) / 2 for i in range(3)]
    pond = Entity(
        entity_id="ent:pond:littlefield",
        # DECLARED: the venue asserts these bounds. Nothing measured them into
        # the model and nobody surveyed them -- which is exactly what DECLARED
        # is for, and why the observed service's opinion is allowed to differ.
        basis=Basis.DECLARED,
        type_uris=[TYPE_BASIN],
        layer=ModelLayer.STATIC,
        frame_ref=frame,
        has_pose=True,
        pose=PoseSE3(t=px, q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=True,
        extent=Aabb3(min_xyz=list(POND_MIN), max_xyz=list(POND_MAX)),
        properties=[KV(key="demo.label", value="Pond"),
                    KV(key="demo.note",
                       value="The water, as its own entity. Bounds are declared by the venue and deliberately a little inside the waterline.")],
        external_refs=[],
        content_refs=[],
        state=LifecycleState.ACTIVE,
        state_reason="",
        source_id=SOURCE_ID,
        stamp=stamp,
    )

    ducks = [
        Entity(
            entity_id=entity_id,
            # AUTHORED: somebody put it there. Nothing observed these.
            basis=Basis.AUTHORED,
            type_uris=[TYPE_RUBBER_DUCK],
            # FAST since Part 3: a mover service wanders them continuously,
            # so their pose changes on the tempo lane and their entity record
            # is refreshed periodically. The field is not decoration -- it is
            # what `move` branches on.
            layer=ModelLayer.FAST,
            frame_ref=frame,
            has_pose=True,
            pose=PoseSE3(t=list(translation), q=list(rotation)),
            has_extent=False,
            extent=zero_extent,
            # `demo.label` is what a client shows a person. Namespaced,
            # because properties is a shared bag and an unqualified "label"
            # would collide with the first other producer to want one.
            properties=[KV(key="demo.label", value=name)],
            external_refs=[],
            # All three, the same row. One asset, three instances.
            content_refs=[f"catalog:{DUCK_CONTENT_ID}"],
            state=LifecycleState.ACTIVE,
            state_reason="",
            source_id=SOURCE_ID,
            stamp=stamp,
        )
        for entity_id, name, translation, rotation in DUCKS
    ]
    return [fountain, pond] + ducks


def seed_relationships(entities: List[Entity],
                       stamp: Optional[Time] = None) -> List[Relationship]:
    """
    The hierarchy: fountain contains pond, pond contains each duck.

    Part 1 had the fountain containing the ducks directly, which was true and
    shallow -- the ducks are on the water, and the water is part of the
    fountain. Wikidata says the same thing about the types (Q810524 is
    `part of` Q483453), so the shape is borrowed rather than invented.

    Ids name both ends because there are now two levels and
    `rel:contains:west` no longer says which parent is meant. The Part 1 ids
    are superseded, not renamed: an operator disposes them through the
    command lane, since an edge cannot be updated into a different edge.
    """
    stamp = stamp or _now()
    by_id = {entity.entity_id: entity for entity in entities}
    fountain = by_id["ent:fountain:littlefield"]
    pond = by_id["ent:pond:littlefield"]

    def edge(parent: Entity, child: Entity) -> Relationship:
        return Relationship(
            # Both ends, by kind and name. Taking only the child's last
            # segment read fine for ducks and produced
            # `rel:contains:fountain-littlefield` for the pond, whose last
            # segment is the venue. An id that needs you to know which end is
            # which is not an id.
            rel_id=(f"rel:contains:{parent.entity_id.split(':')[1]}-"
                    f"{child.entity_id.split(':', 1)[1].replace(':', '-')}"),
            kind="contains",
            from_entity_id=parent.entity_id,
            to_entity_id=child.entity_id,
            properties=[],
            source_id=SOURCE_ID,
            stamp=stamp,
        )

    ducks = [e for e in entities if e.entity_id.startswith("ent:duck")]
    return [edge(fountain, pond)] + [edge(pond, duck) for duck in ducks]


class ModelPublisher:
    """
    Latched writers for both model topics, and the authority over what they
    hold.

    "Authority" is not grandeur: TRANSIENT_LOCAL history is scoped to the
    writer that published it, so whatever this process latches is what a late
    joiner is handed, no matter who else has written to the topic since. A
    retirement published by anyone else dies with that writer and the entity
    comes back. See SPEC_COMPLIANCE, "Two writers, one instance" -- the same
    problem measured for moves, and the reason retirement is a request.
    """

    def __init__(self, participant: DomainParticipant):
        self._entities = tt.make_writer(
            participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        self._relationships = tt.make_writer(
            participant, TOPIC_MODEL_RELATIONSHIP_V1, Relationship, MODEL_LATCHED.name)
        self._poses = tt.make_writer(
            participant, TOPIC_MODEL_POSE_V1, ModelPose, MODEL_FAST.name)
        self._published: Dict[str, Entity] = {}
        self._edges: Dict[str, Relationship] = {}
        # Per FAST entity: moves since the latched record was last refreshed,
        # and when its last fast pose went out. Both exist only for the fast
        # tier; a SLOW entity is latched on every move as it always was.
        self._since_latch: Dict[str, int] = {}
        self._last_fast: Dict[str, float] = {}
        # Smoothed interval between an entity's poses, which is what "idle"
        # has to be measured against.
        self._gap: Dict[str, float] = {}

    def publish_entity(self, entity: Entity) -> None:
        self._entities.write(entity)
        self._published[entity.entity_id] = entity

    def publish_relationship(self, relationship: Relationship) -> None:
        self._relationships.write(relationship)
        self._edges[relationship.rel_id] = relationship

    def owns(self, entity_id: str) -> bool:
        return entity_id in self._published

    def retire(self, entity_id: str, reason: str,
               settle: float = TOMBSTONE_SETTLE_S) -> List[str]:
        """
        Tombstone, then dispose -- for the entity and every edge touching it.

        The order carries the meaning. A dispose on its own says the entity is
        gone and nothing about why; the tombstone is the last thing the entity
        ever says, and it says what happened to it. Reversed, or collapsed
        into one step, the reason is unreadable.

        `settle` is a deliberate pause between the two. It is not needed for
        delivery -- both samples arrive either way -- but it is needed for a
        person: a tombstone and its dispose issued in the same millisecond
        reach a human as a thing that simply vanished.

        Returns the rel_ids that were cascaded.
        """
        entity = self._published.get(entity_id)
        if entity is None:
            raise KeyError(entity_id)

        # Read-modify-write on our own latched copy: state and stamp change,
        # nothing else does. Pose, type, refs and extent are what the entity
        # was at the end, and a tombstone that quietly edited them would be
        # rewriting history rather than closing it.
        entity.state = LifecycleState.RETIRED
        entity.state_reason = reason
        entity.stamp = _now()
        self._entities.write(entity)

        # Edges first on the way out, so nothing is left pointing at a
        # tombstone that is about to be disposed.
        cascaded = [rel_id for rel_id, edge in self._edges.items()
                    if entity_id in (edge.from_entity_id, edge.to_entity_id)]

        time.sleep(settle)

        for rel_id in cascaded:
            edge = self._edges.pop(rel_id)
            # Disposed, not tombstoned. `Relationship` has no LifecycleState,
            # so an edge cannot say why it went away -- it can only stop
            # being there. Recorded in SPEC_COMPLIANCE as a design note.
            self._relationships.dispose(edge)

        self._entities.dispose(entity)
        del self._published[entity_id]
        return cascaded

    def move(self, entity_id: str, pose: PoseSE3) -> Tuple[float, float, float]:
        """
        Change the pose and the stamp of an entity we own, and nothing else.

        Read-modify-write on our own latched copy, the discipline the mover
        script used to apply from outside. Doing it here is what makes it
        last: written from any other writer, the new pose lives only as long
        as that writer does, and the next reader to join is handed the pose we
        are still latching. Returns where it was.

        How it is published depends on the entity's own `layer`, which is
        exactly what that field is for. A SLOW thing is latched on every move.
        A FAST one goes out on the pose topic every time and is latched every
        `LATCH_EVERY_N_MOVES`, so the expensive record is not rewritten to say
        a duck drifted 0.9 m -- with `flush_idle` closing the gap once it
        stops.
        """
        entity = self._published.get(entity_id)
        if entity is None:
            raise KeyError(entity_id)
        was = tuple(entity.pose.t)
        entity.pose = PoseSE3(t=list(pose.t), q=list(pose.q))
        entity.stamp = _now()

        if entity.layer != ModelLayer.FAST:
            self._entities.write(entity)
            return was

        self._poses.write(ModelPose(
            entity_id=entity_id,
            pose=PoseSE3(t=list(pose.t), q=list(pose.q)),
            source_id=SOURCE_ID,
            stamp=entity.stamp))
        now = time.time()
        previous = self._last_fast.get(entity_id)
        if previous is not None:
            gap = now - previous
            known = self._gap.get(entity_id)
            self._gap[entity_id] = (gap if known is None
                                    else GAP_SMOOTHING * known
                                    + (1 - GAP_SMOOTHING) * gap)
        self._last_fast[entity_id] = now
        count = self._since_latch.get(entity_id, 0) + 1
        if count >= LATCH_EVERY_N_MOVES:
            self._entities.write(entity)
            self._since_latch[entity_id] = 0
        else:
            self._since_latch[entity_id] = count
        return was

    def idle_threshold(self, entity_id: str) -> float:
        """How long without a pose means this entity has stopped.

        Derived from its own cadence, because a gap is only evidence of
        stopping if it is longer than the gaps that thing normally has."""
        gap = self._gap.get(entity_id)
        if gap is None:
            return IDLE_FLUSH_S
        return max(IDLE_FLUSH_S, IDLE_FLUSH_FACTOR * gap)

    def flush_idle(self, now: Optional[float] = None) -> List[str]:
        """
        Bring the latch up to date for anything that has stopped moving.

        A fast lane is allowed to lag the truth while things are moving. It is
        not allowed to leave a wrong answer lying around once they have
        stopped: a reader arriving after the motion ended has nothing coming
        to correct it, and would be handed a pose from up to four moves before
        the end -- one duck, two positions, which is the failure Part 2 closed.

        Cheap by construction: it writes only entities that have both moved
        since their last refresh and gone quiet since.
        """
        now = time.time() if now is None else now
        flushed = []
        for entity_id, pending in list(self._since_latch.items()):
            if pending == 0:
                continue
            last = self._last_fast.get(entity_id, 0.0)
            if now - last < self.idle_threshold(entity_id):
                continue
            entity = self._published.get(entity_id)
            if entity is None:
                self._since_latch.pop(entity_id, None)
                continue
            self._entities.write(entity)
            self._since_latch[entity_id] = 0
            flushed.append(entity_id)
        return flushed

    def dispose_edge(self, rel_id: str, reason: str) -> bool:
        """
        Remove one edge. Not "retire" it -- it cannot be retired.

        `Relationship` has no `LifecycleState`, so an edge has no way to say
        why it went; it can only stop being there. The reason is carried in
        this service's log and the operator tool's output and nowhere on the
        wire, which is the gap recorded in SPEC_COMPLIANCE made visible one
        more time rather than papered over with a field that does not exist.

        Calling this verb `retire` would have contradicted that finding four
        commits after publishing it.
        """
        edge = self._edges.pop(rel_id, None)
        if edge is None:
            return False
        self._relationships.dispose(edge)
        return True

    def set_extent(self, entity_id: str, extent: Aabb3) -> Tuple[List[float], List[float]]:
        """
        Change what an entity claims its bounds are. Returns the old ones.

        The same read-modify-write as `move`, on our own latched copy, for the
        same reason: a boundary written by anyone else lasts exactly as long
        as that writer. Consumers reading bounds -- the mover is one -- get
        the new ones from the latch and from the stream both.
        """
        entity = self._published.get(entity_id)
        if entity is None:
            raise KeyError(entity_id)
        was = (list(entity.extent.min_xyz), list(entity.extent.max_xyz))
        entity.has_extent = True
        entity.extent = Aabb3(min_xyz=list(extent.min_xyz),
                              max_xyz=list(extent.max_xyz))
        entity.stamp = _now()
        self._entities.write(entity)
        return was

    def restore_seed(self) -> int:
        """Re-publish the seed, proving a retired id is not burned."""
        entities = seed_entities()
        for entity in entities:
            self.publish_entity(entity)
        for relationship in seed_relationships(entities):
            self.publish_relationship(relationship)
        return len(entities)

    def handle_command(self, command: ModelCommand) -> str:
        """Act on a request, or say why not. Returns a line for the log."""
        if command.verb == "restore":
            count = self.restore_seed()
            return f"restore from {command.requester_id}: re-seeded {count} entities"
        if command.verb == "set_extent":
            if not command.has_extent:
                # A zeroed Aabb3 is a well-formed request to shrink something
                # to a point. Declining is the only reading that cannot be
                # mistaken for obedience.
                return f"declined: set_extent for {command.subject_id} carried no extent"
            if not self.owns(command.subject_id):
                return (f"declined: {command.subject_id} is not ours "
                        f"(asked by {command.requester_id})")
            was = self.set_extent(command.subject_id, command.extent)
            now = command.extent
            return (f"set_extent {command.subject_id} "
                    f"x {was[0][0]:.1f}..{was[1][0]:.1f} -> "
                    f"x {now.min_xyz[0]:.1f}..{now.max_xyz[0]:.1f}, "
                    f"y {was[0][1]:.1f}..{was[1][1]:.1f} -> "
                    f"y {now.min_xyz[1]:.1f}..{now.max_xyz[1]:.1f} "
                    f"(asked by {command.requester_id})")
        if command.verb == "dispose_edge":
            if self.dispose_edge(command.subject_id, command.reason):
                # The only place the reason survives.
                return (f"disposed edge {command.subject_id} — "
                        f"{command.reason!r} (asked by {command.requester_id}); "
                        f"the edge itself carries no reason, it can only stop "
                        f"being there")
            return (f"declined: no edge {command.subject_id} here "
                    f"(asked by {command.requester_id})")
        if command.verb == "move":
            if not command.has_pose:
                return f"declined: move for {command.subject_id} carried no pose"
            if not self.owns(command.subject_id):
                return (f"declined: {command.subject_id} is not ours "
                        f"(asked by {command.requester_id})")
            was = self.move(command.subject_id, command.pose)
            now = command.pose.t
            return (f"moved {command.subject_id} "
                    f"({was[0]:.2f}, {was[1]:.2f}) -> ({now[0]:.2f}, {now[1]:.2f}) "
                    f"(asked by {command.requester_id})")
        if command.verb != "retire":
            return f"ignored: unknown verb {command.verb!r} from {command.requester_id}"
        if not self.owns(command.subject_id):
            # Refusing is the honest answer. This process can only retire what
            # it latches; pretending otherwise would publish a tombstone that
            # some other writer's sample immediately contradicts.
            return (f"declined: {command.subject_id} is not ours "
                    f"(asked by {command.requester_id})")
        cascaded = self.retire(command.subject_id, command.reason)
        edges = f", cascaded {len(cascaded)} edge(s)" if cascaded else ""
        return (f"retired {command.subject_id} — {command.reason!r}"
                f"{edges} (asked by {command.requester_id})")

    def close(self) -> None:
        """Dispose entity instances so a clean shutdown does not leave a model
        latched for readers that outlive us. Tombstones are Part 2; this is
        only the courtesy version."""
        for entity in self._published.values():
            try:
                self._entities.dispose(entity)
            except Exception:
                pass
        self._published.clear()


def run_server(domain_id: Optional[int] = None) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)
    publisher = ModelPublisher(participant)

    entities = seed_entities()
    relationships = seed_relationships(entities)

    print(f"model: domain {domain_id}, source {SOURCE_ID}")
    print(f"model: entity topic       {TOPIC_MODEL_ENTITY_V1}")
    print(f"model: relationship topic {TOPIC_MODEL_RELATIONSHIP_V1}")
    print(f"model: qos {MODEL_LATCHED.name} — {MODEL_LATCHED.note}")

    for entity in entities:
        publisher.publish_entity(entity)
        placed = (f"pose ({entity.pose.t[0]:.2f}, {entity.pose.t[1]:.2f}, "
                  f"{entity.pose.t[2]:.2f})" if entity.has_pose else "unplaced")
        refs = ",".join(entity.content_refs) or "no content"
        print(f"model: entity {entity.entity_id} — {entity.basis.name}/"
              f"{entity.layer.name}, {placed}, {refs}")
    for relationship in relationships:
        publisher.publish_relationship(relationship)
        print(f"model: relationship {relationship.rel_id} — "
              f"{relationship.from_entity_id} {relationship.kind} "
              f"{relationship.to_entity_id}")
    print(f"model: seeded {len(entities)} entities, {len(relationships)} "
          f"relationships; latched, holding")

    # The command lane. Retirement arrives here rather than being written
    # around us, because only the writer that latched a sample can make it
    # stop being the answer to a late join.
    commands = tt.make_reader(
        participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)
    print(f"model: command topic     {TOPIC_MODEL_COMMAND_V1} "
          f"({MODEL_COMMAND.name})")

    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    failures = 0
    while not stop:
        try:
            # The take is inside the try, which it was not. A malformed sample
            # on the command lane used to raise here and kill the service --
            # so an operator tool exiting could take down the authority
            # holding the entire world. Nothing arriving on this topic is
            # worth more than the model being served.
            batch = tt.take_samples(commands) or []
        except Exception as error:
            failures += 1
            if failures == 1 or failures % 100 == 0:
                print(f"model: command lane read failed ({failures}): {error!r}",
                      flush=True)
            batch = []
        for entity_id in publisher.flush_idle():
            print(f"model: latch flushed for {entity_id} — it stopped moving",
                  flush=True)
        for command in batch:
            try:
                print(f"model: {publisher.handle_command(command)}", flush=True)
            except Exception as error:
                # A bad command must not take the service down with it; the
                # world it is holding is worth more than the request.
                print(f"model: command failed ({command.verb} "
                      f"{command.subject_id}): {error!r}", flush=True)
        time.sleep(0.1)

    print("model: disposing instances and exiting")
    publisher.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SpatialDDS demo world model publisher (demo-local oarc_model)")
    parser.add_argument("--domain", type=int, default=None,
                        help="DDS domain id (default: SPATIALDDS_DDS_DOMAIN)")
    args = parser.parse_args()
    return run_server(args.domain)


if __name__ == "__main__":
    sys.exit(main())
