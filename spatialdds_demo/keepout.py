#!/usr/bin/env python3
"""
Model bounds to a costmap keepout mask: policy, not perception.

A robot must not drive into the pond. The pond is not solid, no sensor
reports it, and nothing about it is physically there -- it is a *declaration*
made by the venue, and the robot avoids it because the model says so.

**The mechanism has to match the claim.** The obvious route was to synthesise
a PointCloud2 ring around each extent and feed nav2's obstacle layer. That is
fake sensor data standing in for a rule, which is the same category error as
a tool printing "moved" when it means "asked", wearing a lidar costume. It
also fails mechanically: the obstacle layer raytraces to *clear* cells that
its sensors can see through, so a virtual obstacle producing no real returns
gets marked and then promptly cleared as free space by the next scan.

nav2 has the right mechanism already. A **costmap filter** takes an
OccupancyGrid mask and applies it as policy, alongside the sensor-derived
layers rather than pretending to be one. So:

    declared bounds  ->  costmap filter mask
    sensed obstacles ->  observation sources

and a model bridge must never launder one into the other. That is the first
line of a model-to-costmap contract, and it is a rule about provenance rather
than about geometry.

**Only DECLARED contributes.** *OBSERVED locates; DECLARED legislates.*

That is narrower than it first looks, and the reason is worth keeping. An
`Aabb3` on an entity answers "where is this thing"; a costmap needs "where may
I not go". For the pond those coincide -- the venue declared a boundary, a
little inside the waterline, precisely so that something trusting it stays
out of the water. For the fountain they do not: its extent is the basin's
bounding box, and the seeder says so outright -- *"coarse on purpose... a
claim about where the fountain is, not a model of its geometry."*

Taking that box as keep-out forbade 916 m2, most of it dry walkable plaza,
and swallowed the pond entirely: the pond's 84 m2 added exactly nothing, so
reshaping the water changed the robot's world by zero cells. The test that
found it asserted the delta and reported `92258 not less than 92258`.

So an extent only becomes keep-out when the venue *declared* it. What the
model cannot yet say is which kind of claim an extent is -- a locator or a
boundary -- and `basis` is standing in for that here because a venue declaring
bounds is usually legislating while a sensor bounding a thing is usually
locating. That is a real gap and it is recorded in SPEC_COMPLIANCE rather than
patched with a field mid-part.

AUTHORED and DERIVED are excluded for their own reasons, and both matter: a
rubber duck placed by whoever felt like it must not be able to close a route,
and a second party's opinion about where the water is must not be able to
widen one. All three exclusions are consequences of `basis` being on the
entity, which is the argument for having it there at all.

**A robot never avoids itself.** Its own entity is excluded by id.
"""

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

# The bases whose extents become keep-out. See the module docstring: this is
# the whole policy, and it is one word long because `basis` carries it.
KEEPOUT_BASES = ("DECLARED",)

# Metres per cell. 10 cm is nav2's usual costmap resolution and is finer than
# any bound in this venue is accurate to.
RESOLUTION_M = 0.10

# How much clear space to leave around the outermost bound, so a planner has
# somewhere to route through rather than starting against the edge of its own
# map.
MARGIN_M = 4.0

FREE = 0
OCCUPIED = 100

# A shoulder: cells outside a declared bound but close enough that a robot
# should prefer not to be there. Rendered by the *consumer*, never by the
# venue -- see `build_mask`.
SHOULDER_MIN = 1


@dataclass(frozen=True)
class KeepoutMask:
    """
    An OccupancyGrid, in the fields nav2's map server wants.

    Origin is the south-west corner in venue-frame metres; `data` is row-major
    from that corner, which is the ROS convention and the opposite of how an
    image is usually stored.
    """
    resolution: float
    width: int
    height: int
    origin_x: float
    origin_y: float
    data: bytes
    contributors: Tuple[str, ...]

    def cell(self, x: float, y: float) -> int:
        """Occupancy at a venue-frame point, or FREE outside the mask."""
        col = int((x - self.origin_x) / self.resolution)
        row = int((y - self.origin_y) / self.resolution)
        if not (0 <= col < self.width and 0 <= row < self.height):
            return FREE
        return self.data[row * self.width + col]

    @property
    def occupied_cells(self) -> int:
        return sum(1 for v in self.data if v == OCCUPIED)


def contributing(entities: Iterable, exclude_ids: Sequence[str] = ()) -> List:
    """
    The entities whose extents become keep-out.

    Everything else is filtered here rather than in the rasteriser, so that
    "which claims constrain a robot" is one readable list rather than a
    condition buried in a loop.
    """
    excluded = set(exclude_ids)
    out = []
    for entity in entities:
        if entity.entity_id in excluded:
            continue                        # a robot does not avoid itself
        if not getattr(entity, "has_extent", False):
            continue                        # a point is not a region
        if entity.state.name != "ACTIVE":
            continue                        # retired or unobserved: not a fact
        if entity.basis.name not in KEEPOUT_BASES:
            continue                        # located, not legislated
        out.append(entity)
    return out


