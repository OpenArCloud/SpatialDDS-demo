#!/usr/bin/env python3
"""A protagonist's controller: it drives its car from the bus, and only the bus.

    python3 -m scenarios.crossing.controller --car car-a1 --domain 47 --port 8871

**What it reads.** Three things, every one of them a record some asserter put
on the bus; never the world's ground truth (the truth feed is its tick and
its command channel, and `Blind` hands the controller no frame):

* where its own car is: its operator's record of it, `ent:<operator>:<car>`,
  pose from the fast lane;
* claims, filtered by asserter and basis (`TRUSTED`): the roadside unit's
  pedestrian in the crosswalk, DERIVED or OBSERVED (C1), and the gate
  controller's declaration that gate-9 closes the crossing zone, DECLARED
  (C3). A claim stops the car whatever its `valid_from`: a held prediction or
  intent about the zone ahead is reason enough to be able to stop there;
* its own operator's perception, the pedestrians its own car sees
  (`demo.seen_by` naming it), near or in the crosswalk; and, as a floor,
  gate-9's own declared state, when it is anything but open.

**What it does.** For each such zone ahead it has a stop point, `MARGIN_M`
before the zone's near edge, and it holds the car to the speed from which a
constant `A_PLAN` brakes it to rest there: `v = sqrt(2 * A_PLAN * d)`. With
nothing ahead it hands the speed back to SUMO. A car already past the stop
point does what the zone asks (`PAST_THE_LINE`): in the crosswalk it halts
where it is, with a pedestrian in or near it; on the crossing it clears the
tracks. The zones' geometry is the map
author's records. The speed is a cap, not a target: under it SUMO's own
following drives, so a cap never speeds a car up; a cap under `STOP_MPS` is
a stop, held, not crept at. The world lowers a cap no faster than the car
can brake (`world.World`).

**What SUMO keeps.** On its first command the controller sets the car's speed
mode to `SPEED_MODE`: SUMO still keeps the gap to the vehicle ahead and the
car's acceleration and braking limits (4.5 m/s², SUMO's passenger default),
but no longer yields to pedestrians, gives way at the junctions, or stops
for the crossing's signal. That is this controller's job, from what it
reads, and it brakes no harder than the car can.
"""
from __future__ import annotations

import math
import sys
from typing import Dict, List, Optional, Tuple

import spatialdds as sd
from spatialdds.dds.types.spatial.owm import Basis, Entity, LifecycleState

from spatialdds_demo import claims
from spatialdds_demo.lanes import MODEL_FAST, MODEL_LATCHED, RELATIONSHIP
from scenarios.crossing import participant as runner
from scenarios.crossing.scene import (
    DEMO_CLOSES, SCENARIO, SF_WITHIN, TYPE, entity_id, service_id)

#: SUMO's speed mode: bits 0-2 kept (safe speed to the leader, the maximum
#: acceleration and deceleration); right of way (3) and red lights (4) off;
#: and bit 5 on, disregarding foes already within an intersection, which is
#: what a pedestrian on the crosswalk is. Without bit 5 SUMO still brakes for
#: the pedestrian from its own ground truth, 0.4 s before this controller had
#: seen it (measured; docs/crossing-claims.md).
SPEED_MODE = 0b100111
#: The planned stop's constant deceleration, m/s².
A_PLAN = 1.0
#: Where the car's front stops, short of a zone's near edge, m.
MARGIN_M = 1.5
#: A pedestrian the car sees this close to the crosswalk counts as in it.
KERB_M = 1.0
#: A speed cap is re-sent when it moves by more than this, m/s.
RESEND_MPS = 0.3
#: A cap below this is a stop.
STOP_MPS = 0.5
#: At or above this the car is not held back.
FREE_MPS = 16.7

UNIT = service_id(SCENARIO["sensing_unit"]["id"])
GATE = SCENARIO["gate"]
GATE_CTL = service_id(GATE["controller"])
GATE_ID = entity_id(GATE["controller"], GATE["entity"])
CROSSWALK = entity_id("map", "crosswalk")
CROSSING_ZONE = entity_id("map", "crossing-zone")

#: What a car already past a zone's stop point does while it is still short
#: of the zone's far edge.
PAST_THE_LINE = {CROSSWALK: "halt", CROSSING_ZONE: "clear"}

