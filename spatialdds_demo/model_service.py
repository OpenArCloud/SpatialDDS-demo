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
from spatialdds_demo.qos_profiles import MODEL_LATCHED
from spatialdds_demo.topics import (
    TOPIC_MODEL_ENTITY_V1, TOPIC_MODEL_RELATIONSHIP_V1,
)
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import (
    Basis, Entity, LifecycleState, ModelLayer, Relationship,
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

# Borrowed vocabulary. The layer mints no types of its own: Wikidata's
# canonical entity URIs, in the http:// form Wikidata publishes.
TYPE_FOUNTAIN = "http://www.wikidata.org/entity/Q483453"   # fountain
TYPE_RUBBER_DUCK = "http://www.wikidata.org/entity/Q851478"  # rubber duck

# Venue-frame metres. The frame's origin is the OpenVPS map anchor, which sits
# on the plaza north-west of the basin, so the fountain itself is a short walk
# from the origin rather than at it.
FOUNTAIN_CENTRE = (11.708, -5.23, -1.42)

# The basin, coarsely: about 28 m across and 33 m along the mall.
BASIN_HALF_EW, BASIN_HALF_NS = 14.1, 16.25
BASIN_DOWN, BASIN_UP = 1.0, 4.0

# Three ducks on the water. The first reuses the catalogue row's own pose, so
# switching the client from catalogue placement to model placement does not
# move anything on screen -- the point being demonstrated is the extra two.
DUCKS: List[Tuple[str, Tuple[float, float, float], List[float]]] = [
    ("ent:duck:catalog-pose", (11.708, -14.273, -1.423),
     [0.0, 0.0, -0.7071067811865475, 0.7071067811865476]),   # facing south
    ("ent:duck:west", (6.5, -8.0, -1.423),
     [0.0, 0.0, 0.0, 1.0]),                                   # facing east
    ("ent:duck:east", (16.5, -10.5, -1.423),
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
        properties=[KV(key="demo.label", value="Littlefield Fountain")],
        # Empty in Part 1. A GERS or OSM id belongs here, but only a verified
        # one -- inventing an identifier that resolves to something else is
        # worse than carrying none.
        external_refs=[],
        # The fountain has no asset: it is already in the photorealistic tiles.
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
            # SLOW: they move, but not at frame rate -- see move_duck.py.
            layer=ModelLayer.SLOW,
            frame_ref=frame,
            has_pose=True,
            pose=PoseSE3(t=list(translation), q=list(rotation)),
            has_extent=False,
            extent=zero_extent,
            properties=[],
            external_refs=[],
            # All three, the same row. One asset, three instances.
            content_refs=[f"catalog:{DUCK_CONTENT_ID}"],
            state=LifecycleState.ACTIVE,
            state_reason="",
            source_id=SOURCE_ID,
            stamp=stamp,
        )
        for entity_id, translation, rotation in DUCKS
    ]
    return [fountain] + ducks


def seed_relationships(entities: List[Entity],
                       stamp: Optional[Time] = None) -> List[Relationship]:
    """`contains`, fountain to each duck. Keyed separately from the entities,
    so an edge can retire without either end retiring."""
    stamp = stamp or _now()
    fountain = entities[0]
    return [
        Relationship(
            rel_id=f"rel:contains:{duck.entity_id.split(':')[-1]}",
            kind="contains",
            from_entity_id=fountain.entity_id,
            to_entity_id=duck.entity_id,
            properties=[],
            source_id=SOURCE_ID,
            stamp=stamp,
        )
        for duck in entities[1:]
    ]


class ModelPublisher:
    """Latched writers for both model topics."""

    def __init__(self, participant: DomainParticipant):
        self._entities = tt.make_writer(
            participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        self._relationships = tt.make_writer(
            participant, TOPIC_MODEL_RELATIONSHIP_V1, Relationship, MODEL_LATCHED.name)
        self._published: Dict[str, Entity] = {}

    def publish_entity(self, entity: Entity) -> None:
        self._entities.write(entity)
        self._published[entity.entity_id] = entity

    def publish_relationship(self, relationship: Relationship) -> None:
        self._relationships.write(relationship)

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

    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while not stop:
        time.sleep(0.5)

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
