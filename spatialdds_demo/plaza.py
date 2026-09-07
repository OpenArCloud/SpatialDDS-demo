#!/usr/bin/env python3
"""
Facts about the plaza that several processes need, and no bus to get them.

Small, and it exists for a specific reason: the ROS side of the robot tier
runs a different major version of CycloneDDS than this repo does, so a node
over there cannot import anything that pulls in the DDS binding. Reaching into
`robot_bridge` for two numbers dragged in `cyclonedds` and failed at start.

Copying the numbers into the ROS node would have been the other way to solve
it, and the wrong one -- two definitions of where the ground is, drifting
apart the first time either moved. So the facts live here, importable from
either world, and `robot_bridge` re-exports them under the names it already
used.
"""

# The ground, in venue-frame metres. The water surface measured off the tiles
# sits at about -1.42; the plaza around it is a little higher, and this is
# where a wheeled thing rests.
GROUND_Z = -1.9

# Where the robot starts: on the plaza east of the basin, outside every
# declared keep-out. That last part is a test, not a hope -- under the earlier
# keep-out policy this point was inside the fountain's bounding box and the
# robot began life in a forbidden cell.
ROBOT_START_XY = (22.0, -8.0)