#: (asserter, predicate, object) -> the bases trusted, and what the claim is.
TRUSTED = {
    (UNIT, SF_WITHIN, CROSSWALK): ({Basis.DERIVED, Basis.OBSERVED},
                                   "pedestrian in the crosswalk"),
    (GATE_CTL, DEMO_CLOSES, CROSSING_ZONE): ({Basis.DECLARED},
                                            "gate-9 closes the crossing zone"),
}


def yaw_of(q) -> float:
    return 2.0 * math.atan2(q[2], q[3])


class Controller:
    """The decision, from bus records only. `take(events)` feeds it; `decide()`
    returns the command to send, if any."""

    def __init__(self, car: str, operator: str) -> None:
        self.car = car
        self.operator = operator
        self.own = entity_id(operator, car)
        self.entities: Dict[str, Entity] = {}
        self.pose: Optional[Tuple[list, list]] = None
        #: The stamp of the own-car record `pose` came from, in seconds.
        self.pose_stamp = 0.0
        #: The newest fast-lane position of each of its operator's records.
        self.positions: Dict[str, list] = {}
        self.claims: Dict[str, object] = {}
        self.commanded: Optional[float] = None      # None: the car's own maximum
        self.taken = False
        self.reason = ""

    # -- reading --------------------------------------------------------------

    def on_entity(self, ev) -> None:
        if isinstance(ev, sd.Sample):
            self.entities[ev.key] = ev.data
            if ev.key == self.own and ev.data.has_pose:
                self._own(ev.data)
        elif isinstance(ev, sd.Dispose):
            self.entities.pop(ev.key, None)
            self.positions.pop(ev.key, None)

    def on_pose(self, ev) -> None:
        if not isinstance(ev, sd.Sample):
            return
        if ev.key == self.own:
            self._own(ev.data)
        elif ev.key.startswith(f"ent:{self.operator}:"):
            self.positions[ev.key] = list(ev.data.pose.t)

    def _own(self, record) -> None:
        stamp = record.stamp.sec + record.stamp.nanosec / 1e9
        if stamp >= self.pose_stamp:            # never step back in time
            self.pose = (list(record.pose.t), list(record.pose.q))
            self.pose_stamp = stamp

    def on_relationship(self, ev) -> None:
        if isinstance(ev, sd.Sample):
            edge = ev.data
            trust = TRUSTED.get((edge.asserter, edge.predicate, edge.object_ref))
            if trust is None or edge.basis not in trust[0]:
                return
            if edge.state.name == "RETIRED":
                self.claims.pop(edge.id, None)
            else:
                self.claims[edge.id] = edge
        elif isinstance(ev, sd.Dispose):
            self.claims.pop(ev.key, None)

    # -- deciding ------------------------------------------------------------

    def _zone(self, zid: str) -> Optional[Tuple[float, float, float, float]]:
        z = self.entities.get(zid)
        if z is None or not z.has_extent:
            return None
        lo, hi = z.extent.min_xyz, z.extent.max_xyz
        return lo[0], lo[1], hi[0], hi[1]

    def hazards(self) -> List[Tuple[str, str]]:
        """(zone id, why) for every zone the car should be able to stop short of."""
        out = []
        for edge in self.claims.values():
            what = TRUSTED[(edge.asserter, edge.predicate, edge.object_ref)][1]
            out.append((edge.object_ref,
                        f"claim {edge.id} ({what}, {edge.basis.name})"))
        cw = self._zone(CROSSWALK)
        for key, e in self.entities.items():
            if not key.startswith(f"ent:{self.operator}:") or cw is None:
                continue
            if TYPE["pedestrian"] not in e.type_uris:
                continue
            props = {kv.key: kv.value for kv in e.properties}
            if self.car not in props.get("demo.seen_by", "").split(","):
                continue
            x, y = self.positions.get(key, e.pose.t)[:2]
            if (cw[0] - KERB_M <= x <= cw[2] + KERB_M
                    and cw[1] - KERB_M <= y <= cw[3] + KERB_M):
                out.append((CROSSWALK, f"own perception: {key}"))
        gate = self.entities.get(GATE_ID)
        if gate is not None:
            state = {kv.key: kv.value for kv in gate.properties}.get(
                "demo.gate_state", "open")
            if state != "open":
                out.append((CROSSING_ZONE, f"gate-9 declared {state}"))
        return out

    def target(self) -> Tuple[Optional[float], str, float]:
        """The speed to hold, or None for SUMO's own; why; how far to stop."""
        if self.pose is None:
            return None, "", 0.0
        (x, y, _z), q = self.pose
        heading = math.cos(yaw_of(q))
        east = heading >= 0
        length = 4.6
        e = self.entities.get(self.own)
        if e is not None:
            dims = {kv.key: kv.value for kv in e.properties}
            length = float(dims.get("demo.obb.length_m", length))
        front = x + (length / 2 if east else -length / 2)
        best, why, dist = None, "", 0.0
        for zid, reason in self.hazards():
            box = self._zone(zid)
            if box is None:
                continue
            x0, _y0, x1, _y1 = box
            d = (x0 - MARGIN_M - front) if east else (front - (x1 + MARGIN_M))
            beyond = (front > x1) if east else (front < x0)
            if beyond:
                continue                 # past the zone altogether
            if d < -MARGIN_M and PAST_THE_LINE.get(zid) != "halt":
                continue                 # over the line: clear it
            v = math.sqrt(2.0 * A_PLAN * max(0.0, d))
            if v < STOP_MPS:
                v = 0.0
            if v < FREE_MPS and (best is None or v < best):
                best, why, dist = v, reason, max(0.0, d)
        return best, why, dist

    def decide(self) -> Optional[dict]:
        if self.pose is None:
            return None
        v, reason, d = self.target()
        # The own-car record this decision placed the car by.
        cmd = {"cmd": "speed", "id": self.car,
               "pose_stamp": round(self.pose_stamp, 3)}
        why = f"{reason}: stop {d:.1f} m ahead" if reason else ""
        if not self.taken:
            self.taken = True
            self.commanded, self.reason = v, reason
            return dict(cmd, cap=None if v is None else round(v, 2),
                        speed_mode=SPEED_MODE,
                        why=why or "take control: yielding and signals are "
                                   "the controller's")
        if v is None:
            if self.commanded is None:
                return None
            self.commanded, self.reason = None, ""
            return dict(cmd, cap=None, why="nothing ahead to stop for")
        if (self.commanded is None or abs(v - self.commanded) > RESEND_MPS
                or (v == 0.0 and self.commanded != 0.0)
                or reason != self.reason):
            self.commanded, self.reason = v, reason
            return dict(cmd, cap=round(v, 2), why=why)
        return None


