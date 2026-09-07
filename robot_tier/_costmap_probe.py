#!/usr/bin/env python3
"""Ask both costmaps what they think of a point in the middle of the pond."""
import sys, time
import rclpy
from nav2_msgs.srv import GetCostmap
from rclpy.node import Node

POND_POINT = (14.0, -14.0)     # squarely inside the declared water
PLAZA_POINT = (23.0, -8.0)     # squarely outside it


def sample(node, service, label):
    client = node.create_client(GetCostmap, service)
    if not client.wait_for_service(timeout_sec=10.0):
        print(f"  {label}: no service"); return
    future = client.call_async(GetCostmap.Request())
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    result = future.result()
    if result is None:
        print(f"  {label}: no response"); return
    m = result.map
    res = m.metadata.resolution
    ox = m.metadata.origin.position.x
    oy = m.metadata.origin.position.y
    w, h = m.metadata.size_x, m.metadata.size_y
    lethal = sum(1 for v in m.data if v >= 253)
    print(f"  {label}: {w}x{h} at {res:.2f} m, origin ({ox:.1f}, {oy:.1f}), "
          f"{lethal} lethal cells")
    for name, (x, y) in (("pond", POND_POINT), ("plaza", PLAZA_POINT)):
        col, row = int((x - ox) / res), int((y - oy) / res)
        if 0 <= col < w and 0 <= row < h:
            print(f"    {name} {x, y}: cost {m.data[row * w + col]}")
        else:
            print(f"    {name} {x, y}: outside this costmap")


def main():
    rclpy.init()
    node = Node("costmap_probe")
    sample(node, "/global_costmap/get_costmap", "global")
    sample(node, "/local_costmap/get_costmap", "local ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
