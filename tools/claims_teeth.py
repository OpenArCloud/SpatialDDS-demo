#!/usr/bin/env python3
"""
The claims tooth's three arms, judged from a recorded run.

    python3 tools/claims_teeth.py OUT_DIR

Reads only what `tools/claims_tooth.sh` recorded (`probes.jsonl`,
`lane.jsonl`, `events.jsonl`, `tier.log`), so a run can be re-judged without
re-running it, and prints each arm's evidence before its verdict. Exit 0 when
all three hold, 1 when any fails. `docs/ros-claims.md` has the plant that
breaks each one.

1. **The claim bends the route.** The route planned with no hazard on the
   bus passes through the spill; the route planned while the trusted camera
   holds its claim does not, and the claims mask carried the region.
2. **A silenced asserter stops bending it, by ageing out.** After SIGKILL the
   lane shows the claim's writers gone and never a dispose or a RETIRED
   sample; the mask is drawn clear; the re-plan is the original route.
3. **An untrusted asserter never bent it.** The stranger's claim reached the
   lane and the robot node said why it did not draw it; the mask stayed
   clear; the route is the original one.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

#: tools/hazard_asserter.py's spill, x 23..25, y -23.8..-22.2.
SPILL = (23.0, -23.8, 25.0, -22.2)
TRUSTED = "svc:hazard:plaza-cam"
STRANGER = "svc:hazard:stranger"
#: Two plans of one costmap from one planner are the same route to well
#: under a cell (0.1 m); a detour round a 1.6 m spill is metres.
SAME_ROUTE_M = 0.05
DETOUR_M = 0.5
#: nav2's LETHAL_OBSTACLE. A claim is advice, so it must stay under it.
LETHAL = 254


def cost(probe):
    """The global costmap's cost at the spill when the probe planned."""
    return (probe.get("costmap") or {}).get("spill_centre")


