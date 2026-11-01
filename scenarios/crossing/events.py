#!/usr/bin/env python3
"""The scene's events, from a session's truth log and recording; two compared.

    python3 -m scenarios.crossing.events RUN.mcap
    python3 -m scenarios.crossing.events A.mcap B.mcap

A session is named by its recording; its truth log is beside it
(`RUN.truth.jsonl.gz`, which `run_session.py` writes). A truth log alone, as
the in-process determinism test writes, is read for ground truth only.

An event is a named instant on the simulation clock: the bus arriving at
its stop and leaving it, ped-17 held at the kerb and released, entering and
leaving the crosswalk, the train entering and clearing the crossing zone,
each gate state the world applied, each protagonist entering and leaving
the two zones, and the longest queue stopped at the closed gate. These are the
two beats' motion, measured on ground truth rather than on what anyone
asserted.

**On the bus**, from the recording: each record's first appearance, every
change of its lifecycle state, each gate state the controller declared, each
claim's every sample (by what it says, its state and its basis), and every
dispose, all on the session's clock (a record's stamp, or a dispose's log
time, less the epoch the world service announced). A claim is stamped by
`claims.ClaimWriter` on the wall clock, which at rate 1 is the session's.

**Actuation**, from the truth log: each protagonist's first command for each
reason its controller gave, as the world applied it. These are what the
participants asserted, so they show that a rerun reproduces the assertions
and not only the street.

Comparing two runs prints each event's times side by side and the largest
difference, and exits 1 if any event is in one run and not the other or
differs by more than `--tolerance` (default one step, 0.1 s).
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Dict, Iterator, Optional

SCENARIO = json.loads((Path(__file__).resolve().parent / "scenario.json").read_text())
ZONES = {z["id"]: z for z in SCENARIO["zones"]}
GATE_NODE = SCENARIO["gate"]["node"]


def frames(path: Path) -> Iterator[dict]:
    with gzip.open(path, "rt") as f:
        for line in f:
            msg = json.loads(line)
            if msg.get("type") == "truth":
                yield msg


def inside(a: dict, zone: dict) -> bool:
    (x0, y0, _), (x1, y1, _) = zone["min_xyz"], zone["max_xyz"]
    return x0 <= a["x"] <= x1 and y0 <= a["y"] <= y1


def footprint_in(a: dict, zone: dict) -> bool:
    """Any part of the actor's footprint along its travel axis in the zone."""
    (x0, y0, _), (x1, y1, _) = zone["min_xyz"], zone["max_xyz"]
    half = a["length"] / 2
    if abs(a["yaw_deg"]) < 45 or abs(a["yaw_deg"]) > 135:     # east-west
        return a["x"] + half >= x0 and a["x"] - half <= x1 and y0 <= a["y"] <= y1
    return a["y"] + half >= y0 and a["y"] - half <= y1 and x0 <= a["x"] <= x1


#: How far back from the crossing zone a stopped road vehicle counts as queued.
QUEUE_REACH_M = 100.0


def queued(actors: Dict[str, dict]) -> int:
    """Road vehicles stopped on the arterial within reach of the crossing
    zone, on either side of it."""
    (x0, _, _), (x1, _, _) = ZONES["crossing-zone"]["min_xyz"], ZONES["crossing-zone"]["max_xyz"]
    return sum(1 for a in actors.values()
               if a["kind"] in ("car", "bus") and a["speed"] < 0.1
               and abs(a["y"]) < 6.4
               and (x0 - QUEUE_REACH_M <= a["x"] < x0
                    or x1 < a["x"] <= x1 + QUEUE_REACH_M))


def extract(path: Path) -> Dict[str, float]:
    ev: Dict[str, float] = {}
    state: Dict[str, object] = {}

    def edge(name: str, now: bool, t: float) -> None:
        was = state.get(name, False)
        if now and not was:
            ev.setdefault(f"{name} enter", t)
        if was and not now:
            ev.setdefault(f"{name} leave", t)
        state[name] = now

    queue_max, queue_at = 0, None
    for fr in frames(path):
        t, actors = fr["t"], fr["actors"]
        bus = actors.get("bus-3")
        edge("bus-3 stopped", bool(bus and bus["stopped"]), t)
        hold = fr["holds"].get("ped-17")
        if hold == "held":
            ev.setdefault("ped-17 held at the kerb", t)
        if hold == "released":
            ev.setdefault("ped-17 released", t)
        ped = actors.get("ped-17")
        edge("ped-17 crosswalk", bool(ped and inside(ped, ZONES["crosswalk"])), t)
        train = actors.get("train-7")
        edge("train-7 crossing-zone",
             bool(train and footprint_in(train, ZONES["crossing-zone"])), t)
        for c in fr["applied"]:
            if c.get("cmd") == "crossing":
                ev.setdefault(f"gate {c['state']}", c["applied_at"])
        for c in fr["applied"]:
            if c.get("cmd") == "speed" and c.get("why"):
                reason = c["why"].split(":")[0]
                if reason.startswith("claim "):
                    reason = "claim " + c["why"].split("(")[1].split(",")[0]
                ev.setdefault(f"{c['id']} command: {reason}", c["applied_at"])
        for car in SCENARIO["protagonists"]:
            a = actors.get(car)
            for z in ("crosswalk", "crossing-zone"):
                edge(f"{car} {z}", bool(a and footprint_in(a, ZONES[z])), t)
        if fr["crossings"].get(GATE_NODE) in ("warning", "closed"):
            n = queued(actors)
            if n > queue_max:
                queue_max, queue_at = n, t
    if queue_at is not None:
        ev[f"queue at the gate reaches {queue_max}"] = queue_at
    return dict(sorted(ev.items(), key=lambda kv: kv[1]))


