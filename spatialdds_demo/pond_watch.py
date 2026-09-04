#!/usr/bin/env python3
"""
A second opinion about the same water.

    python3 -m spatialdds_demo.pond_watch

`svc:fusion:demo/pondwatch` publishes `ent:pond:observed`: its own entity, in
the same venue frame, describing the same pond the venue declares -- and
disagreeing with it. Basis DERIVED, because this service computes bounds from
observations rather than being told them.

**Neither pond is crowned.** The model carries both, each saying who published
it and how the claim was arrived at, and a consumer decides which to believe.
The mover takes `--bounds declared|derived` for exactly this reason: choosing
whom to trust is a policy a consumer holds, not a fact the model settles.
Run it against the other bounds and the ducks roam differently, which is the
whole demonstration.

**No edge joins the two.** They are visibly about the same water and nothing
in the model says so. `Relationship` carries `source_id`, so an edge could say
who claimed it; what it cannot carry is a `basis`, so it could not say whether
identity was asserted, computed, or assumed. For an identity claim that is the
load-bearing half, and an edge that cannot express it launders an assumption
into the model. That is the R10 conversation; see SPEC_COMPLIANCE.

Why they differ, concretely: the venue declared bounds a little inside the
waterline, conservatively, so that anything trusting them stays wet. This
service reports what it thinks it sees, which is a slightly larger pond with
a wobble on it. The disagreement is therefore legible rather than a rounding
error -- one is cautious, the other is measured, and a viewer can see which
is which.
"""

import argparse
import math
import random
import signal
import sys
import time
from typing import Optional

from cyclonedds.domain import DomainParticipant

from spatialdds_demo import typed_transport as tt
from spatialdds_demo.dds_transport import require_dds_env
from spatialdds_demo.model_service import (
    POND_MAX, POND_MIN, TYPE_BASIN, venue_frame,
)
from spatialdds_demo.qos_profiles import MODEL_LATCHED
from spatialdds_demo.topics import TOPIC_MODEL_ENTITY_V1
from spatialdds_idl.builtin import Time
from spatialdds_idl.oarc_model import (
    Basis, Entity, LifecycleState, ModelLayer,
)
from spatialdds_idl.spatial.common import KV
from spatialdds_idl.spatial.core import Aabb3, PoseSE3

ENTITY_ID = "ent:pond:observed"
SOURCE_ID = "svc:fusion:demo/pondwatch"

# What this service thinks it sees: the water reaches a little further than
# the venue's cautious declaration in every direction.
MARGIN_M = 0.9

# And it is not certain. Each observation wobbles by up to this much, because
# a thing that reports identical bounds forever is not observing anything --
# it is remembering. The wobble is what makes DERIVED worth distinguishing
# from DECLARED on screen.
WOBBLE_M = 0.25

# Slow, because the shape of a pond is not news. Fast enough that a viewer
# watching for a minute sees it breathe.
PERIOD_S = 5.0


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now % 1) * 1e9))


def observed_extent(rng: random.Random) -> Aabb3:
    """The venue's bounds, grown by the margin, with a wobble on each edge."""
    def edge(value: float, outward: float) -> float:
        return value + outward * MARGIN_M + rng.uniform(-WOBBLE_M, WOBBLE_M)
    return Aabb3(
        min_xyz=[edge(POND_MIN[0], -1), edge(POND_MIN[1], -1), POND_MIN[2]],
        max_xyz=[edge(POND_MAX[0], +1), edge(POND_MAX[1], +1), POND_MAX[2]])


def observation(rng: random.Random, stamp: Optional[Time] = None) -> Entity:
    extent = observed_extent(rng)
    centre = [(extent.min_xyz[i] + extent.max_xyz[i]) / 2 for i in range(3)]
    return Entity(
        entity_id=ENTITY_ID,
        # DERIVED: computed from observations. Not OBSERVED, which in this
        # demo means a thing is in the capture the map was built from; this
        # service produces a figure, not a sighting.
        basis=Basis.DERIVED,
        type_uris=[TYPE_BASIN],
        # SLOW, not STATIC: it is republished, and a consumer that treats it
        # as never-changing would miss the disagreement moving.
        layer=ModelLayer.SLOW,
        frame_ref=venue_frame(),
        has_pose=True,
        pose=PoseSE3(t=centre, q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=True,
        extent=extent,
        properties=[KV(key="demo.label", value="Pond"),
                    KV(key="demo.note",
                       value="The same water as ent:pond:littlefield, measured rather than declared. Nothing in the model says the two are the same thing: an edge could say who claimed it, but not whether identity was asserted, computed or assumed, and Relationship has no basis to carry that.")],
        external_refs=[],
        content_refs=[],
        state=LifecycleState.ACTIVE,
        state_reason="",
        source_id=SOURCE_ID,
        stamp=stamp or _now(),
    )


def run(domain_id: Optional[int] = None, seed: Optional[int] = None) -> int:
    domain_id = require_dds_env() if domain_id is None else domain_id
    participant = DomainParticipant(domain_id)
    writer = tt.make_writer(
        participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
    rng = random.Random(seed)

    print(f"pondwatch: domain {domain_id}, source {SOURCE_ID}")
    print(f"pondwatch: publishing {ENTITY_ID} — DERIVED, every {PERIOD_S}s")
    print(f"pondwatch: it disagrees with the venue by about {MARGIN_M} m, "
          f"on purpose")

    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    published = 0
    last = 0.0
    while not stop:
        if time.time() - last >= PERIOD_S:
            entity = observation(rng)
            writer.write(entity)
            last = time.time()
            published += 1
            if published == 1 or published % 12 == 0:
                print(f"pondwatch: {published} observations; latest "
                      f"x {entity.extent.min_xyz[0]:.2f}.."
                      f"{entity.extent.max_xyz[0]:.2f}, "
                      f"y {entity.extent.min_xyz[1]:.2f}.."
                      f"{entity.extent.max_xyz[1]:.2f}", flush=True)
        time.sleep(0.2)

    try:
        writer.dispose(observation(rng))
    except Exception:
        pass
    print("pondwatch: disposed and exiting")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish an observed opinion of the pond's bounds")
    parser.add_argument("--domain", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None,
                        help="fix the wobble, for tests")
    args = parser.parse_args()
    return run(args.domain, args.seed)


if __name__ == "__main__":
    sys.exit(main())
