"""The crossing scene's world: SUMO behind a seam.

Everything the scene's participants know about the street comes through here,
as plain data, and nothing else in the scene imports SUMO
(`tests/test_crossing_world.py` here holds that, by reading every module's
imports).
The asserters and the recorder see the world only through the world service
(`world_service.py`), which runs this.

    w = World(load_scenario())
    w.start()
    truth = w.step()           # ground truth after one step, as a dict
    w.command({"cmd": "crossing", "id": "RX", "state": "closed", "at": 54.0})
    w.close()

**Ground truth is the simulator's, in the scene frame** (`map/crossing`:
metres, ENU, x east, y north). A vehicle's position is the centre of its
footprint; SUMO reports the front bumper, so it is moved back half a length
along the heading. Heading is ENU yaw, degrees counter-clockwise from east;
SUMO's is clockwise from north.

**Actuation is the scene's, not SUMO's.** Three things are driven over TraCI
rather than left to SUMO's own models:

* the protagonists' speed, on a consumer's command (`speed`), and their lane:
  they keep the lane they were inserted in, so their path is the scenario's;
* the level crossing (`crossing`), which the gate controller owns: the node is
  a SUMO `rail_crossing`, and from the first step its road links are held in
  the state the controller last commanded (open until then), so the arms move
  when the controller says and not when SUMO's own crossing logic would;
* the scenario's choreography: ped-17's wait at the kerb (`scenario.json`).

A command with `at` takes effect at that simulation time, so a schedule
declared ahead is kept exactly; one without takes effect at the next step.
"""
from __future__ import annotations

import heapq
import itertools
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import sumo                                   # the pinned eclipse-sumo wheel
import traci

HERE = Path(__file__).resolve().parent

#: Road-link state per crossing state, in SUMO's signal alphabet. "opening" is
#: SUMO's own red-yellow, which its rail crossings show while the arms rise.
SIGNAL = {"open": "G", "warning": "y", "closed": "r", "opening": "u"}
STATE_OF = {v: k for k, v in SIGNAL.items()}

#: An actor's kind, from its SUMO vehicle class.
KIND = {"passenger": "car", "bus": "bus", "rail": "train"}

#: Heights, which SUMO keeps for drawing; the scene's line of sight uses them.
HEIGHT_M = {"car": 1.5, "bus": 3.2, "train": 4.2, "pedestrian": 1.7}


