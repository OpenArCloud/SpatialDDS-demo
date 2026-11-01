#!/usr/bin/env python3
"""Export the committed crossing session as the replay viewer's asset.

    python3 -m scenarios.crossing.export_replay            # writes web/crossing/crossing-replay.json
    python3 -m scenarios.crossing.export_replay --check    # exit 1 if that file is stale

**owm-replay/2**: owm-replay/1's shape (docs: the OWM replay export brief),
extended additively with a map layer and heading on tracks. One JSON
document, deterministic (the same recording gives the same bytes), under
`CEILING_BYTES`:

* `meta`: the recording, its digest and commit, capture date, the window
  (session seconds, and absolute stamps), the loop, and the two honesty
  lines the page prints: the world is simulated; every claim is a record.
* `map`: buildings (footprint and height), zones (outlines), lane and rail
  centre lines, the roadside unit's mount and the gate, each from the
  record the map author, the unit or the gate controller published, with
  that record's key.
* `tracks`: the moving world, from the truth log the recording ships with,
  sampled every `TRACK_DT` s (`meta.track_dt`): position, heading, size, and which bus
  records describe each thing. Simulated, and labelled so.
* `gate`: gate-9's declared states, each from its record.
* `sight`: when car-a1's line of sight to ped-17 is blocked, and by what,
  by the scene's visibility model over the simulated world.
* `claims`: one entry per edge id: what it says, its segments (each
  assertion's interval, its revisions, and how it ended, with the recorded
  reason verbatim), and every sample of it in the recording, verbatim in
  the binding's JSON form, for the provenance card.
* `events`: the narration, in time order, each line naming its sources: a
  record (lane, key, stamp), or the truth log (the world's own record of
  what it applied) where the bus has no record of the thing.

Times are seconds from the window's start; `meta.window` gives the session
time it starts at. Nothing is hand-authored but sentence templates: every
number and every state is read from the session's files.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from scenarios.crossing import teeth, visibility
from scenarios.crossing.teeth import Session, secs

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RECORDING = HERE / "recording" / "crossing.mcap"
ASSET = REPO / "web" / "crossing" / "crossing-replay.json"
SCENARIO = json.loads((HERE / "scenario.json").read_text())

SCHEMA = "owm-replay/2"
#: The window, in session seconds: both beats, from the bus stopping to the
#: protagonists clearing the crossing.
WINDOW = (20.0, 82.0)
#: The track grid: 5 Hz, a whole number of the world's 0.1 s steps.
TRACK_STEPS = 2
TRACK_DT = TRACK_STEPS * 0.1
CEILING_BYTES = 300_000

WORLD_LINE = ("The world is simulated: SUMO, seed 42, every vehicle and the "
              "pedestrian moved by the simulator.")
CLAIMS_LINE = ("Every claim is a real record: recorded off the bus and "
               "validated, shown as it was written.")

ENTITY_TOPIC = "spatialdds/crossing/world/entity/v1"
REL_TOPIC = "spatialdds/crossing/world/oarc.relationship/v1"


def r1(v: float) -> float:
    return round(v + 0.0, 1)


def r2(v: float) -> float:
    return round(v + 0.0, 2)


class Export:
    def __init__(self, mcap: Path = RECORDING) -> None:
        self.s = Session(mcap)
        self.mcap = mcap
        self.t0, self.t1 = WINDOW

    def rel(self, session_t: float) -> float:
        return r2(session_t - self.t0)

    def stamp_rel(self, stamp: dict) -> float:
        return self.rel(secs(stamp) - self.s.epoch)

    # -- meta ------------------------------------------------------------------

    def meta(self) -> dict:
        prov = self.s.prov
        try:
            commit = subprocess.run(
                ["git", "log", "-1", "--format=%H", "--", str(self.mcap)],
                cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = ""
        return {
            "recording": self.mcap.name,
            "sha256": prov["sha256"],
            "commit": commit,
            "captured_utc": prov["captured_utc"],
            "driven_by": prov["driven_by"],
            "scene": prov["scene"],
            "frame": SCENARIO["frame_fqn"],
            "window": {"session_start": self.t0, "session_end": self.t1,
                       "stamp_start": round(self.s.epoch + self.t0, 3),
                       "stamp_end": round(self.s.epoch + self.t1, 3)},
            "loop_s": r1(self.t1 - self.t0),
            "track_dt": r2(TRACK_DT),
            "honesty": {"world": WORLD_LINE, "claims": CLAIMS_LINE},
        }

    # -- the map -----------------------------------------------------------------

    def latest_records(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for _t, d in self.s.entities:
            if d["state"] != "RETIRED":
                out.setdefault(d["entity_id"], d)
        return out

    def map(self) -> dict:
        recs = self.latest_records()
        props = lambda d: {kv["key"]: kv["value"] for kv in d["properties"]}

        def box(d):
            lo, hi = d["extent"]["min_xyz"], d["extent"]["max_xyz"]
            return [r2(lo[0]), r2(lo[1]), r2(hi[0]), r2(hi[1])], r2(hi[2])

        buildings, zones, lanes, rail = [], [], [], []
        for eid in sorted(recs):
            d = recs[eid]
            if not eid.startswith("ent:map:"):
                continue
            (x0, y0, x1, y1), top = box(d)
            kind = d["type_uris"][0].rsplit("/", 1)[-1]
            name = eid.split(":")[-1]
            ref = {"key": eid, "basis": d["basis"]}
            if name.startswith("bldg-"):
                buildings.append({"id": name, "box": [x0, y0, x1, y1],
                                  "height": top, "record": ref})
            elif name.startswith("lane-"):
                lanes.append({"id": name, "line": [[x0, r2((y0 + y1) / 2)],
                                                   [x1, r2((y0 + y1) / 2)]],
                              "box": [x0, y0, x1, y1], "record": ref})
            elif name == "rail-line":
                cx = (x0 + x1) / 2
                rail.append({"id": name, "gauge_lines": [
                    [[r2(cx - 0.72), y0], [r2(cx - 0.72), y1]],
                    [[r2(cx + 0.72), y0], [r2(cx + 0.72), y1]]],
                    "box": [x0, y0, x1, y1], "record": ref})
            else:
                zones.append({"id": name, "label": props(d).get("demo.label", name),
                              "box": [x0, y0, x1, y1], "type": kind,
                              "record": ref})
        unit = recs[f"ent:{teeth.UNIT}:self"]
        gate = recs[f"ent:{teeth.GATE_CTL}:gate-9"]
        (gx0, gy0, gx1, gy1), _ = box(gate)
        return {
            "buildings": buildings, "zones": zones, "lanes": lanes, "rail": rail,
            "unit": {"id": teeth.UNIT, "at": [r2(v) for v in unit["pose"]["t"]],
                     "range_m": float(props(unit)["demo.range_m"]),
                     "emulation": props(unit)["demo.emulation"],
                     "record": {"key": unit["entity_id"], "basis": unit["basis"]}},
            "gate": {"id": "gate-9", "box": [gx0, gy0, gx1, gy1],
                     "record": {"key": gate["entity_id"], "basis": gate["basis"]}},
        }

    # -- the moving world -----------------------------------------------------

    def tracks(self) -> List[dict]:
        """Every actor in the window, from the truth log, on a fixed grid."""
        rows: Dict[str, dict] = {}
        for fr in self.s.frames:
            t = fr["t"]
            if t < self.t0 - 1e-9 or t > self.t1 + 1e-9:
                continue
            if round(t * 10) % TRACK_STEPS:
                continue
            for aid, a in fr["actors"].items():
                tr = rows.setdefault(aid, {
                    "id": aid, "kind": a["kind"],
                    "size": [r2(a["length"]), r2(a["width"]), r2(a["height"])],
                    "t0": self.rel(t), "x": [], "y": [], "h": []})
                tr["x"].append(r1(a["x"]))
                tr["y"].append(r1(a["y"]))
                tr["h"].append(int(round(a["yaw_deg"])))
        published = {}
        for _t, d in self.s.entities:
            thing = d["entity_id"].split(":")[-1]
            published.setdefault(thing, set()).add(d["entity_id"])
        roles = {v: f"protagonist · {p['operator']}" for v, p in
                 SCENARIO["protagonists"].items()}
        fleets = {v: op for op, vs in SCENARIO["fleets"].items() for v in vs}
        out = []
        for aid in sorted(rows):
            tr = rows[aid]
            tr["role"] = roles.get(aid) or (f"fleet · {fleets[aid]}" if aid in fleets
                                            else "background")
            tr["records"] = sorted(published.get(aid, ()))
            out.append(tr)
        return out

    def gate(self) -> List[dict]:
        out, last = [], None
        for _t, d in self.s.entities:
            if d["entity_id"] != f"ent:{teeth.GATE_CTL}:gate-9":
                continue
            state = {kv["key"]: kv["value"] for kv in d["properties"]}.get("demo.gate_state")
            if state != last:
                out.append({"t": self.stamp_rel(d["stamp"]), "state": state,
                            "record": d})
                last = state
        return out

    def sight(self) -> List[dict]:
        """Intervals in which the bus (or anything) blocks car-a1's view of
        ped-17, by the visibility model the operators publish through."""
        h = SCENARIO["operator_sensing"]["sensor_height_m"]
        rng = SCENARIO["operator_sensing"]["range_m"]
        sight = visibility.Sight(SCENARIO["buildings"])
        out: List[dict] = []
        cur: Optional[dict] = None
        for fr in self.s.frames:
            t = fr["t"]
            if t < self.t0 - 1e-9 or t > self.t1 + 1e-9:
                continue
            a, p = fr["actors"].get("car-a1"), fr["actors"].get("ped-17")
            blocked = None
            if a and p:
                occl = {k: v for k, v in fr["actors"].items()
                        if v["kind"] in visibility.OCCLUDING_KINDS}
                blocked = sight.visible((a["x"], a["y"], a["z"] + h), rng,
                                        {"ped-17": p, **occl}).get("ped-17")
            if blocked and (cur is None or cur["blocked_by"] != blocked):
                cur = {"from": self.rel(t), "to": self.rel(t),
                       "observer": "car-a1", "target": "ped-17",
                       "blocked_by": blocked}
                out.append(cur)
            elif blocked:
                cur["to"] = self.rel(t)
            else:
                cur = None
        return out

    # -- claims -----------------------------------------------------------------

    def claims(self) -> List[dict]:
        by_id: Dict[str, List[dict]] = {}
        order: List[str] = []
        for _t, d in self.s.edges:
            if d["id"] not in by_id:
                order.append(d["id"])
            by_id.setdefault(d["id"], []).append(d)
        disposes = {}
        for r in self.s.watch:
            if r["event"] == "dispose":
                disposes.setdefault(r["key"], []).append(r.get("reason", ""))
        names = {("mmwave-ut-04", "ped-17"): "C1", ("mmwave-ut-04", "bus-3"): "C2",
                 ("gate-ctl-9", "gate-9"): "C3", ("mmwave-ut-04", "train-7"): "C4",
                 ("fuser", "crossing-zone"): "C5"}
        out = []
        for cid in order:
            samples = by_id[cid]
            first = samples[0]
            asserter = first["asserter"].split(":")[-1]
            subject = first["subject_ref"].split(":")[-1]
            segs, cur = [], None
            for d in samples:
                t = self.stamp_rel(d["stamp"])
                if d["state"] in ("CREATED",) or cur is None:
                    cur = {"from": t, "to": None, "ending": None, "reason": None,
                           "revisions": []}
                    segs.append(cur)
                if d["state"] == "RETIRED":
                    cur["to"] = t
                    cur["ending"] = d["state_reason"].split(":")[0]
                    cur["reason"] = d["state_reason"]
                    cur = None
                    continue
                cur["revisions"].append({
                    "t": t, "state": d["state"], "basis": d["basis"],
                    "valid_from": self.stamp_rel(d["valid_from"])
                    if d["has_valid_from"] else None})
            out.append({
                "id": cid, "name": names.get((asserter, subject), cid),
                "asserter": asserter, "subject": first["subject_ref"],
                "predicate": first["predicate"], "object": first["object_ref"],
                "lane": REL_TOPIC, "segments": segs,
                "disposed_with": disposes.get(cid, []),
                "records": [{"t": self.stamp_rel(d["stamp"]), "record": d}
                            for d in samples]})
        out.sort(key=lambda c: c["name"])
        return out

    # -- the narration --------------------------------------------------------

    def events(self, claims: List[dict], gate: List[dict], sight: List[dict]) -> List[dict]:
        ev: List[dict] = []
        s = self.s

        def rec(c: dict, i: int) -> dict:
            d = c["records"][i]["record"]
            return {"record": {"lane": c["lane"], "key": d["id"],
                               "stamp": d["stamp"]}}

        def add(t, text, sources, who=None):
            if self.t0 - 1e-9 <= t + self.t0 <= self.t1 + 1e-9:
                ev.append({"t": r2(t), "text": text, "who": who,
                           "sources": sources})

        cl = {c["name"]: c for c in claims}
        w = teeth.crosswalk_arm(s)
        g = teeth.gate_arm(s)

        c2 = cl["C2"]
        add(c2["records"][0]["t"], "mmwave-ut-04 observes bus-3 within the stop "
            "zone [observed]", [rec(c2, 0)], "bus-3")
        if sight:
            add(sight[0]["from"], f"car-a1 cannot see ped-17: {sight[0]['blocked_by']} "
                "blocks its line of sight", [{"truth": "the visibility model over "
                                              "the simulated world"}], "car-a1")
        c1 = cl["C1"]
        r0 = c1["records"][0]
        vf = r0["record"]["valid_from"]
        decided = float({kv["key"]: kv["value"] for kv in r0["record"]["meta"]}["demo.decided_t"])
        ahead = secs(vf) - s.epoch - decided
        add(r0["t"], f"mmwave-ut-04 predicts ped-17 within the crosswalk "
            f"[derived, valid from {ahead:.2f} s ahead]", [rec(c1, 0)], "ped-17")
        kerb = [i for i, x in enumerate(c1["records"])
                if "kerb" in {kv["key"]: kv["value"] for kv in x["record"]["meta"]}.get("demo.how", "")]
        if kerb:
            add(c1["records"][kerb[0]]["t"], f"ped-17 waits at the kerb: the unit "
                f"re-predicts every second ({len(kerb)} revisions while it waits)",
                [rec(c1, i) for i in kerb], "ped-17")
        hold = w["stop_initiated"]
        if hold:
            add(self.rel(hold["applied_at"]), "car-a1 begins to stop for a pedestrian "
                "it cannot see, on mmwave-ut-04's claim", [
                    {"truth": f"the world applied car-a1's controller's cap "
                              f"{hold['cap']} m/s at t={hold['applied_at']}"},
                    rec(c1, 0)], "car-a1")
        obs = next(i for i, x in enumerate(c1["records"])
                   if x["record"]["basis"] == "OBSERVED")
        add(c1["records"][obs]["t"], "ped-17 steps in: the prediction is revised "
            "to observed, on the same edge id", [rec(c1, obs - 1), rec(c1, obs)], "ped-17")
        seen = next(((s.t_of(d["stamp"]), d) for _t, d in s.entities
                     if d["entity_id"] == "ent:operator-a:ped-17" and "car-a1" in
                     {kv["key"]: kv["value"] for kv in d["properties"]}
                     .get("demo.seen_by", "").split(",")), None)
        if seen:
            add(self.rel(seen[0]), "car-a1's own sensors see ped-17 now; it is "
                "already stopping", [{"record": {"lane": ENTITY_TOPIC,
                                                 "key": seen[1]["entity_id"],
                                                 "stamp": seen[1]["stamp"]}}], "car-a1")
        if w["stopped"] is not None:
            add(self.rel(w["stopped"]), f"car-a1 stopped, {abs(w['stopped_front_past_crosswalk_edge_m']):.1f} m "
                f"short of the crosswalk; ped-17 never closer than {w['min_distance_m']:.1f} m",
                [{"truth": "car-a1's position and speed in the simulated world"}], "car-a1")
        c3 = cl["C3"]
        d3 = c3["records"][0]["record"]
        lead = secs(d3["valid_from"]) - secs(d3["stamp"])
        add(c3["records"][0]["t"], f"gate-ctl-9 declares: gate-9 closes the "
            f"crossing zone from {self.stamp_rel(d3['valid_from']) + self.t0:.1f} s "
            f"[declared, {lead:.1f} s ahead; an intent, not a prediction]",
            [rec(c3, 0)], "gate-9")
        c5 = cl["C5"]
        add(c5["records"][0]["t"], "the fuser derives: crossing zone blocked by "
            "gate-9 [derived from the declaration it read]", [rec(c5, 0)], "crossing-zone")
        b2 = g["car-b2"]["stop_initiated"]
        first_move = next(x["t"] for x in gate if x["state"] == "warning")
        if b2:
            add(self.rel(b2["applied_at"]), f"car-b2 begins to stop on the declared "
                f"future, {first_move + self.t0 - b2['applied_at']:.1f} s before the "
                "arms move", [{"truth": f"the world applied car-b2's controller's cap "
                                        f"{b2['cap']} m/s at t={b2['applied_at']}"},
                              rec(c3, 0)], "car-b2")
        add(c3["records"][0]["t"] + 0.6, "car-a1 sits this one out: it is still "
            "held at the crosswalk for ped-17 when the gate declares",
            [rec(c3, 0), rec(c1, len(c1["records"]) - 1)], "car-a1")
        ret1 = len(c1["records"]) - 1
        add(c1["records"][ret1]["t"], "ped-17 clears the crosswalk: the claim "
            f"retires, “{c1['records'][ret1]['record']['state_reason']}”",
            [rec(c1, ret1)], "ped-17")
        a1g = g["car-a1"]["stop_initiated"]
        if a1g:
            add(self.rel(a1g["applied_at"]), "car-a1, released, now stops for the "
                "gate on gate-ctl-9's claim, after the arms are already down",
                [{"truth": f"the world applied car-a1's controller's cap "
                           f"{a1g['cap']} m/s at t={a1g['applied_at']}"},
                 rec(c3, 0)], "car-a1")
        for x in gate:
            if x["state"] in ("warning", "closed", "open"):
                text = {"warning": "gate-9 warning lights: the first physical move",
                        "closed": "gate-9 arms down",
                        "open": "gate-9 arms up"}[x["state"]]
                add(x["t"], text, [{"record": {"lane": ENTITY_TOPIC,
                                               "key": x["record"]["entity_id"],
                                               "stamp": x["record"]["stamp"]}}],
                    "gate-9")
        natives = [h for h in g["native_heads"] if h["native"]]
        if natives:
            first = min(natives, key=lambda h: h["braking_began_truth"])
            add(self.rel(first["braking_began_truth"]), f"native traffic brakes only "
                f"at the physical gate ({first['vehicle']}, simulator-driven, "
                "reads no claims)", [{"truth": f"{first['vehicle']}'s speed in the "
                                               "simulated world"}], first["vehicle"])
        c4 = cl["C4"]
        add(c4["records"][0]["t"], "mmwave-ut-04 predicts train-7 in the crossing "
            "zone [derived]", [rec(c4, 0)], "train-7")
        o4 = next(i for i, x in enumerate(c4["records"])
                  if x["record"]["basis"] == "OBSERVED")
        add(c4["records"][o4]["t"], "train-7 arrives: revised to observed",
            [rec(c4, o4)], "train-7")
        add(c3["records"][-1]["t"], "gate-ctl-9 retires its declaration: "
            f"“{c3['records'][-1]['record']['state_reason']}”; C5 retires with it",
            [rec(c3, len(c3["records"]) - 1), rec(c5, len(c5["records"]) - 1)], "gate-9")
        cross = [self.rel(t) for k, t in teeth_events(s).items()
                 if k in ("car-a1 crossing-zone enter", "car-b2 crossing-zone enter")]
        if cross:
            add(min(cross), "both protagonists cross; traffic drains",
                [{"truth": "the protagonists' positions in the simulated world"}],
                "car-a1")
        ev.sort(key=lambda e: (e["t"], e["text"]))
        return ev

    def asset(self) -> dict:
        claims = self.claims()
        gate = self.gate()
        sight = self.sight()
        return {"schema": SCHEMA, "meta": self.meta(), "map": self.map(),
                "tracks": self.tracks(), "gate": gate, "sight": sight,
                "claims": claims, "events": self.events(claims, gate, sight)}


def teeth_events(s: Session) -> Dict[str, float]:
    from scenarios.crossing import events
    return events.extract(s.truth_path)


def render(asset: dict) -> str:
    return json.dumps(asset, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", type=Path, default=ASSET)
    args = ap.parse_args()
    text = render(Export().asset())
    size = len(text.encode())
    if args.check:
        same = args.out.exists() and args.out.read_text() == text
        print(f"export_replay: {args.out.name} {'matches' if same else 'is stale against'} "
              f"the recording ({size} bytes)")
        return 0 if same else 1
    if size > CEILING_BYTES:
        print(f"export_replay: {size} bytes, over the {CEILING_BYTES} ceiling")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"export_replay: wrote {args.out} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
