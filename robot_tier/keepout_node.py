#!/usr/bin/env python3
"""
The model's declared bounds, as a nav2 costmap filter.

This is the whole model-to-costmap path, and it is deliberately small. It
fetches the venue's keep-out from the bridge, and publishes two things nav2
understands:

* an **OccupancyGrid** on the mask topic -- where a robot may not go;
* a **CostmapFilterInfo** naming that topic and saying it is a keep-out.

Both are TRANSIENT_LOCAL, because a costmap that starts after this node must
still receive them; the same latching argument the model layer makes, arrived
at independently by nav2.

**Why a filter and not an obstacle layer.** Synthesising a PointCloud2 ring
around each extent would be fake sensor data standing in for a rule, and it
fails mechanically as well as ethically: the obstacle layer raytraces to clear
cells its sensors can see through, so a virtual obstacle producing no real
returns is marked and then promptly cleared. Filters are nav2's mechanism for
policy-derived exclusion, applied alongside the sensor layers rather than
pretending to be one.

    declared bounds  ->  costmap filter mask
    sensed obstacles ->  observation sources

**One process, two DDS stacks.** This node holds a SpatialDDS participant and
a ROS node at once. ROS Humble's middleware is CycloneDDS 0.10.5 and this
repo's Python binding is 11.0.1, which sounds like it could not work and does:
the binding loads its own libddsc from /usr/local and shares no symbols with
rmw_cyclonedds_cpp's. `bridges/ros2_bridge` has done exactly this since long
before Part 4, and is the precedent this follows.

SpatialDDS and ROS meet on the wire because both are DDS -- here, in one
process, with the model read as model and the mask published as ROS.

**The keep-out is law, not perception.** It changes when the venue legislates,
which is rare and never because a robot moved; `cmd_vel` and odom never leave
ROS. The last mask stands until superseded, and no mask is not an empty mask:
publishing a blank grid would tell a planner everywhere is clear on the
authority of a model that said nothing.
"""

import os
import sys
from typing import Optional

import rclpy
from nav2_msgs.msg import CostmapFilterInfo
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

sys.path.insert(0, "/ws")

from spatialdds_demo import typed_transport as tt  # noqa: E402
from spatialdds_demo.keepout import build_mask, describe  # noqa: E402
from spatialdds_demo.qos_profiles import MODEL_LATCHED  # noqa: E402
from spatialdds_demo.topics import TOPIC_MODEL_ENTITY_V1  # noqa: E402
from spatialdds_idl.oarc_model import Entity  # noqa: E402

# The robot's own entity, excluded from its own keep-out. Named here rather
# than imported from robot_bridge so this file's imports stay readable.
ROBOT_ID = "ent:robot:tb3"

MASK_TOPIC = "/keepout_filter_mask"
INFO_TOPIC = "/costmap_filter_info"
MAP_TOPIC = "/map"

# How much clear ground to publish around the declared bounds, as the venue's
# known extent. A costmap needs a canvas: a filter marks inside the window
# other layers ask for, and with nothing else in the costmap there is no
# window -- which is why a correct mask marked nothing at all.
CANVAS_MARGIN_M = 25.0

# The venue frame is the map frame here: the plaza's origin is the frame the
# model publishes in, so nothing has to be transformed between them. A choice
# the sim makes and the README states, not a coincidence.
MAP_FRAME = "map"

# How often to rebuild. The model pushes changes to us; this is the rate at
# which we notice, and law changes rarely.
POLL_S = 1.0

# How much clearance *this robot* wants from the venue's declared bounds.
#
# The law names the region; rendering clearance around it is the consumer's
# job. The extents on the bus are robot-agnostic -- a Roomba and a forklift
# read the same pond and want different room -- so this number lives here,
# beside the thing that has a footprint, and never in the model.
#
# Robot radius (0.22 m in nav2_params.yaml) plus margin. Without it the mask
# is a cliff and a shortest-path planner shaves the boundary: measured at
# 0.10 m from the water, a legal path the controller could not drive.
SHOULDER_M = 0.9

