#!/usr/bin/env python3
"""The crossing scene's world: the seam, the street, determinism, and sight.

* **The seam.** Only `world.py` and `build_net.py` import SUMO, of every
  Python file in the repository: the participants and the recorder see the
  street through the world service, never the simulator. Read from each
  file's imports, so a new module is covered without being listed.
* **The street.** The committed network matches its hand-authored sources,
  and the zones the scenario authors are where the network has them: the
  crosswalk covers the crossing SUMO built, the crossing zone is the level
  crossing's junction, and the bus stops inside the stop zone.
* **Determinism.** The same seed gives the same events to the step, run twice
  in-process with the gate controller's schedule; another seed gives other
  background traffic, so the seed is doing something.
* **Sight.** The visibility model blocks what it should and nothing else.

Needs the pinned SUMO (`scenarios/crossing/requirements.txt`); without it the
SUMO-side tests skip and say what to install, and arm themselves when it is.
"""
from __future__ import annotations

import ast
import gzip
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scenarios.crossing import visibility                     # noqa: E402

SCENE_DIR = REPO / "scenarios" / "crossing"
SCENARIO = json.loads((SCENE_DIR / "scenario.json").read_text())
ZONES = {z["id"]: z for z in SCENARIO["zones"]}
SUMO_MODULES = {"sumo", "traci", "sumolib", "libsumo"}
SEAM = {SCENE_DIR / "world.py", SCENE_DIR / "build_net.py"}


def _sumo():
    # Probed, not imported: this file is held to the seam like any other.
    import importlib.util
    if not all(importlib.util.find_spec(m) for m in ("sumo", "traci")):
        raise unittest.SkipTest(
            "the pinned SUMO is not installed: pip install -r "
            "scenarios/crossing/requirements.txt")


def imports_of(path: Path) -> set:
    tree = ast.parse(path.read_text(), filename=str(path))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


