#!/usr/bin/env python3
"""
A stranger's entity, published by a stranger's process.

    python3 scripts/gnome_publisher.py

This is deliberately *not* the model service. It is a second, independent
publisher writing to the same topics, which is the situation the layer has to
survive in the field: you do not get to assume one authority, one vocabulary,
or one deployment. Everything the demo knows about this thing arrives on the
bus and nothing else.

Two things it is built to prove.

**An unknown type must render.** The gnome's `type_uris` names a vocabulary
this client has never seen and cannot resolve -- deliberately fictional and
deliberately unresolvable. A client that only draws things it recognises is a
client that hides the world from its user; the honest behaviour is to place it
correctly, say what little is known, and carry on. Nothing about the ducks or
the fountain may change because a stranger showed up.

**A second writer is normal.** It publishes with its own `source_id`, on the
same latched topics, and a consumer sees one model rather than two feeds.

It carries no `demo.label`, on purpose: a stranger has no reason to speak this
demo's property conventions, so the client has to fall back to something
honest rather than to a name it was handed.
"""

import argparse
import signal
import sys
import time
import uuid
from typing import Optional

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.model_service import VENUE_FRAME_FQN, venue_frame
from spatialdds_demo.qos_profiles import MODEL_LATCHED
from spatialdds_demo.topics import TOPIC_MODEL_ENTITY_V1
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import (
    Basis, Entity, LifecycleState, ModelLayer,
)
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

ENTITY_ID = "ent:gnome:visitor"

# A different publisher, and it says so. `svc:<kind>:<org>/<name>` per the
# convention the rest of the demo follows.
SOURCE_ID = "svc:model:visitor/garden-ornaments"

# Well-formed, obviously fictional, and deliberately unresolvable. The point
# is what the client does with a type it cannot look up, not what the URI says.
TYPE_GARDEN_GNOME = "https://example.org/vocab/garden-gnome"

# Venue-frame metres. On the plaza at the south-east corner of the basin --
# beside the water rather than in it, where the tiles put the surface about a
# metre above the waterline.
POSE_T = [20.0, -21.0, -0.98]


def gnome(stamp: Optional[Time] = None) -> Entity:
    now = time.time()
    stamp = stamp or Time(sec=int(now), nanosec=int((now % 1) * 1e9))
    return Entity(
        entity_id=ENTITY_ID,
        # Somebody put it there. Nothing observed it.
        basis=Basis.AUTHORED,
        type_uris=[TYPE_GARDEN_GNOME],
        layer=ModelLayer.SLOW,
        # The same venue frame, by the same UUIDv5 derivation. A stranger who
        # names the frame correctly is placeable; one who does not is not, and
        # the client declines rather than guessing -- that is already tested.
        frame_ref=venue_frame(),
        has_pose=True,
        pose=PoseSE3(t=list(POSE_T), q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=False,
        extent=Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0]),
        # No `demo.label`: this publisher does not know that convention.
        properties=[],
        external_refs=[],
        # No asset. There is no gnome model to draw, which is the ordinary
        # case for an entity that is a fact about the world rather than
        # something to render.
        content_refs=[],
        state=LifecycleState.ACTIVE,
        state_reason="",
        source_id=SOURCE_ID,
        stamp=stamp,
    )


def run(domain_id: Optional[int] = None) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)
    writer = tt.make_writer(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)

    entity = gnome()
    writer.write(entity)
    print(f"gnome: domain {domain_id}, source {SOURCE_ID}")
    print(f"gnome: {entity.entity_id} — {entity.basis.name}/{entity.layer.name}, "
          f"pose ({POSE_T[0]:.2f}, {POSE_T[1]:.2f}, {POSE_T[2]:.2f}) "
          f"in {VENUE_FRAME_FQN}")
    print(f"gnome: type {TYPE_GARDEN_GNOME} — no client here can resolve this")
    print("gnome: latched, holding")

    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while not stop:
        time.sleep(0.5)

    # Stay a good citizen on the way out, the same as the model service.
    try:
        writer.dispose(entity)
    except Exception:
        pass
    print("gnome: disposed and exiting")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish one entity of an unknown type, as a second writer")
    parser.add_argument("--domain", type=int, default=None)
    args = parser.parse_args()
    return run(args.domain)


if __name__ == "__main__":
    sys.exit(main())
