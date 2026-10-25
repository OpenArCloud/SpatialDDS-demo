#!/usr/bin/env python3
"""Measure the duck-reaction behaviour out of a recording, with its control.

    python3 tools/measure_duck_reaction.py recordings/robot-littlefield.mcap

The robot batch's sharpest claim is that a behaviour the staged migration
severed has come back: `duck_mover` avoids `ent:robot:tb3` within
`AVOID_RADIUS_M`, and until the robot lane migrated it was publishing poses on
a topic nothing read. "It looked right" is not an acceptable proof of that, so
this derives a number from the committed artifact.

**The control is the point, not the headline figure.** Ducks wander, so a bare
"the ducks were further from the robot" could be a random walk plus a
convenient robot trajectory. Every duck observation is therefore classified by
whether the robot was *inside* the avoidance radius at the time, and the two
arms are reported side by side. Both arms come out of the same recording, so
the control is not a second run that could have been tuned to agree.

**What to measure took two attempts, and the first one is kept here as a
reported null rather than deleted.** The obvious measure -- per duck step, did
the distance to the robot increase -- returns nothing: 51% inside against 52%
outside on the committed capture. That is not the behaviour failing, it is the
measure not matching it. `step_for` does not nudge a duck a little further
away each step; it steers for "as far from it as this water gets", a
relocation to the far corner, after which the duck is already as far as the
water allows and per-step distance change is boundary jitter.

So the primary measure is the logic's own objective: the duck's distance to
the corner of its water furthest from the robot, using `farthest_corner` --
the mover's own function, so "away" means here exactly what it means there.
Reacting ducks sit closer to that corner while the robot is inside the radius
than while it is outside, when the logic is not engaged at all.

The null is reported beside it because somebody will compute it again, and a
null nobody explained reads like a contradiction.

**Why sharing `farthest_corner` with the mover does not make this
self-observing.** The objection is the right one to raise -- an instrument that
borrows the implementation it is testing usually proves only that the
implementation agrees with itself. It does not apply here, because of what the
claim rests on. The claim is not "ducks are near the far corner", which a
shared function could indeed manufacture; it is the *contrast* between the
inside-the-radius arm and the outside-the-radius arm. That contrast is
produced by the bus: by whether the mover actually received a robot pose and
engaged its avoidance. If it had received none, both arms would report the same
distance-to-corner -- which is exactly what a random walk gives -- no matter
whose definition of the corner was used. `farthest_corner` names the
destination; it cannot create the difference between the two arms, and the
difference is the finding.

What *would* be self-observing is an instrument that computed where the ducks
should be by calling `step_for`, then checked they were there. This one reads
positions out of the recording and never runs the mover's stepping logic at
all. The shared symbol is a shared definition of "away", which is the honest
choice: a second, private definition could disagree with the code under test
and the measurement would then be of the disagreement.

Exits non-zero if either arm has too few observations to mean anything, rather
than reporting a statistic over three samples.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mcap.reader import make_reader  # noqa: E402

from spatialdds_demo.duck_mover import (  # noqa: E402
    AVOID_ENTITY_ID, AVOID_RADIUS_M, AVOID_STALE_S, DEFAULT_BOUNDS_ENTITY,
    farthest_corner)
from spatialdds.dds.types.spatial.owm import Entity, ModelPose  # noqa: E402

#: Below this, a percentage is noise dressed as a measurement.
MIN_PER_ARM = 20

NS = 1_000_000_000


def read_poses(path: Path):
    """`(poses, water)`: pose tracks per entity, and the ducks' bounds.

    The bounds come out of the recording's own entity lane rather than from
    `seed_entities()`, because a session may have reshaped the water and the
    measure has to use the box the ducks were actually being clamped into.
    """
    out: dict[str, list[tuple[int, float, float]]] = {}
    water = None
    with open(path, "rb") as f:
        for _sch, ch, msg in make_reader(f).iter_messages():
            if "/model_pose/" in ch.topic:
                p = ModelPose.deserialize(msg.data)
                out.setdefault(p.entity_id, []).append(
                    (msg.log_time, p.pose.t[0], p.pose.t[1]))
            elif "/entity/" in ch.topic:
                e = Entity.deserialize(msg.data)
                if e.entity_id == DEFAULT_BOUNDS_ENTITY and e.has_extent:
                    water = e.extent          # last one wins: a reshape counts
    for v in out.values():
        v.sort()
    return out, water


def robot_at(robot: list[tuple[int, float, float]], t: int):
    """The robot's last known position at time `t`, or None if stale/unknown.

    Stale matters: the mover ignores a robot pose older than AVOID_STALE_S and
    stops avoiding, so a measurement that used an hour-old pose would be
    scoring the mover against information it had discarded.
    """
    i = bisect.bisect_right([r[0] for r in robot], t) - 1
    if i < 0:
        return None
    rt, rx, ry = robot[i]
    if (t - rt) > AVOID_STALE_S * NS:
        return None
    return rx, ry


def measure(path: Path) -> dict:
    poses, water = read_poses(path)
    robot = poses.get(AVOID_ENTITY_ID, [])
    ducks = {k: v for k, v in poses.items() if k.startswith("ent:duck")}
    if water is None:
        raise SystemExit(f"{path.name}: no {DEFAULT_BOUNDS_ENTITY} with an "
                         f"extent in the recording, so there is no water to "
                         f"measure a far corner of")

    # Primary: distance to the corner of the water furthest from the robot,
    # which is where the mover is actually steering. Secondary: the raw
    # distance to the robot, and the per-step null the first attempt measured.
    arms = {"inside": {"n": 0, "to_corner": 0.0, "to_robot": 0.0},
            "outside": {"n": 0, "to_corner": 0.0, "to_robot": 0.0}}
    steps = {"inside": {"n": 0, "away": 0}, "outside": {"n": 0, "away": 0}}
    skipped_stale = 0

    for duck, samples in sorted(ducks.items()):
        for t, x, y in samples:
            r = robot_at(robot, t)
            if r is None:
                skipped_stale += 1
                continue
            d_robot = math.dist((x, y), r)
            corner = farthest_corner(water, r, entity_id=duck)
            a = arms["inside" if d_robot < AVOID_RADIUS_M else "outside"]
            a["n"] += 1
            a["to_corner"] += math.dist((x, y), corner)
            a["to_robot"] += d_robot
        for (t1, x1, y1), (t2, x2, y2) in zip(samples, samples[1:]):
            r1, r2 = robot_at(robot, t1), robot_at(robot, t2)
            if r1 is None or r2 is None:
                continue
            before, after = math.dist((x1, y1), r1), math.dist((x2, y2), r2)
            if math.isclose(before, after, abs_tol=1e-9):
                continue
            s = steps["inside" if before < AVOID_RADIUS_M else "outside"]
            s["n"] += 1
            if after > before:
                s["away"] += 1

    for a in arms.values():
        n = a["n"] or 1
        a["mean_distance_to_far_corner_m"] = a.pop("to_corner") / n
        a["mean_distance_to_robot_m"] = a.pop("to_robot") / n
    for s in steps.values():
        s["away_fraction"] = (s["away"] / s["n"]) if s["n"] else None

    return {
        "recording": path.name,
        "avoid_entity": AVOID_ENTITY_ID,
        "avoid_radius_m": AVOID_RADIUS_M,
        "water": DEFAULT_BOUNDS_ENTITY,
        "robot_pose_samples": len(robot),
        "duck_pose_samples": {k: len(v) for k, v in sorted(ducks.items())},
        "samples_skipped_no_fresh_robot_pose": skipped_stale,
        "arms": arms,
        "per_step_null": steps,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "",
                                 formatter_class=argparse
                                 .RawDescriptionHelpFormatter)
    ap.add_argument("recording", type=Path)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    m = measure(args.recording)
    if args.json:
        text = json.dumps(m, indent=2)
    else:
        ins, out = m["arms"]["inside"], m["arms"]["outside"]
        lines = [
            f"reaction measured from {m['recording']}",
            f"  avoiding {m['avoid_entity']} within {m['avoid_radius_m']:.1f} m,"
            f" water = {m['water']}",
            f"  {m['robot_pose_samples']} robot pose sample(s); duck poses "
            f"{', '.join(f'{k.split(':')[-1]}={v}' for k, v in m['duck_pose_samples'].items())}",
            "",
            f"  {'robot':9} {'duck obs':>9} {'to far corner':>14} "
            f"{'to robot':>10}",
        ]
        for name, a in (("inside", ins), ("outside", out)):
            lines.append(
                f"  {name:9} {a['n']:>9} "
                f"{a['mean_distance_to_far_corner_m']:>12.2f} m "
                f"{a['mean_distance_to_robot_m']:>8.1f} m")
        closer = (out["mean_distance_to_far_corner_m"]
                  - ins["mean_distance_to_far_corner_m"])
        lines += [
            "",
            f"  ducks sit {closer:+.2f} m closer to the corner furthest from "
            f"the robot",
            f"  while it is inside the radius than while it is outside"
            f"{' — the reaction' if closer > 0 else ' — NO reaction'}",
        ]
        n_ins = m["per_step_null"]["inside"]
        n_out = m["per_step_null"]["outside"]
        lines += [
            "",
            "  the reported null, so nobody re-derives it as a contradiction:",
            f"    per-step 'distance to robot increased' — inside "
            f"{n_ins['away_fraction']:.0%} of {n_ins['n']}, outside "
            f"{n_out['away_fraction']:.0%} of {n_out['n']}",
            "    flat by design: the mover relocates to the far corner "
            "rather than nudging away each step,",
            "    so once a duck is there, per-step change is boundary jitter.",
        ]
        if m["samples_skipped_no_fresh_robot_pose"]:
            lines.append(f"  ({m['samples_skipped_no_fresh_robot_pose']} "
                         f"sample(s) skipped: no robot pose fresher than "
                         f"{AVOID_STALE_S:.0f}s, which is when the mover stops "
                         f"avoiding too)")
        thin = [n for n, a in (("inside", ins), ("outside", out))
                if a["n"] < MIN_PER_ARM]
        lines.append("")
        if thin:
            lines.append(f"  INCONCLUSIVE: {', '.join(thin)} arm has fewer "
                         f"than {MIN_PER_ARM} steps; a percentage of that many "
                         f"observations is noise with a decimal point")
        else:
            lines.append(f"  both arms have >= {MIN_PER_ARM} steps, so the "
                         f"comparison stands on its own")
        text = "\n".join(lines)

    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    thin = any(a["n"] < MIN_PER_ARM for a in m["arms"].values())
    return 1 if thin else 0


if __name__ == "__main__":
    sys.exit(main())
