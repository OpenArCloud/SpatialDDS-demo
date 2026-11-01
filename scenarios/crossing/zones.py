"""Footprints against zones: the geometry every claim of the scene is decided by.

A footprint is an oriented box in the scene plane: centre, length along the
heading, width across it, ENU yaw. A zone is the map author's axis-aligned
box. Two GeoSPARQL Simple Features relations are decided here, on the plane,
as DE-9IM defines them (OGC 22-047r1, Table 2):

* `within`: every point of the footprint is in the zone (`geo:sfWithin`);
* `intersects`: they share at least one point (`geo:sfIntersects`).

A prediction asks when, at constant velocity, a footprint will first be
within a zone (`time_to_within`). Nothing here knows SUMO or the bus.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]


def corners(x: float, y: float, yaw_deg: float, length: float,
            width: float) -> List[Point]:
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    out = []
    for dl in (-length / 2, length / 2):
        for dw in (-width / 2, width / 2):
            out.append((x + c * dl - s * dw, y + s * dl + c * dw))
    return out


def bounds(zone: dict) -> Tuple[float, float, float, float]:
    (x0, y0, _), (x1, y1, _) = zone["min_xyz"], zone["max_xyz"]
    return x0, y0, x1, y1


def within(x: float, y: float, yaw_deg: float, length: float, width: float,
           zone: dict) -> bool:
    x0, y0, x1, y1 = bounds(zone)
    return all(x0 <= px <= x1 and y0 <= py <= y1
               for px, py in corners(x, y, yaw_deg, length, width))


def intersects(x: float, y: float, yaw_deg: float, length: float,
               width: float, zone: dict) -> bool:
    """Separating axes: the zone's two and the footprint's two."""
    x0, y0, x1, y1 = bounds(zone)
    pts = corners(x, y, yaw_deg, length, width)
    if (max(p[0] for p in pts) < x0 or min(p[0] for p in pts) > x1
            or max(p[1] for p in pts) < y0 or min(p[1] for p in pts) > y1):
        return False
    zone_pts = [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    for ax, ay in ((c, s), (-s, c)):
        a = [px * ax + py * ay for px, py in pts]
        b = [px * ax + py * ay for px, py in zone_pts]
        if max(a) < min(b) or max(b) < min(a):
            return False
    return True


def time_to_within(x: float, y: float, vx: float, vy: float, yaw_deg: float,
                   length: float, width: float, zone: dict, *,
                   horizon_s: float, dt: float = 0.05) -> Optional[float]:
    """Seconds until the footprint, moving at (vx, vy) with its heading held,
    is first within the zone; None if not within `horizon_s`. 0 if it is now."""
    n = int(round(horizon_s / dt))
    for i in range(n + 1):
        t = i * dt
        if within(x + vx * t, y + vy * t, yaw_deg, length, width, zone):
            return round(t, 3)
    return None


def time_to_intersect(x: float, y: float, vx: float, vy: float,
                      yaw_deg: float, length: float, width: float, zone: dict,
                      *, horizon_s: float, dt: float = 0.05) -> Optional[float]:
    """As `time_to_within`, for first contact."""
    n = int(round(horizon_s / dt))
    for i in range(n + 1):
        t = i * dt
        if intersects(x + vx * t, y + vy * t, yaw_deg, length, width, zone):
            return round(t, 3)
    return None


def gap_ahead(x: float, y: float, yaw_deg: float, zone: dict,
              reach_m: float) -> Optional[float]:
    """Distance from (x, y), straight along the heading, to the zone's edge;
    0 if inside; None if the heading misses it within `reach_m`."""
    x0, y0, x1, y1 = bounds(zone)
    if x0 <= x <= x1 and y0 <= y <= y1:
        return 0.0
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    lo, hi = 0.0, reach_m
    for p, d, a, b in ((x, c, x0, x1), (y, s, y0, y1)):
        if abs(d) < 1e-12:
            if not a <= p <= b:
                return None
            continue
        t0, t1 = (a - p) / d, (b - p) / d
        if t0 > t1:
            t0, t1 = t1, t0
        lo, hi = max(lo, t0), min(hi, t1)
        if lo > hi:
            return None
    return lo


def heading_of(points: Sequence[Tuple[float, float, float]]) -> Optional[Tuple[float, float]]:
    """(vx, vy) from the first and last of timed positions (t, x, y)."""
    if len(points) < 2:
        return None
    (t0, x0, y0), (t1, x1, y1) = points[0], points[-1]
    if t1 - t0 <= 0:
        return None
    return (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
