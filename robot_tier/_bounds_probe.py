#!/usr/bin/env python3
"""
Does the costmap's update window ever cover the pond?

CostmapFilter::updateBounds exists but a filter is not a source of bounds --
it marks inside whatever window other layers ask for. With inflation as the
only layer there may be no window at all, which would explain a correct mask
marking nothing. Publishing an all-free map as a static layer would give the
costmap a canvas; this checks whether that is the missing piece by watching
what the costmap publishes.
"""
import sys
import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy


def main():
    rclpy.init()
    node = Node("bounds_probe")
    got = []
    qos = QoSProfile(depth=1)
    qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
    qos.reliability = ReliabilityPolicy.RELIABLE
    node.create_subscription(OccupancyGrid, "/global_costmap/costmap",
                             lambda m: got.append(m), qos)
    for _ in range(120):
        rclpy.spin_once(node, timeout_sec=0.1)
        if got:
            break
    if not got:
        print("  the global costmap publishes nothing")
        return 1
    m = got[-1]
    data = list(m.data)
    print(f"  global costmap {m.info.width}x{m.info.height} at "
          f"{m.info.resolution:.2f} m, origin "
          f"({m.info.origin.position.x:.1f}, {m.info.origin.position.y:.1f})")
    print(f"  distinct values: {sorted(set(data))[:8]}")
    print(f"  non-zero cells: {sum(1 for v in data if v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
