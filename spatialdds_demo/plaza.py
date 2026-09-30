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
# again: from here, in front of the pond, nav2 rounds the *west* end on the
# way to the grass behind it; from (20, -22) it rounded the east. The planner
# takes the nearer end, so this constant silently chooses which way the
# demo's headline route sweeps on camera.
ROBOT_START_XY = (17.0, -21.5)