class TheSeam(unittest.TestCase):

    def test_only_the_world_and_the_net_builder_import_sumo(self):
        tracked = [REPO / p for p in __import__("subprocess").run(
            ["git", "-C", str(REPO), "ls-files", "*.py"],
            capture_output=True, text=True, check=True).stdout.split()]
        files = set(tracked) | set(SCENE_DIR.glob("*.py"))
        users = {f for f in files if f.exists() and imports_of(f) & SUMO_MODULES}
        self.assertTrue(SCENE_DIR / "world.py" in users,
                        "the scan did not see world.py import SUMO")
        self.assertEqual(users - SEAM, set(),
                         "SUMO imported outside the seam")

    def test_the_scan_catches_an_import(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py") as f:
            f.write("from traci import vehicle\n")
            f.flush()
            self.assertIn("traci", imports_of(Path(f.name)))


class TheStreet(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.net = ET.parse(SCENE_DIR / "net" / "crossing.net.xml").getroot()

    def lane_shape(self, lane_id):
        lane = self.net.find(f".//lane[@id='{lane_id}']")
        pts = [tuple(map(float, p.split(","))) for p in lane.get("shape").split()]
        return pts, float(lane.get("width", "3.2"))

    def test_the_network_matches_its_sources(self):
        _sumo()
        from scenarios.crossing import build_net
        self.assertFalse(build_net.stale(),
                         "crossing.net.xml is stale: python3 -m "
                         "scenarios.crossing.build_net")

    def test_the_crosswalk_zone_covers_the_crossing(self):
        (x0, y0), (x1, y1) = self.lane_shape(":CW_c0_0")[0]
        width = self.lane_shape(":CW_c0_0")[1]
        z = ZONES["crosswalk"]
        self.assertAlmostEqual(z["min_xyz"][0], x0 - width / 2, places=1)
        self.assertAlmostEqual(z["max_xyz"][0], x0 + width / 2, places=1)
        self.assertAlmostEqual(z["min_xyz"][1], min(y0, y1), places=1)
        self.assertAlmostEqual(z["max_xyz"][1], max(y0, y1), places=1)

    def test_the_crossing_zone_is_the_junction(self):
        ends = [self.lane_shape(f"{e}_1")[0][-1][0] for e in ("art_cw_rx",)]
        starts = [self.lane_shape(f"{e}_1")[0][0][0] for e in ("art_rx_e",)]
        z = ZONES["crossing-zone"]
        self.assertAlmostEqual(z["min_xyz"][0], ends[0], places=1)
        self.assertAlmostEqual(z["max_xyz"][0], starts[0], places=1)
        rail_x = self.lane_shape("rail_s_x_0")[0][0][0]
        self.assertTrue(z["min_xyz"][0] < rail_x < z["max_xyz"][0])

    def test_the_bus_stops_inside_the_stop_zone(self):
        routes = ET.parse(SCENE_DIR / "net" / "crossing.rou.xml").getroot()
        stop = routes.find(".//vehicle[@id='bus-3']/stop")
        lane, end = stop.get("lane"), float(stop.get("endPos"))
        pts, _ = self.lane_shape(lane)
        nose_x = pts[0][0] + end                 # an eastbound lane along +x
        length = float(routes.find(".//vType[@id='bus']").get("length"))
        z = ZONES["stop-zone"]
        self.assertLessEqual(z["min_xyz"][0], nose_x - length)
        self.assertGreaterEqual(z["max_xyz"][0], nose_x)
        self.assertTrue(z["min_xyz"][1] <= pts[0][1] <= z["max_xyz"][1])


def run_in_process(seed: int, out: Path) -> Path:
    """The world, unpaced, with the gate controller's schedule: no DDS."""
    from scenarios.crossing.gate_ctl import GateSchedule
    from scenarios.crossing.world import World
    world, plan = World(SCENARIO, seed=seed, label=f"det-{seed}-{out.name}"), GateSchedule()
    world.start()
    try:
        with gzip.open(out, "wt") as f:
            while not world.done():
                frame = {"type": "truth", **world.step()}
                f.write(json.dumps(frame) + "\n")
                for cmd in plan.step(frame):
                    world.command(cmd)
    finally:
        world.close()
    return out


class Determinism(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _sumo()
        from scenarios.crossing import events
        cls.events = events
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        seed = SCENARIO["sim"]["seed"]
        cls.a = events.extract(run_in_process(seed, tmp / "a.gz"))
        cls.b = events.extract(run_in_process(seed, tmp / "b.gz"))
        cls.other = run_in_process(seed + 1, tmp / "c.gz")
        cls.first = tmp / "a.gz"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_same_seed_gives_the_same_events(self):
        self.assertEqual(self.a, self.b)

    def test_both_beats_happen(self):
        for name in ("bus-3 stopped enter", "ped-17 held at the kerb",
                     "ped-17 crosswalk enter", "gate warning", "gate closed",
                     "train-7 crossing-zone enter", "train-7 crossing-zone leave",
                     "gate open"):
            self.assertIn(name, self.a)
        self.assertLess(self.a["ped-17 crosswalk enter"], self.a["gate warning"],
                        "beat 1 should come before beat 2")
        self.assertLess(self.a["gate closed"], self.a["train-7 crossing-zone enter"])
        self.assertLess(self.a["train-7 crossing-zone leave"], self.a["gate opening"])
        self.assertTrue(any(k.startswith("queue at the gate") for k in self.a))

    def test_the_seed_moves_the_background(self):
        def background(path):
            """When each background vehicle departed: the seed draws these."""
            with gzip.open(path, "rt") as f:
                return [(d, fr["t"]) for fr in map(json.loads, f)
                        for d in fr["departed"] if d.startswith("bg-")]
        self.assertNotEqual(background(self.first), background(self.other))


class Sight(unittest.TestCase):

    BUS = {"kind": "bus", "x": -16.0, "y": -4.8, "z": 0.0, "yaw_deg": 0.0,
           "length": 12.0, "width": 2.55, "height": 3.2}
    PED = {"kind": "pedestrian", "x": 0.88, "y": -6.8, "z": 0.0, "yaw_deg": 90.0,
           "length": 0.4, "width": 0.6, "height": 1.7}

    def car(self, x):
        return {"kind": "car", "x": x, "y": -1.6, "z": 0.0, "yaw_deg": 0.0,
                "length": 4.6, "width": 1.85, "height": 1.5}

    def test_the_bus_hides_the_pedestrian_from_the_adjacent_lane(self):
        s = visibility.Sight(SCENARIO["buildings"])
        actors = {"bus-3": self.BUS, "ped-17": self.PED, "car-a1": self.car(-50)}
        seen = s.visible((-50.0, -1.6, 1.2), 60.0, actors, exclude=["car-a1"])
        self.assertEqual(seen["ped-17"], "bus-3")
        self.assertIsNone(seen["bus-3"])

    def test_past_the_bus_the_pedestrian_is_seen(self):
        s = visibility.Sight(SCENARIO["buildings"])
        actors = {"bus-3": self.BUS, "ped-17": self.PED}
        self.assertIsNone(s.visible((-8.0, -1.6, 1.2), 60.0, actors)["ped-17"])

    def test_the_roadside_unit_sees_the_kerb(self):
        unit = SCENARIO["sensing_unit"]
        s = visibility.Sight(SCENARIO["buildings"])
        seen = s.visible(tuple(unit["position"]), unit["range_m"],
                         {"bus-3": self.BUS, "ped-17": self.PED})
        self.assertIsNone(seen["ped-17"])

    def test_a_building_blocks_and_height_clears_it(self):
        b = [{"id": "wall", "min_xyz": [-1.0, -10.0, 0.0], "max_xyz": [1.0, 10.0, 5.0]}]
        s = visibility.Sight(b)
        target = {"t": {"kind": "car", "x": 10.0, "y": 0.0, "z": 0.0, "yaw_deg": 0.0,
                        "length": 4.6, "width": 1.85, "height": 1.5}}
        self.assertEqual(s.visible((-10.0, 0.0, 1.2), 50.0, target)["t"], "wall")
        self.assertIsNone(s.visible((-10.0, 0.0, 60.0), 50.0, target)["t"])

    def test_out_of_range_is_absent(self):
        s = visibility.Sight([])
        self.assertEqual(s.visible((0.0, 0.0, 1.0), 5.0, {"p": self.PED}), {})


if __name__ == "__main__":
    unittest.main()