def build_mask(entities: Iterable, exclude_ids: Sequence[str] = (),
               resolution: float = RESOLUTION_M,
               margin: float = MARGIN_M,
               shoulder_m: float = 0.0) -> Optional[KeepoutMask]:
    """
    Rasterise the contributing extents into an OccupancyGrid mask.

    Returns None when nothing contributes: an empty mask and no mask are
    different statements, and publishing a blank one would tell a planner
    "everywhere is clear" on the authority of a model that said nothing.

    **`shoulder_m` is the consumer's, not the venue's.** A binary mask makes a
    region illegal, not undesirable, and a shortest-path planner shaves an
    illegal boundary by design -- measured at 0.10 m from the water, which is
    a legal path no robot can drive. Inflation does not help: nav2 applies
    filters *after* the layer stack, so filter-marked cells never enter the
    inflation computation, and the keep-out is a cliff -- cost 0 at 0.11 m,
    cost 254 at 0.10 m. Doubling the inflation radius moved the failure point
    by one centimetre, which was the pipeline saying the knob was not
    connected.

    A shoulder gives the planner a reason to bow away: the declared extent
    stays lethal, and a band outside it ramps down, so being near the water
    costs something without being forbidden.

    The width belongs to whoever is driving. The venue's extents are
    robot-agnostic -- a Roomba and a forklift render different shoulders from
    the same pond -- so the law names the region and the consumer renders its
    own clearance around it. That is why the bridge's inspection endpoint
    passes no shoulder: it shows the law, not one robot's reading of it.
    """
    keep = contributing(entities, exclude_ids)
    if not keep:
        return None

    shoulder = max(0.0, shoulder_m)
    min_x = min(e.extent.min_xyz[0] for e in keep) - margin
    min_y = min(e.extent.min_xyz[1] for e in keep) - margin
    max_x = max(e.extent.max_xyz[0] for e in keep) + margin
    max_y = max(e.extent.max_xyz[1] for e in keep) + margin

    width = max(1, int(math.ceil((max_x - min_x) / resolution)))
    height = max(1, int(math.ceil((max_y - min_y) / resolution)))
    data = bytearray([FREE]) * (width * height)

    for entity in keep:
        lo_x, lo_y = entity.extent.min_xyz[0], entity.extent.min_xyz[1]
        hi_x, hi_y = entity.extent.max_xyz[0], entity.extent.max_xyz[1]

        # The shoulder first, so the lethal core overwrites it where they meet
        # and a cell inside two extents never ends up merely discouraged.
        if shoulder > 0:
            scol0 = max(0, int((lo_x - shoulder - min_x) / resolution))
            scol1 = min(width - 1, int((hi_x + shoulder - min_x) / resolution))
            srow0 = max(0, int((lo_y - shoulder - min_y) / resolution))
            srow1 = min(height - 1, int((hi_y + shoulder - min_y) / resolution))
            for row in range(srow0, srow1 + 1):
                cy = min_y + (row + 0.5) * resolution
                dy = max(lo_y - cy, 0.0, cy - hi_y)
                base = row * width
                for col in range(scol0, scol1 + 1):
                    cx = min_x + (col + 0.5) * resolution
                    dx = max(lo_x - cx, 0.0, cx - hi_x)
                    gap = math.hypot(dx, dy)
                    if gap <= 0.0 or gap > shoulder:
                        continue
                    # Linear ramp: just outside the bound is nearly as bad as
                    # inside it; at the shoulder's edge it costs almost
                    # nothing. Linear because a planner only needs a gradient
                    # to lean on, and a curve here would be a claim about
                    # dynamics this module has no business making.
                    value = int(round(OCCUPIED * (1.0 - gap / shoulder)))
                    value = max(SHOULDER_MIN, min(OCCUPIED - 1, value))
                    if value > data[base + col]:
                        data[base + col] = value

        col0 = max(0, int((lo_x - min_x) / resolution))
        col1 = min(width - 1, int((hi_x - min_x) / resolution))
        row0 = max(0, int((lo_y - min_y) / resolution))
        row1 = min(height - 1, int((hi_y - min_y) / resolution))
        for row in range(row0, row1 + 1):
            base = row * width
            for col in range(col0, col1 + 1):
                data[base + col] = OCCUPIED

    return KeepoutMask(
        resolution=resolution, width=width, height=height,
        origin_x=min_x, origin_y=min_y, data=bytes(data),
        contributors=tuple(sorted(e.entity_id for e in keep)))


def describe(mask: Optional[KeepoutMask]) -> str:
    if mask is None:
        return "no mask: nothing in the model constrains a robot"
    area = mask.occupied_cells * mask.resolution ** 2
    shoulder = sum(1 for v in mask.data if 0 < v < OCCUPIED)
    band = (f", {shoulder * mask.resolution ** 2:.0f} m2 of shoulder"
            if shoulder else "")
    return (f"{mask.width}x{mask.height} cells at {mask.resolution:.2f} m, "
            f"origin ({mask.origin_x:.1f}, {mask.origin_y:.1f}), "
            f"{area:.0f} m2 keep-out{band} from {', '.join(mask.contributors)}")
