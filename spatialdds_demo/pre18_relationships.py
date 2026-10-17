"""
The retired relationship lane, kept for the consumers that have not migrated.

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

When the bridge migrates, this file goes with it. It is a dated artifact by
construction, which is why it says so at the top rather than looking like
part of the demo.
"""

from typing import List, Optional

from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import Entity, Relationship

from spatialdds_demo.model_service import SOURCE_ID, _now


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
