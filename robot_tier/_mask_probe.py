#!/usr/bin/env python3
"""What is actually in the mask, as received from the wire."""
import sys
import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy


def main():
    rclpy.init()
    node = Node("mask_probe")
    got = []
    qos = QoSProfile(depth=1)
    qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
    qos.reliability = ReliabilityPolicy.RELIABLE
    node.create_subscription(OccupancyGrid, "/keepout_filter_mask",
                             lambda m: got.append(m), qos)
    for _ in range(80):
        rclpy.spin_once(node, timeout_sec=0.1)
        if got:
            break
    if not got:
        print("  no mask received")
        return 1
    m = got[0]
    data = list(m.data)
    print(f"  cells {len(data)}, distinct values {sorted(set(data))[:6]}")
    print(f"  cells at 100: {sum(1 for v in data if v == 100)}")
    res = m.info.resolution
    ox, oy = m.info.origin.position.x, m.info.origin.position.y
    w, h = m.info.width, m.info.height
    for name, (x, y) in (("pond centre", (14.0, -14.0)), ("plaza", (23.0, -8.0))):
        col, row = int((x - ox) / res), int((y - oy) / res)
        if 0 <= col < w and 0 <= row < h:
            print(f"  {name}: mask value {data[row * w + col]}")
        else:
            print(f"  {name}: outside the mask ({col},{row} of {w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
