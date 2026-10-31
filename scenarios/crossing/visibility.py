"""What a sensor can see: range, then line of sight past buildings and vehicles.

The scene's sensing is emulated from ground truth, and this is the whole of
the emulation: an actor is seen when it is within range and the straight line
from the sensor to the actor's centre is not blocked. No radio, no noise, no
misses; the scene says so wherever it publishes a track.

Line of sight is 2.5-D. Each occluder is a footprint (a box, rotated by its
yaw) with a height; the sight line runs from the sensor's height to the
target's centre height, and it is blocked where it passes through a footprint
lower than the occluder's top. That is what lets a roadside unit on a pole
see over a car, and keeps a car's sensors from seeing through a bus.

Occluders are the scene's buildings (scenario.json) and the tall vehicles,
buses and trains, wherever they are this step. Cars do not occlude: the
scene's beats turn on the bus and on the buildings, and leaving cars out
keeps what a sensor misses attributable to those.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: Actor kinds that block a sight line.
OCCLUDING_KINDS = ("bus", "train")


@dataclass(frozen=True)
class Box:
    """A footprint: centre, half-sizes along its own axes, yaw, height."""
    name: str
    cx: float
    cy: float
    hx: float
    hy: float
    yaw_deg: float
    top: float

    def local(self, x: float, y: float) -> Tuple[float, float]:
        c, s = math.cos(math.radians(self.yaw_deg)), math.sin(math.radians(self.yaw_deg))
        dx, dy = x - self.cx, y - self.cy
        return c * dx + s * dy, -s * dx + c * dy


def building_boxes(buildings: Iterable[dict]) -> List[Box]:
    out = []
    for b in buildings:
        (x0, y0, _z0), (x1, y1, z1) = b["min_xyz"], b["max_xyz"]
        out.append(Box(b["id"], (x0 + x1) / 2, (y0 + y1) / 2,
                       (x1 - x0) / 2, (y1 - y0) / 2, 0.0, z1))
    return out


def actor_box(name: str, a: dict) -> Box:
    return Box(name, a["x"], a["y"], a["length"] / 2, a["width"] / 2,
               a["yaw_deg"], a["z"] + a["height"])


def _segment_in_box(box: Box, p0: Tuple[float, float],
                    p1: Tuple[float, float]) -> Optional[Tuple[float, float]]:
    """The parameter interval [s0, s1] of p0->p1 inside the footprint, or None
    (the slab method, in the box's own axes)."""
    (ax, ay), (bx, by) = box.local(*p0), box.local(*p1)
    s0, s1 = 0.0, 1.0
    for a, b, h in ((ax, bx, box.hx), (ay, by, box.hy)):
        d = b - a
        if abs(d) < 1e-12:
            if abs(a) > h:
                return None
            continue
        t0, t1 = (-h - a) / d, (h - a) / d
        if t0 > t1:
            t0, t1 = t1, t0
        s0, s1 = max(s0, t0), min(s1, t1)
        if s0 > s1:
            return None
    return s0, s1


def blocker(sensor: Sequence[float], target: Sequence[float],
            occluders: Iterable[Box]) -> Optional[str]:
    """The name of the first occluder that blocks sensor -> target, or None.

    Points are (x, y, z). The sight line is straight in 3-D; it is blocked by
    a footprint it crosses while below that footprint's top.
    """
    (sx, sy, sz), (tx, ty, tz) = sensor, target
    for box in occluders:
        span = _segment_in_box(box, (sx, sy), (tx, ty))
        if span is None:
            continue
        s0, s1 = span
        # Lowest point of the sight line over the footprint: it is linear, so
        # one of the ends of the span.
        low = min(sz + (tz - sz) * s0, sz + (tz - sz) * s1)
        if low < box.top:
            return box.name
    return None


class Sight:
    """One sensor's view of one truth frame."""

    def __init__(self, buildings: Iterable[dict]) -> None:
        self.static = building_boxes(buildings)

    def visible(self, sensor: Sequence[float], range_m: float,
                actors: Dict[str, dict], *, exclude: Iterable[str] = ()
                ) -> Dict[str, Optional[str]]:
        """Every actor in range, mapped to None if seen, or to what blocks it.
        Actors out of range are absent. `exclude` names actors that are the
        sensor's own platform, which neither occlude nor are seen."""
        skip = set(exclude)
        dynamic = [actor_box(n, a) for n, a in actors.items()
                   if a["kind"] in OCCLUDING_KINDS and n not in skip]
        out: Dict[str, Optional[str]] = {}
        sx, sy, _ = sensor
        for name, a in actors.items():
            if name in skip:
                continue
            if math.hypot(a["x"] - sx, a["y"] - sy) > range_m:
                continue
            target = (a["x"], a["y"], a["z"] + a["height"] / 2.0)
            out[name] = blocker(sensor, target,
                                self.static + [b for b in dynamic if b.name != name])
        return out
