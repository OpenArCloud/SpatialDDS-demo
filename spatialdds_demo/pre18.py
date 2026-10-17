"""
The pre-1.8 surface, kept for the lanes that have not migrated.

SpatialDDS 1.8's ``spatial.owm/0.1`` carries no relationship type: the module
excludes relationships explicitly and reserves them as open design. The Open
World Model demo migrated to 1.8 and lost this lane, which is the GAP row in
``docs/owm-1.8-census.md`` and the entry it feeds into the 1.9 queue.

This module is not that lane. It is one seed function, still typed against the
demo-local ``oarc_model`` bindings, kept because the web bridge -- a later
batch -- still reads the pre-1.8 topics and still builds its fixtures from
this hierarchy. Nothing publishes it: ``ModelPublisher`` has no relationship
writer any more, so an edge built here reaches DDS only if some unmigrated
consumer writes it itself.

When the bridge and the robot lane migrate, this file goes with them. It is a
dated artifact by construction, which is why it says so at the top rather than
looking like part of the demo.

It holds two things, and renaming it from `pre18_relationships` is why: the
relationship seed above, and `venue_frame` below. Both exist for the same
reason -- a consumer still on the demo-local types needs a value in that era's
shape -- and a module named for only one of them would have gone on collecting
the other in silence.
"""

import uuid
from typing import List, Optional

from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity, Relationship
from spatialdds_idl.spatial.common import CoordConvention, FrameRef

from spatialdds_demo.model_service import SOURCE_ID, VENUE_FRAME_FQN, _now


def seed_relationships(entities: List[Entity],
                       stamp: Optional[Time] = None) -> List[Relationship]:
    """
    The hierarchy: fountain contains pond, pond contains the shallows,
    pond contains each duck.

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

    shallows = by_id["ent:shallows:littlefield"]

    # The ducks stay children of the pond rather than of the shallows. They
    # are on the water; the shallows are the region the venue keeps them in,
    # which is a policy about them and not a place that contains them.
    ducks = [e for e in entities if e.entity_id.startswith("ent:duck")]
    return ([edge(fountain, pond), edge(pond, shallows)]
            + [edge(pond, duck) for duck in ducks])


def venue_frame() -> FrameRef:
    """The venue frame in the four-member 1.7 shape.

    `model_service.venue_frame()` returns 1.8's eight-member `FrameRef`, which
    gained the metric scale declaration. A pre-1.8 lane that calls it builds a
    1.7 `Entity` holding an object of the wrong era: dataclasses do not
    type-check, so it constructs, and CycloneDDS then serializes against the
    *declared* 1.7 type and silently drops the four scale members. 320 bytes
    out, no error, and the wire result happens to equal this function's --
    which is exactly why it would have gone unnoticed until the bridge batch
    tried to read a scale that was never sent.

    So the pin is honoured in the wiring too, not only in the topic names:
    `_PRE18` lanes get a `_PRE18` frame. This is not a translation between the
    two eras and nothing converts anything -- the frame's *identity* is a
    constant, `VENUE_FRAME_FQN`, and both functions derive the same UUIDv5
    from it. One source of truth for which frame this is; two type shapes for
    the two eras that have to name it.
    """
    return FrameRef(
        uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, VENUE_FRAME_FQN)),
        fqn=VENUE_FRAME_FQN,
        has_coord_convention=True,
        coord_convention=CoordConvention.ENU,
    )
