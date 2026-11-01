#!/usr/bin/env python3
"""The four teeth, measured from a session's files.

    python3 -m scenarios.crossing.teeth FULL.mcap --silenced SILENCED.mcap

A session is named by its recording; beside it are the world's truth log
(`.truth.jsonl.gz`), the live subscriber's log (`.watch.jsonl.gz`) and the
provenance (`.provenance.json`), as `run_session.py` writes them.

1. **The silenced unit.** car-a1 with mmwave-ut-04 running, and with it
   SIGKILLed: when the controller first held the car below its speed for
   the crosswalk (the world's record of the command), when the recording's
   own poses show it slowing, where it stopped, its least distance to
   ped-17 on the approach (up to that stop), and when ped-17 got off the
   crosswalk. In the silenced arm, the unit's held records: liveliness
   ended, none disposed, none retired.
2. **The declared gate.** C3's instant, each protagonist's first gate-driven
   command and braking onset, the arms' instants as the gate's own record
   declared them, and when the SUMO-native traffic at the head of each lane's
   queue began to brake.
3. **The prediction's lifecycle**, C1, as the live subscriber received it,
   with P2's revision whole: the last DERIVED payload and the OBSERVED one.
4. **The derived claim's lineage**, C5: each `derived_from` reference looked
   up in the recording.

Plus **the clocks**: every claim's stamp against the simulation time it was
decided at (`demo.decided_t`), since `claims.ClaimWriter` stamps with the
wall clock and the scene's other records are on the simulation's.

Ground truth is what the world did; the recording and the subscriber's log
are what the bus carried. Each number says which it comes from.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

SCENARIO = json.loads((Path(__file__).resolve().parent / "scenario.json").read_text())
ZONES = {z["id"]: z for z in SCENARIO["zones"]}
UNIT = SCENARIO["sensing_unit"]["id"]
GATE_CTL = SCENARIO["gate"]["controller"]
PROTAGONISTS = sorted(SCENARIO["protagonists"])

#: Below this a vehicle is stopped, m/s.
STOPPED_MPS = 0.1
#: A recorded speed this far under the speed at the stop's initiation shows
#: the car slowing on the bus, m/s.
SLOWED_MPS = 1.0


def secs(t: dict) -> float:
    # N.1 renders a 64-bit integer as a string.
    return int(t["sec"]) + int(t["nanosec"]) / 1e9


# -- loading -----------------------------------------------------------------


class Session:
    def __init__(self, mcap: Path) -> None:
        self.mcap = Path(mcap)
        base = str(self.mcap)[: -len(".mcap")]
        self.truth_path = Path(base + ".truth.jsonl.gz")
        self.watch_path = Path(base + ".watch.jsonl.gz")
        self.prov = json.loads(Path(base + ".provenance.json").read_text())
        self.frames: List[dict] = []
        with gzip.open(self.truth_path, "rt") as f:
            for line in f:
                msg = json.loads(line)
                if msg.get("type") == "hello":
                    self.hello = msg
                elif msg.get("type") == "truth":
                    self.frames.append(msg)
        self.epoch = float(self.hello["epoch"])
        self.watch: List[dict] = []
        if self.watch_path.exists():
            with gzip.open(self.watch_path, "rt") as f:
                self.watch = [json.loads(line) for line in f]
        self._read_recording()

    def _read_recording(self) -> None:
        from mcap.reader import make_reader
        from spatialdds.dds.codec import to_json
        from spatialdds.dds.types.spatial.owm import Entity, ModelPose
        from spatialdds_demo_idl18.oarc_owm import Relationship
        import spatialdds_demo.lanes  # noqa: F401  registers the extension
        self.entities: List[Tuple[str, dict]] = []      # (topic, json)
        self.poses: List[dict] = []
        self.edges: List[Tuple[str, dict]] = []
        self.disposes: List[dict] = []
        decode = {"/world/entity/": Entity, "/world/model_pose/": ModelPose,
                  "/world/oarc.relationship/": Relationship}
        with open(self.mcap, "rb") as f:
            reader = make_reader(f)
            for _s, ch, msg in reader.iter_messages(log_time_order=True):
                cls = next((c for k, c in decode.items() if k in ch.topic), None)
                if cls is None:
                    continue
                doc = to_json(cls.deserialize(msg.data))
                if cls is ModelPose:
                    self.poses.append(doc)
                elif cls is Entity:
                    self.entities.append((ch.topic, doc))
                else:
                    self.edges.append((ch.topic, doc))
            f.seek(0)
            for m in make_reader(f).iter_metadata():
                if m.name.endswith("dispose"):
                    self.disposes.append(dict(m.metadata))

    def t_of(self, stamp: dict) -> float:
        """A stamp on the session clock."""
        return round(secs(stamp) - self.epoch, 3)

    def killed(self) -> Optional[Tuple[str, float]]:
        driver = self.prov.get("driven_by", "")
        if "SIGKILL " not in driver:
            return None
        name, _, at = driver.split("SIGKILL ", 1)[1].split(")")[0].partition(" at t=")
        return name, float(at)


# -- geometry and motion ---------------------------------------------------------


def point_to_footprint(px: float, py: float, a: dict) -> float:
    c, s = math.cos(math.radians(a["yaw_deg"])), math.sin(math.radians(a["yaw_deg"]))
    dx, dy = px - a["x"], py - a["y"]
    lx, ly = c * dx + s * dy, -s * dx + c * dy
    ox = max(abs(lx) - a["length"] / 2, 0.0)
    oy = max(abs(ly) - a["width"] / 2, 0.0)
    return math.hypot(ox, oy)


def braking_began(series: List[Tuple[float, float, float]], stop_t: float,
                  look_s: float = 20.0, tol: float = 0.3) -> Optional[float]:
    """When the braking that ended in the stop at `stop_t` began: the last
    moment, in the `look_s` before it, that the vehicle was within `tol` of
    the fastest it had been in that span (`tol` is SUMO's dawdling)."""
    peak, began = 0.0, None
    for t, v, _x in series:
        if t < stop_t - look_s - 1e-9 or t > stop_t + 1e-9:
            continue
        peak = max(peak, v)
        if v >= peak - tol:
            began = t
    return None if began is None else round(began, 1)


def approach(zone: str, east: bool, reach: float = 30.0) -> Tuple[float, float]:
    """The x range, short of a zone, a vehicle's centre stops in."""
    (x0, _, _), (x1, _, _) = ZONES[zone]["min_xyz"], ZONES[zone]["max_xyz"]
    return (x0 - reach, x0) if east else (x1, x1 + reach)


def truth_speed(s: Session, vid: str) -> List[Tuple[float, float, float]]:
    return [(fr["t"], fr["actors"][vid]["speed"], fr["actors"][vid]["x"])
            for fr in s.frames if vid in fr["actors"]]


def recorded_speed(s: Session, key: str) -> List[Tuple[float, float, float]]:
    """A record's speed from its own published poses, over 0.5 s."""
    pts = sorted((s.t_of(p["stamp"]), p["pose"]["t"][0], p["pose"]["t"][1])
                 for p in s.poses if p["entity_id"] == key)
    out, j = [], 0
    for i, (t, x, y) in enumerate(pts):
        while pts[j][0] < t - 0.5 - 1e-6:
            j += 1
        t0, x0, y0 = pts[j]
        if t - t0 >= 0.45:
            out.append((round(t, 1), math.hypot(x - x0, y - y0) / (t - t0), x))
    return out


def commands(s: Session, vid: str) -> List[dict]:
    """Every command the world applied to `vid`, with the speed the car was
    doing when it was applied (the frame before: a frame's `applied` took
    effect in its own step)."""
    out, speed = [], None
    for fr in s.frames:
        for c in fr["applied"]:
            if c.get("cmd") == "speed" and c.get("id") == vid:
                out.append(dict(c, speed_then=speed))
        speed = fr["actors"].get(vid, {}).get("speed")
    return out


def slowed(series: List[Tuple[float, float, float]], t0: float, v0: float,
           drop: float = SLOWED_MPS) -> Optional[float]:
    """The first time at or after `t0` the speed is `drop` below `v0`."""
    return next((t for t, v, _x in series if t >= t0 - 1e-9 and v <= v0 - drop),
                None)


def stopped(series: List[Tuple[float, float, float]], after: float,
            near: Tuple[float, float] = (-1e9, 1e9)
            ) -> Optional[Tuple[float, float]]:
    """(time, x) of the first stop at or after `after` with x in `near`."""
    lo, hi = near
    return next(((t, round(x, 2)) for t, v, x in series if t >= after - 1e-9
                 and v < STOPPED_MPS and lo <= x <= hi), None)


def first_hold(s: Session, vid: str, why: str) -> Optional[dict]:
    """The first command, for a reason containing `why`, that held the car
    below the speed it was doing."""
    for c in commands(s, vid):
        if why in c.get("why", "") and c.get("cap") is not None \
                and c["speed_then"] is not None and c["cap"] < c["speed_then"]:
            return c
    return None


# -- the teeth ---------------------------------------------------------------------


def crosswalk_arm(s: Session) -> dict:
    car, ped = "car-a1", "ped-17"
    cw = ZONES["crosswalk"]
    hold = first_hold(s, car, "pedestrian in the crosswalk") or None
    own = first_hold(s, car, "own perception")
    if hold is None or (own is not None and own["applied_at"] < hold["applied_at"]):
        hold = own
    stop = stopped(truth_speed(s, car), 20.0)
    best = (1e9, None)
    overlap = False
    left = None
    for fr in s.frames:
        p = fr["actors"].get(ped)
        if p and left is None and fr["t"] > 34.0:
            (x0, y0, _), (x1, y1, _) = cw["min_xyz"], cw["max_xyz"]
            if p["y"] > y1:
                left = fr["t"]
        if stop is not None and fr["t"] > stop[0]:
            continue                       # the approach ends at the stop
        a = fr["actors"].get(car)
        if a and p:
            d = point_to_footprint(p["x"], p["y"], a)
            if d < best[0]:
                best = (d, fr["t"])
            (x0, y0, _), (x1, y1, _) = cw["min_xyz"], cw["max_xyz"]
            if x0 <= p["x"] <= x1 and y0 <= p["y"] <= y1 and \
                    a["x"] + a["length"] / 2 >= x0 and a["x"] - a["length"] / 2 <= x1:
                overlap = True
    front = None if stop is None else round(stop[1] + 4.6 / 2 - cw["min_xyz"][0], 2)
    return {
        "stop_initiated": None if hold is None else {
            "applied_at": hold["applied_at"], "cap": hold["cap"],
            "speed": hold["speed_then"], "why": hold["why"]},
        "slowed_recorded": None if hold is None else slowed(
            recorded_speed(s, "ent:operator-a:car-a1"), hold["applied_at"],
            hold["speed_then"]),
        "stopped": None if stop is None else stop[0],
        "stopped_front_past_crosswalk_edge_m": front,
        "braking_began_truth": None if stop is None else braking_began(
            truth_speed(s, car), stop[0]),
        "min_distance_m": round(best[0], 2), "min_distance_at": best[1],
        "in_crosswalk_with_ped": overlap,
        "ped_off_the_crosswalk": left,
    }


def silenced(s: Session) -> dict:
    killed = s.killed()
    out = {"killed": killed}
    if killed is None:
        return out
    name, at = killed
    sid = f"svc:crossing:{name}"
    prefix = f"ent:{name}:"
    edge_keys = {doc["id"] for _t, doc in s.edges if doc["asserter"] == sid}
    lost = sorted({r["key"] for r in s.watch
                   if r["event"] == "liveliness" and not r["alive"]
                   and r["key"] in edge_keys})
    disposed_rel = sorted({r["key"] for r in s.watch
                           if r["event"] == "dispose" and r["key"] in edge_keys})
    left = sorted({d["key"] for d in s.prov.get("latched_writer_left", [])
                   if d["key"].startswith(prefix) or d["key"] in edge_keys})
    disposed = sorted({d["key"] for d in s.disposes
                       if d["key"].startswith(prefix) or d["key"] in edge_keys})
    retired = sorted({doc["entity_id"] for _t, doc in s.entities
                      if doc["source_id"] == sid and doc["state"] == "RETIRED"}
                     | {doc["id"] for _t, doc in s.edges
                        if doc["asserter"] == sid and doc["state"] == "RETIRED"})
    out.update(claims_held=sorted(edge_keys),
               claims_liveliness_lost_live=lost,
               claims_disposed_live=disposed_rel,
               records_writer_left_recorded=left,
               records_disposed_recorded=disposed,
               records_retired_recorded=retired)
    return out


def edges_of(s: Session, asserter: str, predicate_tail: str) -> List[Tuple[str, dict]]:
    sid = f"svc:crossing:{asserter}"
    return [(t, d) for t, d in s.edges
            if d["asserter"] == sid and d["predicate"].endswith(predicate_tail)]


def gate_arm(s: Session) -> dict:
    c3 = edges_of(s, GATE_CTL, "/closes")
    created = next(d for _t, d in c3 if d["state"] == "CREATED")
    meta = {kv["key"]: kv["value"] for kv in created["meta"]}
    out = {"C3": {"id": created["id"], "stamp": s.t_of(created["stamp"]),
                  "decided_t": float(meta["demo.decided_t"]),
                  "valid_from": s.t_of(created["valid_from"])}}
    arms = []
    last = None
    for _t, d in s.entities:
        if d["entity_id"] != f"ent:{GATE_CTL}:gate-9":
            continue
        state = {kv["key"]: kv["value"] for kv in d["properties"]}.get("demo.gate_state")
        if state != last:
            arms.append((s.t_of(d["stamp"]), state))
            last = state
    out["arms_recorded"] = arms
    out["arms_applied"] = [(c["applied_at"], c["state"]) for fr in s.frames
                           for c in fr["applied"] if c.get("cmd") == "crossing"]
    after = out["C3"]["decided_t"]
    for car in PROTAGONISTS:
        operator = SCENARIO["protagonists"][car]["operator"]
        hold = first_hold(s, car, created["id"])
        gate = approach("crossing-zone", car in SCENARIO["fleets"]["operator-a"])
        out[car] = {
            "stop_initiated": None if hold is None else {
                "applied_at": hold["applied_at"], "cap": hold["cap"],
                "speed": hold["speed_then"]},
            "slowed_recorded": None if hold is None else slowed(
                recorded_speed(s, f"ent:{operator}:{car}"), hold["applied_at"],
                hold["speed_then"]),
        }
        stop = stopped(truth_speed(s, car), after, gate)
        out[car]["stopped"] = None if stop is None else stop[0]
        out[car]["braking_began_truth"] = None if stop is None else \
            braking_began(truth_speed(s, car), stop[0])
    out["native_heads"] = native_heads(s, after)
    return out


def native_heads(s: Session, after: float) -> List[dict]:
    """The SUMO-native road vehicle at the head of each lane's queue at the
    gate (none between it and the zone when it stopped), and when it began
    to brake."""
    (x0, _, _), (x1, _, _) = ZONES["crossing-zone"]["min_xyz"], ZONES["crossing-zone"]["max_xyz"]
    fleets = {v for f in SCENARIO["fleets"].values() for v in f}
    heads: Dict[Tuple[str, int], Tuple[float, str, bool]] = {}
    for fr in s.frames:
        if fr["t"] < after:
            continue
        for vid, a in fr["actors"].items():
            if a["kind"] not in ("car", "bus") or a["speed"] > 0.1:
                continue
            east = abs(a["yaw_deg"]) < 90
            gap = (x0 - (a["x"] + a["length"] / 2)) if east else ((a["x"] - a["length"] / 2) - x1)
            if not 0 <= gap <= 15:
                continue
            key = ("east" if east else "west", a["lane"])
            heads.setdefault(key, (fr["t"], vid, east))
    out = []
    for (way, lane), (t_stop, vid, east) in sorted(heads.items()):
        out.append({"lane": f"{way} {lane}", "vehicle": vid,
                    "native": vid not in fleets and vid not in SCENARIO["protagonists"],
                    "stopped_at": t_stop,
                    "braking_began_truth": braking_began(
                        truth_speed(s, vid), t_stop)})
    return out


def lifecycle(s: Session) -> dict:
    """C1 as the live subscriber received it."""
    sid = f"svc:crossing:{UNIT}"
    rows = [r for r in s.watch if r["event"] == "sample"
            and r["data"]["asserter"] == sid
            and r["data"]["subject_ref"] == f"ent:{UNIT}:ped-17"]
    if not rows:
        return {}
    cid = rows[0]["key"]
    seq = []
    before = after = None
    for r in s.watch:
        if r["key"] != cid:
            continue
        got = round(r["wall"] - s.epoch, 3)
        if r["event"] != "sample":
            seq.append({"received": got, "event": r["event"],
                        **({"reason": r.get("reason")} if r["event"] == "dispose"
                           else {"alive": r.get("alive")})})
            continue
        d = r["data"]
        meta = {kv["key"]: kv["value"] for kv in d["meta"]}
        seq.append({
            "received": got, "state": d["state"], "basis": d["basis"],
            "decided_t": float(meta["demo.decided_t"]) if "demo.decided_t" in meta else None,
            "stamp": s.t_of(d["stamp"]),
            "valid_from": s.t_of(d["valid_from"]) if d["has_valid_from"] else None,
            "valid_until": s.t_of(d["valid_until"]) if d["has_valid_until"] else None,
            "confidence": d["confidence"] if d["has_confidence"] else None,
            "how": meta.get("demo.how"),
            "derived_from": [(r2["key"], s.t_of(r2["stamp"])) for r2 in d["derived_from"]],
            "reason": d["state_reason"] or None})
        if d["basis"] == "DERIVED":
            before = d
        elif d["basis"] == "OBSERVED" and after is None and before is not None:
            after = d
    return {"id": cid, "sequence": seq, "p2_before": before, "p2_after": after}


def lineage(s: Session) -> dict:
    """Each C5 sample's references, looked up in the recording."""
    index = {(t, d["id"], json.dumps(d["stamp"], sort_keys=True)): d for t, d in s.edges}
    out = []
    for _t, d in edges_of(s, "fuser", "/blockedBy"):
        for ref in d["derived_from"]:
            hit = index.get((ref["topic"], ref["key"], json.dumps(ref["stamp"], sort_keys=True)))
            out.append({"c5_state": d["state"], "ref": ref["key"],
                        "ref_stamp": s.t_of(ref["stamp"]), "topic": ref["topic"],
                        "resolves": hit is not None,
                        "resolves_to": None if hit is None else
                        f"{hit['asserter']} {hit['predicate'].rsplit('/', 1)[-1]} "
                        f"{hit['state']} {hit['basis']}"})
    return {"references": out}


def clocks(s: Session) -> dict:
    """Each claim's stamp less the simulation time it was decided at, ms."""
    skew = []
    for _t, d in s.edges:
        meta = {kv["key"]: kv["value"] for kv in d["meta"]}
        if d["state"] == "RETIRED" or "demo.decided_t" not in meta:
            continue
        skew.append(round((secs(d["stamp"]) - s.epoch - float(meta["demo.decided_t"])) * 1000))
    skew.sort()
    return {"claims": len(skew), "median_ms": skew[len(skew) // 2] if skew else None,
            "min_ms": skew[0] if skew else None, "max_ms": skew[-1] if skew else None}


def measure(full: Path, silenced_path: Optional[Path] = None) -> dict:
    f = Session(full)
    out = {"tooth1": {"with": crosswalk_arm(f)}, "tooth2": gate_arm(f),
           "tooth3": lifecycle(f), "tooth4": lineage(f), "clocks": clocks(f)}
    if silenced_path is not None:
        k = Session(silenced_path)
        w, wo = out["tooth1"]["with"], crosswalk_arm(k)
        out["tooth1"]["without"] = wo
        out["tooth1"]["silenced"] = silenced(k)
        if w["stop_initiated"] and wo["stop_initiated"]:
            out["tooth1"]["initiated_earlier_by_s"] = round(
                wo["stop_initiated"]["applied_at"]
                - w["stop_initiated"]["applied_at"], 1)
        out["tooth1"]["min_distance_gain_m"] = round(
            w["min_distance_m"] - wo["min_distance_m"], 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("full", type=Path)
    ap.add_argument("--silenced", type=Path, default=None)
    args = ap.parse_args()
    print(json.dumps(measure(args.full, args.silenced), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