def hello_of(truth: Path) -> dict:
    with gzip.open(truth, "rt") as f:
        return json.loads(f.readline())


def recorded(mcap: Path, epoch: float, rate: float = 1.0) -> Dict[str, float]:
    """Events on the bus, on the session clock (see the module docstring)."""
    from mcap.reader import make_reader
    from spatialdds.dds.types.spatial.owm import Entity
    from spatialdds_demo_idl18.oarc_owm import Relationship
    import spatialdds_demo.lanes  # noqa: F401  registers the extension

    ev: Dict[str, float] = {}
    last: Dict[str, tuple] = {}
    with open(mcap, "rb") as f:
        reader = make_reader(f)
        claims_seen: Dict[str, int] = {}
        for _sch, ch, msg in reader.iter_messages(log_time_order=True):
            if "/world/oarc.relationship/" in ch.topic:
                r = Relationship.deserialize(msg.data)
                t = round((r.stamp.sec + r.stamp.nanosec / 1e9 - epoch)
                          * (rate or 1.0), 1)
                what = (f"claim {r.subject_ref.split(':')[-1]} "
                        f"{r.predicate.rsplit('/', 1)[-1].split('#')[-1]} "
                        f"{r.object_ref.split(':')[-1]} {r.state.name} "
                        f"{r.basis.name}")
                claims_seen[what] = claims_seen.get(what, 0) + 1
                ev[f"{what} #{claims_seen[what]}"] = t
                continue
            if "/world/entity/" not in ch.topic:
                continue
            e = Entity.deserialize(msg.data)
            t = e.stamp.sec + e.stamp.nanosec / 1e9 - epoch
            if e.state.name == "RETIRED":
                # A terminal sample is stamped by the SDK's withdraw(), on
                # the wall clock, which runs at 1/rate of the scene's.
                t *= rate or 1.0
            t = round(t, 1)
            props = {kv.key: kv.value for kv in e.properties}
            key = (e.state.name, props.get("demo.gate_state"))
            if e.entity_id not in last:
                ev.setdefault(f"record {e.entity_id}", t)
            elif key != last[e.entity_id]:
                what = props.get("demo.gate_state") or e.state.name
                n = sum(1 for k in ev if k.startswith(f"record {e.entity_id} -> "))
                ev[f"record {e.entity_id} -> {what} #{n + 1}"] = t
            last[e.entity_id] = key
        f.seek(0)
        seen: Dict[str, int] = {}
        for m in make_reader(f).iter_metadata():
            if m.name.endswith("dispose"):
                k = m.metadata["key"]
                seen[k] = seen.get(k, 0) + 1
                # A dispose has no stamp, only the recorder's log time: the
                # wall clock, which runs at 1/rate of the scene's.
                wall = int(m.metadata["seq"]) / 1e9 - epoch
                ev[f"dispose {k} #{seen[k]}"] = round(wall * (rate or 1.0), 1)
    return ev


def session(path: Path) -> Dict[str, float]:
    """Every event of a session: ground truth, and the bus if recorded."""
    if path.name.endswith(".truth.jsonl.gz"):
        return extract(path)
    truth = path.with_suffix(".truth.jsonl.gz")
    out = extract(truth)
    hello = hello_of(truth)
    out.update(recorded(path, float(hello["epoch"]), float(hello["rate"])))
    return dict(sorted(out.items(), key=lambda kv: kv[1]))


def compare(a: Dict[str, float], b: Dict[str, float], tol: float) -> int:
    worst, bad = 0.0, []
    for name in sorted(set(a) | set(b), key=lambda n: a.get(n, b.get(n))):
        ta, tb = a.get(name), b.get(name)
        if ta is None or tb is None:
            bad.append(name)
            print(f"  {name:42} {fmt(ta):>8} {fmt(tb):>8}   only in one run")
            continue
        d = abs(ta - tb)
        worst = max(worst, d)
        flag = "" if d <= tol + 1e-9 else "   beyond tolerance"
        if flag:
            bad.append(name)
        print(f"  {name:42} {ta:8.1f} {tb:8.1f} {d:6.2f}{flag}")
    print(f"events: {len(set(a) & set(b))} in both runs, largest difference "
          f"{worst:.2f} s, tolerance {tol:g} s, {len(bad)} outside it")
    return 1 if bad else 0


def fmt(t: Optional[float]) -> str:
    return "-" if t is None else f"{t:.1f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--tolerance", type=float, default=0.1)
    args = ap.parse_args()
    if len(args.logs) == 1:
        for name, t in session(args.logs[0]).items():
            print(f"  {t:6.1f}  {name}")
        return 0
    return compare(session(args.logs[0]), session(args.logs[1]), args.tolerance)


if __name__ == "__main__":
    sys.exit(main())