class Blind:
    """The controller as a participant: the truth frame is its tick, and is
    dropped here, unread. What it decides comes from its readers."""

    def __init__(self, car: str, p: "sd.dds.Participant") -> None:
        operator = SCENARIO["protagonists"][car]["operator"]
        self.ctl = Controller(car, operator)
        self.readers = [
            (p.reader("model_entity", qos_profile=MODEL_LATCHED),
             self.ctl.on_entity),
            (p.reader("model_pose", qos_profile=MODEL_FAST), self.ctl.on_pose),
            (p.reader(RELATIONSHIP, stream=claims.STREAM,
                      qos_profile=MODEL_LATCHED), self.ctl.on_relationship),
        ]
        self.feed = None                       # set by run(), for commands

    def on_truth(self, _frame, _stamp) -> None:
        self.tick()

    def tick(self) -> None:
        for reader, handle in self.readers:
            for ev in reader.take():
                handle(ev)
        cmd = self.ctl.decide()
        if cmd is not None:
            self.feed.send(cmd)
            cap = "own" if cmd["cap"] is None else f"{cmd['cap']:g}"
            print(f"{self.ctl.car}-controller: cap {cap} ({cmd['why']})",
                  flush=True)


def main() -> None:
    ap = runner.parser(__doc__)
    ap.add_argument("--car", required=True, choices=sorted(SCENARIO["protagonists"]))
    args = ap.parse_args()
    sys.exit(runner.run(f"{args.car}-controller",
                        lambda p: Blind(args.car, p), args))


if __name__ == "__main__":
    main()