def load_scenario(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or HERE / "scenario.json"
    return json.loads(Path(path).read_text())


def enu_yaw(sumo_angle_deg: float) -> float:
    """SUMO's heading (clockwise from north) as ENU yaw (counter-clockwise
    from east), in (-180, 180]."""
    yaw = 90.0 - sumo_angle_deg
    return (yaw + 180.0) % 360.0 - 180.0 if yaw != -180.0 else 180.0


class World:
    """One SUMO run of the scenario. See the module docstring."""

    def __init__(self, scenario: Dict[str, Any], *, seed: Optional[int] = None,
                 label: str = "crossing-world") -> None:
        self.scenario = scenario
        self.sim = scenario["sim"]
        self.seed = self.sim["seed"] if seed is None else seed
        self.step_s = float(self.sim["step_s"])
        self.label = label
        self.protagonists = set(scenario["protagonists"])
        self.gate = scenario["gate"]
        self._holds = {pid: dict(spec, state="waiting")
                       for pid, spec in scenario.get("choreography", {}).items()}
        self._queue: List[tuple] = []
        self._seq = itertools.count()
        self._crossing: Dict[str, str] = {}
        self._links: Dict[str, int] = {}
        self._kinds: Dict[str, str] = {}
        self._dims: Dict[str, tuple] = {}
        self.t = 0.0
        self.n = 0

    # -- life --------------------------------------------------------------

    def start(self) -> None:
        sumo_bin = os.path.join(sumo.SUMO_HOME, "bin", "sumo")
        traci.start([
            sumo_bin,
            "-n", str(HERE / self.sim["net"]),
            "-r", str(HERE / self.sim["routes"]),
            "--step-length", str(self.step_s),
            "--seed", str(self.seed),
            "--no-step-log", "true",
            "--duration-log.disable", "true",
            # A protagonist under a consumer's command may close on a
            # pedestrian; SUMO would otherwise remove it. The teeth measure
            # that distance, so the vehicle has to stay.
            "--collision.action", "warn",
            "--collision.check-junctions", "true",
        ], label=self.label)
        self._traci = traci.getConnection(self.label)
        node = self.gate["node"]
        self._links[node] = len(self._traci.trafficlight.getControlledLinks(node))
        self._set_crossing(node, "open")

    def close(self) -> None:
        try:
            self._traci.close()
        except Exception:                                    # already gone
            pass

    # -- commands ----------------------------------------------------------

    def command(self, cmd: Dict[str, Any]) -> None:
        """Queue a command; one with `at` waits for that simulation time."""
        at = float(cmd.get("at", self.t + self.step_s))
        heapq.heappush(self._queue, (at, next(self._seq), dict(cmd)))

    def _apply(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        kind = cmd.get("cmd")
        if kind == "crossing":
            self._set_crossing(cmd["id"], cmd["state"])
        elif kind == "speed":
            vid = cmd["id"]
            if vid not in self.protagonists:
                raise ValueError(f"{vid} is not a protagonist; only the "
                                 "protagonists are speed-controlled")
            if vid in self._traci.vehicle.getIDList():
                if "speed_mode" in cmd:
                    self._traci.vehicle.setSpeedMode(vid, int(cmd["speed_mode"]))
                self._traci.vehicle.setSpeed(vid, float(cmd["v"]))
        else:
            raise ValueError(f"unknown command {kind!r}")
        return dict(cmd, applied_at=round(self.t + self.step_s, 6))

    def _set_crossing(self, node: str, state: str) -> None:
        self._traci.trafficlight.setRedYellowGreenState(
            node, SIGNAL[state] * self._links[node])
        self._crossing[node] = state

    # -- stepping ----------------------------------------------------------

    def step(self) -> Dict[str, Any]:
        """Advance one step; ground truth after it, with what was applied."""
        applied = []
        horizon = self.t + self.step_s + 1e-9
        while self._queue and self._queue[0][0] <= horizon:
            _at, _seq, cmd = heapq.heappop(self._queue)
            applied.append(self._apply(cmd))
        self._choreograph()
        self._traci.simulationStep()
        self.n += 1
        self.t = round(self.n * self.step_s, 6)
        departed = list(self._traci.simulation.getDepartedIDList())
        for vid in departed:
            if vid in self.protagonists:
                # Keep the scenario's lane: no lane changes, of any kind.
                self._traci.vehicle.setLaneChangeMode(vid, 0)
        return {
            "t": self.t,
            "step": self.n,
            "actors": self._actors(),
            "crossings": dict(self._crossing),
            "departed": departed,
            "arrived": list(self._traci.simulation.getArrivedIDList()),
            "applied": applied,
            "holds": {pid: h["state"] for pid, h in self._holds.items()},
        }

    def done(self) -> bool:
        return self.t >= float(self.sim["duration_s"]) - 1e-9

    def _choreograph(self) -> None:
        persons = set(self._traci.person.getIDList())
        upcoming = self.t + self.step_s
        for pid, h in self._holds.items():
            if pid not in persons:
                continue
            if h["state"] == "waiting":
                if (self._traci.person.getLaneID(pid) == h["hold_on_lane"]
                        and self._traci.person.getPosition(pid)[1]
                        >= h["hold_once_y_at_least"]):
                    self._traci.person.setSpeed(pid, 0.0)
                    h["state"] = "held"
            if h["state"] == "held" and upcoming >= h["release_at_s"] - 1e-9:
                self._traci.person.setSpeed(pid, float(h["release_speed_mps"]))
                h["state"] = "released"

    def _actors(self) -> Dict[str, Dict[str, Any]]:
        v, p = self._traci.vehicle, self._traci.person
        out: Dict[str, Dict[str, Any]] = {}
        for vid in v.getIDList():
            if vid not in self._kinds:
                self._kinds[vid] = KIND.get(v.getVehicleClass(vid), "car")
                self._dims[vid] = (v.getLength(vid), v.getWidth(vid))
            kind = self._kinds[vid]
            length, width = self._dims[vid]
            fx, fy = v.getPosition(vid)
            angle = v.getAngle(vid)
            rad = math.radians(angle)
            # Front bumper -> footprint centre, half a length back.
            cx = fx - math.sin(rad) * length / 2.0
            cy = fy - math.cos(rad) * length / 2.0
            out[vid] = {
                "kind": kind, "x": round(cx, 3), "y": round(cy, 3), "z": 0.0,
                "yaw_deg": round(enu_yaw(angle), 3),
                "speed": round(v.getSpeed(vid), 3),
                "length": length, "width": width, "height": HEIGHT_M[kind],
                "edge": v.getRoadID(vid), "lane": v.getLaneIndex(vid),
                "stopped": bool(v.isStopped(vid)),
            }
        for pid in p.getIDList():
            x, y = p.getPosition(pid)
            out[pid] = {
                "kind": "pedestrian", "x": round(x, 3), "y": round(y, 3),
                "z": 0.0, "yaw_deg": round(enu_yaw(p.getAngle(pid)), 3),
                "speed": round(p.getSpeed(pid), 3),
                "length": p.getLength(pid), "width": p.getWidth(pid),
                "height": HEIGHT_M["pedestrian"],
                "edge": p.getRoadID(pid), "lane": None,
                "stopped": self._holds.get(pid, {}).get("state") == "held",
            }
        return out
