#!/usr/bin/env python3
"""Ask the planner for a path straight across the pond and look at what it returns."""
import math, sys, time
import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import ComputePathToPose
from rclpy.action import ActionClient
from rclpy.node import Node

sys.path.insert(0, "/ws")
from spatialdds_demo.plaza import GROUND_Z

# The pond the venue declares: x 9.5..20, y -18..-10. Start east of it, aim
# west of it, so the straight line between them crosses the water.
START = (22.0, -8.0)
GOAL = (6.0, -20.0)


def pose(x, y):
    p = PoseStamped()
    p.header.frame_id = "map"
    p.pose.position.x, p.pose.position.y, p.pose.position.z = x, y, GROUND_Z
    p.pose.orientation.w = 1.0
    return p


def main():
    rclpy.init()
    node = Node("plan_probe")
    client = ActionClient(node, ComputePathToPose, "compute_path_to_pose")
    if not client.wait_for_server(timeout_sec=20.0):
        print("  planner action server not available"); return 1

    goal = ComputePathToPose.Goal()
    goal.start = pose(*START)
    goal.goal = pose(*GOAL)
    goal.use_start = True
    future = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, future, timeout_sec=20.0)
    handle = future.result()
    if handle is None or not handle.accepted:
        print("  planner rejected the request"); return 1
    result_future = handle.get_result_async()
    rclpy.spin_until_future_complete(node, result_future, timeout_sec=25.0)
    result = result_future.result()
    if result is None:
        print("  no path returned"); return 1

    path = [(p.pose.position.x, p.pose.position.y) for p in result.result.path.poses]
    if not path:
        print("  planner returned an empty path"); return 1

    # Does it enter the declared water?
    inside = [(x, y) for x, y in path if 9.5 <= x <= 20.0 and -18.0 <= y <= -10.0]
    straight = math.hypot(GOAL[0] - START[0], GOAL[1] - START[1])
    length = sum(math.hypot(path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
                 for i in range(len(path) - 1))
    print(f"  path: {len(path)} poses, {length:.1f} m "
          f"(straight line would be {straight:.1f} m)")
    print(f"  poses inside the declared pond: {len(inside)}")
    print(f"  detour: {length - straight:+.1f} m")
    south = min(y for _, y in path)
    print(f"  furthest south it goes: y={south:.1f} "
          f"(pond's south edge is y=-18.0)")

    def clearance(x, y):
        """Distance to the pond rectangle, 0 if inside."""
        dx = max(9.5 - x, 0.0, x - 20.0)
        dy = max(-18.0 - y, 0.0, y - (-10.0))
        return math.hypot(dx, dy)

    gaps = [clearance(x, y) for x, y in path]
    tight = min(gaps)
    where = path[gaps.index(tight)]
    print(f"  closest the path comes to the water: {tight:.2f} m "
          f"at ({where[0]:.1f}, {where[1]:.1f})")
    near = sum(1 for g in gaps if g < 0.3)
    print(f"  poses within 0.3 m of the water: {near}")
    return 0 if not inside else 2


if __name__ == "__main__":
    sys.exit(main())
