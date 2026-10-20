#!/usr/bin/env python3
"""
Facts about the plaza that several processes need, and no bus to get them.

Small, and it exists so there is one definition of where the ground is.
`robot_bridge` stamps it on every pose it publishes, `plaza_sim_node` puts it
on odom and tf, and the probes aim at it. Copying the number into each would
have been the other way, and the wrong one: two grounds, drifting apart the
first time either moved.

It was originally written to dodge a CycloneDDS version conflict between the
ROS tier and this repo, which turned out not to exist -- see CONTRIBUTING on
escalating the error you have. The module stays because the reason above is
the better one.
"""

# The ground, in venue-frame metres.
#
# Measured, rather than reasoned about. Sampling the photorealistic tiles
# along the robot's route round the basin: the south plaza it starts on reads
# z = -1.00, the east and west walkways about -0.90, and the north terrace it
# finishes on about -0.68. One plane cannot be right at all three, and -0.84
# is within 0.16 m of the ground everywhere it goes.
#
# It was -1.9, which is a number the comment above it already disagreed with:
# that comment said the plaza is *higher* than the -1.42 waterline and then
# named a value half a metre lower. The consequence was a robot drawn a metre
# underground for the whole of Part 4 -- glTF loaded, pose correct, marker and
# body both occluded by the tiles, and only the label visible because labels
# draw over everything. Nothing failed. It was found by looking at the demo.
GROUND_Z = -0.84

# Where the robot starts: the south plaza, clear of the water the venue
# declares. That is a test, not a hope -- `test_the_robot_starts_outside_
# every_keep_out` builds the mask from the seed and asks it.
#
# It has moved three times. The first two are the same lesson from different
# sides. It was (22, -8), which is not in the *declared* pond but is in real
# water -- the declaration was smaller than the pool, so nothing in the model
# could say the robot had spawned afloat. Then it was (16, -7), a dry strip
# between the pool's two northern arms, which stopped being usable the moment
# the pond was declared honestly: the box around a U-shaped pool contains its
# bays, so the venue now forbids ground that is genuinely dry. That is the
# cost of a rectangle, it is paid in the safe direction, and it is visible
# here rather than hidden.
#
# The third move is presentational, and worth knowing before changing this
# again: from (17, -21.5), in front of the pond, nav2 rounded the *west* end
# on the way to the grass behind it; from (20, -22) it rounded the east. The
# planner takes the nearer end, so this constant silently chooses which way
# the demo's headline route sweeps on camera.
#
# The fourth move, 2026-10-19, is about room. The declared box's south edge
# is y = -20.00 and the kinematic tier clears 1.2 m, so the legal band south
# of the water begins at y = -21.20 -- and y = -21.5 put the robot 0.30 m
# into it, against a wall that spans the venue. In a live session it had
# drifted to y = -21.27, leaving 0.07 m: every goal with any northward
# component was refused on the first step, including a 0.7 m nudge onto
# ground that is plainly dry on screen. That reads as a robot that cannot
# move at all rather than one declining the water, which is the complaint
# this has generated three times.
#
# y = -23.0 gives 1.80 m of northward room and raises one-tap reachability
# from 185/553 cells to 192/553. The gain is small because the wall still
# spans the plaza -- the room is the point, not the reachability.
#
# **x is deliberately unchanged**, so the west/east choice above is not
# disturbed; it was not re-measured, because that needs the nav2 tier up.
ROBOT_START_XY = (17.0, -23.0)


# --- the venue's georeference -----------------------------------------------
#
# Where `map/ut-littlefield-fountain` sits on the earth, as the *scene's* own
# claim.
#
# The catalogue server announces a transform for the same frame, derived from
# `SPATIALDDS_FRAME_ANCHORS`, and it keeps doing so: that is a different claim
# --- where its *map* sits --- which coincides with this one today. Two
# services describing one frame is the same arrangement the venue already has
# for the pond, where a DECLARED box and a DERIVED observation disagree on
# purpose and neither is crowned. See docs/venue-georeference-census.md.
#
# The numbers are the anchor the OpenVPS map was built against, copied here as
# the venue's own statement rather than read from the catalogue's config: the
# scene has to be able to say where it is without another service's file being
# present. Height is WGS84 ellipsoidal, and the fix is visual rather than
# surveyed --- aligning the capture's LiDAR cloud against photorealistic
# tiles --- which is why `method` is VisualFix wherever this is reported.
VENUE_ANCHOR_LAT_DEG = 30.28396857092828
VENUE_ANCHOR_LON_DEG = -97.73974829101985
VENUE_ANCHOR_HEIGHT_M = 145.083

#: The global frame a local ENU frame is placed in. Not an axis convention the
#: `CoordConvention` enum can name, which is why every FrameRef for it carries
#: `has_coord_convention = false`.
EARTH_FIXED_FQN = "earth-fixed"


def enu_to_ecef_pose(lat_deg: float, lon_deg: float, height_m: float):
    """A local ENU frame's origin and basis, in earth-fixed metres.

    Returns ``(t, q)``: the translation is the frame origin's ECEF position,
    and the quaternion (x, y, z, w) is the ENU basis at that origin. So the
    pose maps **from** the local frame **into** earth-fixed.

    Shared rather than copied, for this module's existing reason. The venue and
    the catalogue make *different claims* about the same frame, and that is
    deliberate --- but a difference between them should come from their
    anchors, which are separate on purpose, never from two implementations of
    the projection drifting apart. One of the ways this transform goes wrong is
    silent: an inverted direction still produces a plausible pose and places
    everything at the centre of the earth.
    """
    import math

    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    # WGS84.
    a, f = 6378137.0, 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    t = [(n + height_m) * math.cos(lat) * math.cos(lon),
         (n + height_m) * math.cos(lat) * math.sin(lon),
         (n * (1.0 - e2) + height_m) * math.sin(lat)]

    # Columns are the ENU axes expressed in ECEF.
    sl, cl = math.sin(lon), math.cos(lon)
    sp, cp = math.sin(lat), math.cos(lat)
    r = [[-sl, -sp * cl, cp * cl],
         [cl, -sp * sl, cp * sl],
         [0.0, cp, sp]]

    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0.0:
        sc = math.sqrt(trace + 1.0) * 2.0
        qw, qx = 0.25 * sc, (r[2][1] - r[1][2]) / sc
        qy, qz = (r[0][2] - r[2][0]) / sc, (r[1][0] - r[0][1]) / sc
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        sc = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2.0
        qw, qx = (r[2][1] - r[1][2]) / sc, 0.25 * sc
        qy, qz = (r[0][1] + r[1][0]) / sc, (r[0][2] + r[2][0]) / sc
    elif r[1][1] > r[2][2]:
        sc = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2.0
        qw, qx = (r[0][2] - r[2][0]) / sc, (r[0][1] + r[1][0]) / sc
        qy, qz = 0.25 * sc, (r[1][2] + r[2][1]) / sc
    else:
        sc = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2.0
        qw, qx = (r[1][0] - r[0][1]) / sc, (r[0][2] + r[2][0]) / sc
        qy, qz = (r[1][2] + r[2][1]) / sc, 0.25 * sc

    return t, [qx, qy, qz, qw]
