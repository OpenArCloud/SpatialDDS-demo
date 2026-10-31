#!/usr/bin/env python3
"""gate-ctl-9: the level crossing's controller, and its gate.

    python3 -m scenarios.crossing.gate_ctl --domain 47 --port 8871

The controller owns the crossing. Its own track circuit, `track_circuit_y`
south of the crossing, detects a northbound train; it then fixes a schedule
and commands it to the world ahead of time (`world.World.command` with `at`):

    detected            t_d
    warning lights      t_d + lead_s - warning_s
    arms down (closed)  t_d + lead_s
    arms rising         train clear of the crossing + clear_after_s
    open                that + opening_s

so the arms move when the controller said they would, to the step. Its gate,
`ent:gate-ctl-9:gate-9`, is DECLARED: the actuator stating its own state,
`demo.gate_state`, as the world reports it applied. Batch 2 adds the
controller's claim, "gate-9 closes crossing-zone", declared at t_d with
valid_from the arms-down time; this module already holds the schedule that
claim will state.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import spatialdds as sd
from spatialdds.dds.types.spatial.core import Aabb3
from spatialdds.dds.types.spatial.owm import Basis, ModelLayer

from scenarios.crossing import participant as runner
from scenarios.crossing.scene import SCENARIO, Publisher, entity, entity_id

GATE = SCENARIO["gate"]
NAME = GATE["controller"]
ZONE = next(z for z in SCENARIO["zones"] if z["id"] == "crossing-zone")


class GateSchedule:
    """The controller's decisions, without the bus: detection, then the
    commanded schedule. `step(frame)` returns the commands to send."""

    def __init__(self) -> None:
        self.node = GATE["node"]
        self.train: Optional[str] = None
        self.schedule: Dict[str, float] = {}

    def _cmd(self, state: str, at: float) -> dict:
        self.schedule[state] = round(at, 3)
        return {"cmd": "crossing", "id": self.node, "state": state,
                "at": round(at, 3)}

    def step(self, frame) -> List[dict]:
        t, actors = frame["t"], frame["actors"]
        if self.train is None:
            for aid, a in actors.items():
                if (a["kind"] == "train" and a["edge"].startswith("rail_s")
                        and a["y"] + a["length"] / 2 >= GATE["track_circuit_y"]):
                    self.train = aid
                    self.schedule = {"detected": t}
                    close = t + GATE["lead_s"]
                    return [self._cmd("warning", close - GATE["warning_s"]),
                            self._cmd("closed", close)]
        elif "opening" not in self.schedule:
            a = actors.get(self.train)
            if a is None or a["y"] - a["length"] / 2 > ZONE["max_xyz"][1]:
                rise = t + GATE["clear_after_s"]
                return [self._cmd("opening", rise),
                        self._cmd("open", rise + GATE["opening_s"])]
        return []


class GateController:
    def __init__(self, p: "sd.dds.Participant") -> None:
        self.pub = Publisher(p)
        self.node = GATE["node"]
        self.feed = None                       # set by run(), for commands
        self.state = "open"
        self.plan = GateSchedule()

    def gate_record(self, stamp):
        (x0, y0, _), (x1, y1, _) = ZONE["min_xyz"], ZONE["max_xyz"]
        return entity(
            entity_id(NAME, GATE["entity"]), Basis.DECLARED, "gate",
            source=self.pub.source, stamp=stamp, layer=ModelLayer.SLOW,
            pose=((x0 + x1) / 2, (y0 + y1) / 2, 0.0, 0.0),
            extent=Aabb3(min_xyz=[x0, y0, 0.0], max_xyz=[x1, y1, 1.2]),
            properties=[("demo.label", GATE["entity"]),
                        ("demo.gate_state", self.state)])

    def on_start(self, stamp) -> None:
        self.pub.assert_entity(self.gate_record(stamp), now=0.0, moving=False)

    def on_truth(self, frame, stamp) -> None:
        t = frame["t"]
        for cmd in self.plan.step(frame):
            self.feed.send(cmd)
            print(f"{NAME}: t={t:.1f} {self.plan.train}: {cmd['state']} at "
                  f"t={cmd['at']:.1f}", flush=True)
        applied = frame["crossings"].get(self.node, self.state)
        if applied != self.state:
            self.state = applied
            self.pub.assert_entity(self.gate_record(stamp), now=t, moving=False)


def make(p):
    return GateController(p)


if __name__ == "__main__":
    runner.main_for(NAME, make, __doc__)
