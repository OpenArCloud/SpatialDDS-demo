#!/usr/bin/env python3
"""
The ground truth nav2 drives: odom, tf, and a robot that obeys cmd_vel.

No physics and no sensors. The plaza is flat, the pond is not solid, and the
only thing that stops a robot entering the water is the model saying so --
which is the demonstration. A physics engine here would add wheel slip and
collision response to a scene that has neither, and would obscure the claim
by giving the robot a second reason to avoid things.

**Localization is perfect and says so.** There is no AMCL: with no laser
there is nothing to localize against, and pretending otherwise would be the
same fake-sensor mistake the keep-out design refuses. `map -> odom` is
identity and static; `odom -> base_link` is integrated from the velocities
nav2 commands. A real deployment replaces this node and nothing else.
"""

import math
import os
import sys

import rclpy
from geometry_msgs.msg import Quaternion, Twist, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

sys.path.insert(0, "/ws")

# Plain constants, no DDS: this process cannot load this repo's CycloneDDS.
from spatialdds_demo.plaza import GROUND_Z, ROBOT_START_XY  # noqa: E402

RATE_HZ = 20.0


def yaw_to_quaternion(yaw: float) -> Quaternion:
    q = Quaternion()
    q.z = math.sin(yaw / 2)
    q.w = math.cos(yaw / 2)
    return q


class PlazaSimNode(Node):
    def __init__(self):
        super().__init__("plaza_sim")
        self.x, self.y, self.yaw = ROBOT_START_XY[0], ROBOT_START_XY[1], math.pi
        self.vx = self.wz = 0.0

        self._odom = self.create_publisher(Odometry, "/odom", 10)
        self._tf = TransformBroadcaster(self)
        self._static = StaticTransformBroadcaster(self)
        self.create_subscription(Twist, "/cmd_vel", self._on_cmd, 10)

        # map -> odom, identity and static: this sim knows exactly where the
        # robot is, so there is no drift for a localizer to correct.
        anchor = TransformStamped()
        anchor.header.stamp = self.get_clock().now().to_msg()
        anchor.header.frame_id = "map"
        anchor.child_frame_id = "odom"
        anchor.transform.rotation.w = 1.0
        self._static.sendTransform(anchor)

        self.create_timer(1.0 / RATE_HZ, self._tick)
        self.get_logger().info(
            f"plaza_sim: at ({self.x:.1f}, {self.y:.1f}) in the venue frame; "
            f"map->odom identity, no physics, no sensors")

    def _on_cmd(self, msg: Twist) -> None:
        self.vx, self.wz = msg.linear.x, msg.angular.z

    def _tick(self) -> None:
        dt = 1.0 / RATE_HZ
        self.yaw = (self.yaw + self.wz * dt) % (2 * math.pi)
        self.x += self.vx * dt * math.cos(self.yaw)
        self.y += self.vx * dt * math.sin(self.yaw)
        now = self.get_clock().now().to_msg()

        tf = TransformStamped()
        tf.header.stamp = now
        tf.header.frame_id = "odom"
        tf.child_frame_id = "base_link"
        tf.transform.translation.x = self.x
        tf.transform.translation.y = self.y
        tf.transform.translation.z = GROUND_Z
        tf.transform.rotation = yaw_to_quaternion(self.yaw)
        self._tf.sendTransform(tf)

        odom = Odometry()
        odom.header.stamp = now
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_link"
        odom.pose.pose.position.x = self.x
        odom.pose.pose.position.y = self.y
        odom.pose.pose.position.z = GROUND_Z
        odom.pose.pose.orientation = yaw_to_quaternion(self.yaw)
        odom.twist.twist.linear.x = self.vx
        odom.twist.twist.angular.z = self.wz
        self._odom.publish(odom)


def main() -> int:
    rclpy.init()
    node = PlazaSimNode()
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