def latched(depth: int = 1) -> QoSProfile:
    qos = QoSProfile(depth=depth)
    qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
    qos.reliability = ReliabilityPolicy.RELIABLE
    return qos


class KeepoutNode(Node):
    def __init__(self, domain_id: int):
        super().__init__("spatialdds_keepout")
        self._mask_pub = self.create_publisher(OccupancyGrid, MASK_TOPIC, latched())
        self._map_pub = self.create_publisher(OccupancyGrid, MAP_TOPIC, latched())
        self._info_pub = self.create_publisher(CostmapFilterInfo, INFO_TOPIC, latched())

        from cyclonedds.domain import DomainParticipant
        self._participant = DomainParticipant(domain_id)
        self._reader = tt.make_reader(
            self._participant, TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED.name)
        self._entities = {}
        self._published_signature = None

        self._publish_info()
        self.create_timer(POLL_S, self._tick)
        self.get_logger().info(
            f"keepout: reading the model on SpatialDDS domain {domain_id}, "
            f"publishing {MASK_TOPIC} into the ROS graph with a "
            f"{SHOULDER_M:.2f} m shoulder of this robot's own")

    def _publish_info(self) -> None:
        info = CostmapFilterInfo()
        info.header.frame_id = MAP_FRAME
        info.header.stamp = self.get_clock().now().to_msg()
        info.type = 0                      # keep-out filter
        info.filter_mask_topic = MASK_TOPIC
        info.base = 0.0
        info.multiplier = 1.0
        self._info_pub.publish(info)

    def _publish_canvas(self, mask) -> None:
        """
        The venue's known extent, all free.

        Not a map of anything: every cell is clear. It exists because a
        costmap filter marks inside the update window that other layers
        request, and a costmap whose only layer is inflation requests
        nothing -- so a perfectly correct mask marked zero cells. The static
        layer gives the costmap a canvas the size of the venue; the filter
        then paints the law onto it.

        Saying that plainly matters: this grid asserts no obstacles and no
        free space that anyone measured. It is a statement about how much
        ground the robot might reason over, not about what is on it.
        """
        canvas = OccupancyGrid()
        canvas.header.frame_id = MAP_FRAME
        canvas.header.stamp = self.get_clock().now().to_msg()
        canvas.info.resolution = mask.resolution
        pad = int(CANVAS_MARGIN_M / mask.resolution)
        canvas.info.width = mask.width + 2 * pad
        canvas.info.height = mask.height + 2 * pad
        canvas.info.origin.position.x = mask.origin_x - CANVAS_MARGIN_M
        canvas.info.origin.position.y = mask.origin_y - CANVAS_MARGIN_M
        canvas.info.origin.orientation.w = 1.0
        canvas.data = [0] * (canvas.info.width * canvas.info.height)
        self._map_pub.publish(canvas)

    def _tick(self) -> None:
        for sample in tt.take_with_state(self._reader):
            if sample.data is None:
                continue
            self._entities[sample.data.entity_id] = sample.data

        mask = build_mask(self._entities.values(), exclude_ids=(ROBOT_ID,),
                          shoulder_m=SHOULDER_M)
        if mask is None:
            return                          # no mask is not an empty mask
        signature = (mask.width, mask.height, mask.origin_x, mask.origin_y,
                     mask.data)
        if signature == self._published_signature:
            return                          # law unchanged; the costmap has it
        self._published_signature = signature

        grid = OccupancyGrid()
        grid.header.frame_id = MAP_FRAME
        grid.header.stamp = self.get_clock().now().to_msg()
        grid.info.resolution = mask.resolution
        grid.info.width = mask.width
        grid.info.height = mask.height
        grid.info.origin.position.x = mask.origin_x
        grid.info.origin.position.y = mask.origin_y
        grid.info.origin.orientation.w = 1.0
        grid.data = list(mask.data)
        self._mask_pub.publish(grid)
        self._publish_info()
        self._publish_canvas(mask)
        self.get_logger().info(f"keepout: {describe(mask)}")


def main() -> int:
    domain = int(os.environ.get("SPATIALDDS_DDS_DOMAIN", "1"))
    rclpy.init()
    node = KeepoutNode(domain)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
