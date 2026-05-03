"""Unit tests for ``TrajectoryConflictDetector``.

No DDS, no fusion algorithm. We feed synthetic ``PlannedTrajectory``
payloads (built via ``spatialdds_types`` helpers) directly and assert
that the right pairs are flagged.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from spatialdds_types import (  # noqa: E402
    make_planned_trajectory,
    make_planned_waypoint,
)


def _import_detector():
    """Import only what we need from fusion_service without dragging in
    its DDS-side imports. The ``fusion`` module dependency does NOT need
    DDS, but ``time``/``json`` are fine; importing the whole module is
    OK on the host."""
    from fusion_service import TrajectoryConflictDetector
    return TrajectoryConflictDetector


def _straight_line_traj(agent_id: str, x0: float, y0: float,
                          vx: float, vy: float,
                          t0: float = 0.0, n: int = 10, dt: float = 0.5):
    """Build a constant-velocity trajectory starting at (x0, y0) at time t0."""
    waypoints = []
    for i in range(1, n + 1):
        wpt = t0 + i * dt
        waypoints.append(make_planned_waypoint(
            x=x0 + vx * i * dt,
            y=y0 + vy * i * dt,
            z=0.0,
            timestamp_s=wpt,
            vx=vx, vy=vy,
            uncertainty_m=0.5, confidence=0.9,
        ))
    return make_planned_trajectory(
        agent_id=agent_id, plan_id=f"plan_{agent_id}", plan_revision=0,
        frame_ref_fqn=f"{agent_id}/map",
        waypoints=waypoints,
        horizon_sec=n * dt, replan_rate_hz=2.0,
        timestamp_s=t0,
    )


class TestTrajectoryConflict(unittest.TestCase):

    def setUp(self):
        Detector = _import_detector()
        self.det = Detector(conflict_distance_m=5.0, time_tolerance_s=0.4)

    def test_crossing_paths_flag_a_conflict(self):
        """Operator A north-bound (0,-30 → 0,+20) crosses operator B
        west-bound (30,0 → -20,0) at (0,0) around t=6 s. Both plans
        publish at t=2 with horizon 5 s, so each has a waypoint at
        roughly (0,0,t=6). The detector should flag (A, B)."""
        a = _straight_line_traj("op_a_ego", x0=0, y0=-20,
                                  vx=0, vy=5, t0=2.0)
        b = _straight_line_traj("op_b_ego", x0=20, y0=0,
                                  vx=-5, vy=0, t0=2.0)
        self.det.update(a, received_at=2.0)
        self.det.update(b, received_at=2.0)
        conflicts = self.det.check_conflicts(t_now=2.0)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(sorted(c["agents"]), ["op_a_ego", "op_b_ego"])
        self.assertLess(c["min_distance_m"], 5.0)
        self.assertAlmostEqual(c["conflict_position"]["x"], 0.0, delta=2.5)
        self.assertAlmostEqual(c["conflict_position"]["y"], 0.0, delta=2.5)

    def test_parallel_paths_do_not_flag(self):
        """Two westbound trajectories at y = 0 and y = -10 stay 10 m
        apart for their entire horizon — well above the 5 m threshold."""
        b = _straight_line_traj("op_b_ego", x0=20, y0=0,
                                  vx=-5, vy=0, t0=2.0)
        c = _straight_line_traj("op_c_ego", x0=20, y0=-10,
                                  vx=-5, vy=0, t0=2.0)
        self.det.update(b, received_at=2.0)
        self.det.update(c, received_at=2.0)
        conflicts = self.det.check_conflicts(t_now=2.0)
        self.assertEqual(conflicts, [])

    def test_disjoint_in_time_does_not_flag(self):
        """Two trajectories occupying the same xy line but offset in
        time so no waypoints land within ``time_tolerance_s`` of each
        other don't conflict."""
        a = _straight_line_traj("op_a_ego", x0=0, y0=0,
                                  vx=0, vy=5, t0=0.0)
        # b's waypoints start at t=20s — far past A's horizon of t=5
        b = _straight_line_traj("op_b_ego", x0=0, y0=0,
                                  vx=0, vy=5, t0=20.0)
        self.det.update(a, received_at=0.0)
        self.det.update(b, received_at=20.0)
        conflicts = self.det.check_conflicts(t_now=20.0)
        self.assertEqual(conflicts, [])

    def test_three_agents_flag_only_the_crossing_pair(self):
        """A and B cross; C runs parallel to B 10 m away. Only (A, B)
        should be flagged."""
        a = _straight_line_traj("op_a_ego", x0=0, y0=-20,
                                  vx=0, vy=5, t0=2.0)
        b = _straight_line_traj("op_b_ego", x0=20, y0=0,
                                  vx=-5, vy=0, t0=2.0)
        c = _straight_line_traj("op_c_ego", x0=20, y0=-10,
                                  vx=-5, vy=0, t0=2.0)
        for traj in (a, b, c):
            self.det.update(traj, received_at=2.0)
        conflicts = self.det.check_conflicts(t_now=2.0)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(sorted(conflicts[0]["agents"]),
                          ["op_a_ego", "op_b_ego"])

    def test_stale_plan_evicted(self):
        """A plan received longer than ``stale_threshold_s`` ago is
        dropped, so a stopped publisher doesn't keep firing."""
        Detector = _import_detector()
        det = Detector(conflict_distance_m=5.0,
                         time_tolerance_s=0.4,
                         stale_threshold_s=2.0)
        a = _straight_line_traj("op_a_ego", x0=0, y0=-20,
                                  vx=0, vy=5, t0=2.0)
        b = _straight_line_traj("op_b_ego", x0=20, y0=0,
                                  vx=-5, vy=0, t0=2.0)
        det.update(a, received_at=0.0)
        det.update(b, received_at=0.0)
        # 5s later, both plans are stale — no conflicts reported.
        self.assertEqual(det.check_conflicts(t_now=5.0), [])

    def test_event_payload_shape(self):
        """The event dict carries every field the dashboard renders."""
        a = _straight_line_traj("op_a_ego", x0=0, y0=-20,
                                  vx=0, vy=5, t0=2.0)
        b = _straight_line_traj("op_b_ego", x0=20, y0=0,
                                  vx=-5, vy=0, t0=2.0)
        self.det.update(a, received_at=2.0)
        self.det.update(b, received_at=2.0)
        c = self.det.check_conflicts(t_now=2.0)[0]
        self.assertEqual(c["event_type"], "trajectory_conflict")
        self.assertIn("min_distance_m", c)
        self.assertIn("conflict_time", c)
        self.assertIn("conflict_position", c)
        self.assertIn("time_to_conflict", c)
        self.assertGreater(c["time_to_conflict"], 0.0)


if __name__ == "__main__":
    unittest.main()