def rows(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def inside(p, box=SPILL) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def to_polyline(p, line) -> float:
    best = math.inf
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        t = 0.0 if span == 0 else max(0.0, min(1.0, (
            (p[0] - ax) * dx + (p[1] - ay) * dy) / span))
        best = min(best, math.hypot(p[0] - (ax + t * dx),
                                    p[1] - (ay + t * dy)))
    return best


def deviation(a, b) -> float:
    """The furthest either route strays from the other."""
    if len(a) < 2 or len(b) < 2:
        return math.inf
    return max(max(to_polyline(p, b) for p in a),
               max(to_polyline(p, a) for p in b))


def describe(probe) -> str:
    path = probe.get("path") or []
    if not path:
        return f"{probe['label']}: no path ({probe.get('error')})"
    length = sum(math.dist(path[i], path[i + 1])
                 for i in range(len(path) - 1))
    north = max(p[1] for p in path)
    south = min(p[1] for p in path)
    mask = probe.get("mask")
    marked = "no mask" if mask is None else f"mask {mask['marked']} marked"
    return (f"{probe['label']}: {len(path)} poses, {length:.2f} m, y from "
            f"{south:.2f} to {north:.2f}, {sum(map(inside, path))} poses in "
            f"the spill; {marked}")


def waypoints(probe, every: float = 2.0) -> str:
    """The route at roughly `every` metres, for quoting."""
    path = probe.get("path") or []
    if not path:
        return "(none)"
    out, run = [path[0]], 0.0
    for a, b in zip(path, path[1:]):
        run += math.dist(a, b)
        if run >= every:
            out.append(b)
            run = 0.0
    if out[-1] != path[-1]:
        out.append(path[-1])
    return " ".join(f"({x:.1f},{y:.1f})" for x, y in out)


def main() -> int:
    out = Path(sys.argv[1])
    probes = {p["label"]: p for p in rows(out / "probes.jsonl")}
    lane = rows(out / "lane.jsonl")
    events = {e["event"]: e["t"] for e in rows(out / "events.jsonl")}
    log = (out / "tier.log").read_text() if (out / "tier.log").exists() \
        else ""
    verdicts = []

    def arm(name, checks):
        print(f"\n== {name}")
        ok = True
        for passed, line in checks:
            print(f"  [{'ok' if passed else 'FAIL'}] {line}")
            ok = ok and passed
        verdicts.append((name, ok))

    missing = [k for k in ("before", "claimed", "after-kill", "untrusted")
               if k not in probes or not probes[k].get("path")]
    if missing:
        print(f"claims_teeth: no plan recorded for {', '.join(missing)}")
        return 1
    before = probes["before"]["path"]
    for label in ("before", "claimed", "after-kill", "untrusted"):
        print(describe(probes[label]))
        print(f"    {waypoints(probes[label])}")

    claimed = probes["claimed"]
    d1 = deviation(before, claimed["path"])
    arm("arm 1: the trusted claim bends the route", [
        (sum(map(inside, before)) > 0,
         f"the route with no hazard passes through the spill "
         f"({sum(map(inside, before))} poses inside it)"),
        (bool(claimed["mask"]) and claimed["mask"]["marked"] > 0
         and 100 not in claimed["mask"]["values"],
         f"the claims mask carried the region, below lethal: "
         f"{claimed['mask']}"),
        (cost(claimed) is not None and 0 < cost(claimed) < LETHAL,
         f"and the global costmap carried it as a cost, not a wall: "
         f"{cost(claimed)} at the spill (lethal is {LETHAL})"),
        (sum(map(inside, claimed["path"])) == 0,
         f"the route while it is claimed avoids the spill "
         f"({sum(map(inside, claimed['path']))} poses inside it)"),
        (d1 >= DETOUR_M, f"and differs from the original by {d1:.2f} m "
                         f"(at least {DETOUR_M})"),
    ])

    trusted = [r for r in lane if (r.get("asserter") == TRUSTED)
               or (r.get("id") or "").startswith(f"rel:{TRUSTED}")]
    kill = next((t for e, t in events.items() if e.startswith("kill-")),
                None)
    print("\n  the trusted claim's lifecycle, as the lane delivered it:")
    for r in trusted:
        when = r["t"] - kill if kill else r["t"]
        print(f"    {when:+7.2f}s  {r['instance']:<22} "
              f"{r.get('state', '(no payload)'):<9} {r.get('reason', '')}")
    gone = [r for r in trusted if r["instance"] == "NOT_ALIVE_NO_WRITERS"]
    after = probes["after-kill"]
    d2 = deviation(before, after["path"])
    arm("arm 2: the silenced asserter's claim ages out", [
        (any(r.get("state") == "CREATED" for r in trusted),
         "the claim was on the lane, CREATED"),
        (bool(gone) and (kill is None or gone[0]["t"] > kill),
         "after the kill its writers were gone (NOT_ALIVE_NO_WRITERS"
         + (f", {gone[0]['t'] - kill:.1f}s after it)" if gone and kill
            else ")")),
        (not any(r["instance"] == "NOT_ALIVE_DISPOSED" for r in trusted),
         "and nothing disposed it"),
        (not any(r.get("state") == "RETIRED" for r in trusted),
         "and nothing retired it: age-out, not retirement"),
        ("an OBSERVED claim nobody is still making leaves the costmap"
         in log, "the robot node logged the age-out on liveliness"),
        (bool(after["mask"]) and after["mask"]["marked"] == 0,
         f"the mask was drawn clear: {after['mask']}"),
        (cost(after) == 0, f"and the costmap with it: {cost(after)} at the "
                           f"spill"),
        (d2 <= SAME_ROUTE_M,
         f"the re-plan is the original route (within {d2:.3f} m)"),
    ])

    stranger = [r for r in lane if r.get("asserter") == STRANGER
                and r.get("state") == "CREATED"]
    skipped = [line for line in log.splitlines()
               if f"not drawing rel:{STRANGER}" in line]
    untrusted = probes["untrusted"]
    d3 = deviation(before, untrusted["path"])
    if skipped:
        print("\n  " + skipped[0].split("]: ", 1)[-1])
    arm("arm 3: the untrusted asserter's claim changes nothing", [
        (bool(stranger), "the stranger's claim reached the lane, CREATED, "
                         "OBSERVED, the same proposition"),
        (bool(skipped) and "no trust rule admits it" in skipped[0],
         "the robot node saw it and said why it is not drawn"),
        (bool(untrusted["mask"]) and untrusted["mask"]["marked"] == 0,
         f"the mask stayed clear: {untrusted['mask']}"),
        (cost(untrusted) == 0, f"and the costmap with it: {cost(untrusted)} "
                               f"at the spill"),
        (d3 <= SAME_ROUTE_M,
         f"the route is the original one (within {d3:.3f} m)"),
    ])

    print()
    for name, ok in verdicts:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _, ok in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
