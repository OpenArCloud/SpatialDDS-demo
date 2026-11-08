#!/usr/bin/env python3
"""
One plan, and the claims mask it was planned against, as one JSON line.

The claims tooth's ROS-side witness (`tools/claims_tooth.sh`). Asks nav2's
planner for the robot's eastbound route, from its start to (31, -23), the
way `_plan_probe.py` asks for one across the pond, and writes what came
back beside the latest claims mask it received:

    python3 robot_tier/_claims_probe.py LABEL OUT.jsonl

The route is planned on the global costmap, which is where the claims filter
is, so the path is the claim's effect and nothing else's. The mask is read
off the wire, latched, exactly as the costmap's filter receives it, and the
global costmap's own cost at the spill is read back from the costmap server:
the mask is what was offered, the cost is what the planner was given.
"""
import json
import sys
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import ComputePathToPose
from nav2_msgs.srv import GetCostmap
from nav_msgs.msg import OccupancyGrid
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

sys.path.insert(0, "/ws")
from spatialdds_demo.plaza import GROUND_Z, ROBOT_START_XY  # noqa: E402

GOAL = (31.0, -23.0)
MASK = "/spatialdds_robot/claims/mask"
#: tools/hazard_asserter.py's spill, and a point on open paving beside it.
SPILL_CENTRE = (24.0, -23.0)
OPEN_PAVING = (24.0, -27.0)


def costs(node):
    """The global costmap's cost at the spill and beside it, 0..254."""
    client = node.create_client(GetCostmap, "/global_costmap/get_costmap")
    if not client.wait_for_service(timeout_sec=10.0):
        return None
    future = client.call_async(GetCostmap.Request())
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    if future.result() is None:
        return None
    m = future.result().map
    res, w, h = m.metadata.resolution, m.metadata.size_x, m.metadata.size_y
    ox, oy = m.metadata.origin.position.x, m.metadata.origin.position.y

    def at(x, y):
        col, row = int((x - ox) / res), int((y - oy) / res)
        return m.data[row * w + col] if 0 <= col < w and 0 <= row < h else None
    return {"spill_centre": at(*SPILL_CENTRE), "open_paving": at(*OPEN_PAVING)}


def pose(x, y):
    p = PoseStamped()
    p.header.frame_id = "map"
    p.pose.position.x, p.pose.position.y, p.pose.position.z = x, y, GROUND_Z
    p.pose.orientation.w = 1.0
    return p


def main():
    label, out = sys.argv[1], sys.argv[2]
    rclpy.init()
    node = Node("claims_probe")
    masks = []
    latched = QoSProfile(depth=1)
    latched.durability = DurabilityPolicy.TRANSIENT_LOCAL
    latched.reliability = ReliabilityPolicy.RELIABLE
    node.create_subscription(OccupancyGrid, MASK, masks.append, latched)
    for _ in range(20):
        rclpy.spin_once(node, timeout_sec=0.1)
    client = ActionClient(node, ComputePathToPose, "compute_path_to_pose")
    row = {"label": label, "t": round(time.time(), 3)}
    if masks:
        m = masks[-1]
        row["mask"] = {"marked": sum(1 for v in m.data if v),
                       "values": sorted(set(m.data)),
                       "width": m.info.width, "height": m.info.height,
                       "origin": [round(m.info.origin.position.x, 3),
                                  round(m.info.origin.position.y, 3)]}
    else:
        row["mask"] = None
    row["costmap"] = costs(node)
    if not client.wait_for_server(timeout_sec=30.0):
        row["error"] = "planner action server not available"
    else:
        goal = ComputePathToPose.Goal()
        goal.start, goal.goal = pose(*ROBOT_START_XY), pose(*GOAL)
        goal.use_start = True
        future = client.send_goal_async(goal)
        rclpy.spin_until_future_complete(node, future, timeout_sec=20.0)
        handle = future.result()
        if handle is None or not handle.accepted:
            row["error"] = "planner rejected the request"
        else:
            result = handle.get_result_async()
            rclpy.spin_until_future_complete(node, result, timeout_sec=25.0)
            path = result.result().result.path.poses if result.result() else []
            row["path"] = [[round(p.pose.position.x, 3),
                            round(p.pose.position.y, 3)] for p in path]
    with open(out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"claims_probe: {label}: "
          + (row.get("error") or f"{len(row.get('path', []))} poses, mask "
             f"{row['mask']['marked'] if row['mask'] else 'none'} marked"))
    node.destroy_node()
    rclpy.shutdown()
    return 0 if "path" in row else 1


if __name__ == "__main__":
    sys.exit(main())
