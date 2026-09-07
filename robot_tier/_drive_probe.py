#!/usr/bin/env python3
"""Send a NavigateToPose across the pond and watch odom move."""
import math, sys, time
import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.node import Node

sys.path.insert(0, "/ws")
from spatialdds_demo.plaza import GROUND_Z

GOAL = (6.0, -20.0)


def main():
    rclpy.init()
    node = Node("drive_probe")
    seen = []
    node.create_subscription(Odometry, "/odom",
                             lambda m: seen.append((m.pose.pose.position.x,
                                                    m.pose.pose.position.y)), 10)
    client = ActionClient(node, NavigateToPose, "navigate_to_pose")
    if not client.wait_for_server(timeout_sec=20.0):
        print("  navigate_to_pose not available"); return 1

    for _ in range(20):
        rclpy.spin_once(node, timeout_sec=0.1)
    start = seen[-1] if seen else None
    print(f"  start: ({start[0]:.2f}, {start[1]:.2f})" if start else "  no odom")

    goal = NavigateToPose.Goal()
    goal.pose.header.frame_id = "map"
    goal.pose.pose.position.x, goal.pose.pose.position.y = GOAL
    goal.pose.pose.position.z = GROUND_Z
    goal.pose.pose.orientation.w = 1.0
    future = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, future, timeout_sec=20.0)
    handle = future.result()
    if handle is None or not handle.accepted:
        print("  goal rejected"); return 1
    print("  goal accepted by nav2")

    deadline = time.time() + 90
    track = []
    while time.time() < deadline:
        rclpy.spin_once(node, timeout_sec=0.2)
        if seen:
            track.append(seen[-1])
            x, y = seen[-1]
            if math.hypot(x - GOAL[0], y - GOAL[1]) < 0.6:
                break

    x, y = seen[-1]
    inside = [(a, b) for a, b in track if 9.5 <= a <= 20.0 and -18.0 <= b <= -10.0]
    print(f"  ended:  ({x:.2f}, {y:.2f})  after {len(track)} odom samples")
    print(f"  distance to goal: {math.hypot(x - GOAL[0], y - GOAL[1]):.2f} m")
    print(f"  odom samples inside the declared pond: {len(inside)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
