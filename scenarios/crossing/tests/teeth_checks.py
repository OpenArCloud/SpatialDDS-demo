"""The teeth and car-a1's occlusion, asserted on a pair of sessions.

Shared by the live test (`test_crossing_session.py`, which runs the pair) and
the committed one (`test_crossing_recording.py`, which reads it), so the
recording the scene ships is held to exactly what a fresh run is. A class
using it sets `teeth` (`teeth.measure(full, silenced)`) and `session`
(`teeth.Session(full)`).
"""
from __future__ import annotations

import json
from pathlib import Path

from scenarios.crossing import visibility

REPO = Path(__file__).resolve().parents[3]


class TheTeeth:

    # -- perception -------------------------------------------------------------

    def test_car_a1_does_not_see_ped_17_through_the_bus(self):
        scenario = json.loads((REPO / "scenarios/crossing/scenario.json").read_text())
        sight = visibility.Sight(scenario["buildings"])
        h = scenario["operator_sensing"]["sensor_height_m"]
        s = self.session
        hidden = set()
        for fr in s.frames:
            a, ped = fr["actors"].get("car-a1"), fr["actors"].get("ped-17")
            if a and ped:
                occl = {k: v for k, v in fr["actors"].items() if v["kind"] in ("bus", "train")}
                if sight.visible((a["x"], a["y"], a["z"] + h), 60.0,
                                 {"ped-17": ped, **occl}).get("ped-17") == "bus-3":
                    hidden.add(round(fr["t"], 1))
        self.assertGreater(len(hidden), 10, "no occlusion in this session")
        named = []
        for _t, d in s.entities:
            if d["entity_id"] != "ent:operator-a:ped-17":
                continue
            seen_by = {kv["key"]: kv["value"] for kv in d["properties"]}.get(
                "demo.seen_by", "").split(",")
            t = round(s.t_of(d["stamp"]), 1)
            if "car-a1" in seen_by:
                named.append(t)
                self.assertNotIn(t, hidden)
        self.assertTrue(named, "car-a1 never saw ped-17: nothing was tested")

    # -- the teeth ----------------------------------------------------------------

    def test_tooth_1_the_unit_buys_car_a1_its_stop(self):
        t1 = self.teeth["tooth1"]
        w, wo = t1["with"], t1["without"]
        self.assertIn("pedestrian in the crosswalk", w["stop_initiated"]["why"])
        self.assertIn("own perception", wo["stop_initiated"]["why"])
        self.assertGreater(t1["initiated_earlier_by_s"], 2.0, t1)
        self.assertLess(w["stopped_front_past_crosswalk_edge_m"], 0.0, w)
        self.assertFalse(w["in_crosswalk_with_ped"])
        self.assertTrue(wo["in_crosswalk_with_ped"], wo)
        self.assertGreater(t1["min_distance_gain_m"], 2.0, t1)

    def test_tooth_1_the_silenced_units_claims_stand(self):
        s = self.teeth["tooth1"]["silenced"]
        self.assertTrue(s["claims_held"], s)
        self.assertEqual(s["claims_liveliness_lost_live"], s["claims_held"])
        self.assertEqual(s["claims_disposed_live"], [])
        self.assertEqual(s["records_disposed_recorded"], [])
        self.assertEqual(s["records_retired_recorded"], [])
        self.assertTrue(set(s["claims_held"]) <= set(s["records_writer_left_recorded"]))

    def test_tooth_2_the_declaration_comes_before_the_arms(self):
        t2 = self.teeth["tooth2"]
        arms = dict((state, t) for t, state in reversed(t2["arms_recorded"]))
        first_move = arms["warning"]
        b2 = t2["car-b2"]
        self.assertLess(t2["C3"]["decided_t"], first_move)
        self.assertLess(b2["stop_initiated"]["applied_at"], first_move - 2.0, t2)
        self.assertLess(b2["slowed_recorded"], first_move, t2)
        natives = [h for h in t2["native_heads"] if h["native"]]
        self.assertTrue(natives, t2)
        for h in natives:
            self.assertGreaterEqual(h["braking_began_truth"], first_move, h)

    def test_tooth_3_the_prediction_lives_and_ends(self):
        t3 = self.teeth["tooth3"]
        seq = [r for r in t3["sequence"] if "state" in r]
        self.assertEqual(seq[0]["state"], "CREATED")
        self.assertEqual(seq[0]["basis"], "DERIVED")
        self.assertGreater(seq[0]["valid_from"], seq[0]["decided_t"])
        observed = [r for r in seq if r["basis"] == "OBSERVED"]
        self.assertEqual(observed[0]["state"], "REVISED")
        self.assertEqual(t3["p2_before"]["id"], t3["p2_after"]["id"])
        self.assertEqual(seq[-1]["state"], "RETIRED")
        self.assertTrue(seq[-1]["reason"].startswith("no longer holds"))
        self.assertEqual(t3["sequence"][-1]["event"], "dispose")

    def test_tooth_4_c5s_lineage_resolves_to_c3(self):
        refs = self.teeth["tooth4"]["references"]
        self.assertTrue(refs)
        for r in refs:
            self.assertTrue(r["resolves"], r)
            self.assertTrue(r["resolves_to"].startswith("svc:crossing:gate-ctl-9 closes"), r)

    def test_claim_stamps_are_within_a_step_of_their_decision(self):
        """ClaimWriter stamps the wall clock (the clocks, docs/crossing-claims.md)."""
        c = self.teeth["clocks"]
        self.assertGreater(c["claims"], 10)
        self.assertGreaterEqual(c["min_ms"], 0)
        self.assertLess(c["max_ms"], 100)
