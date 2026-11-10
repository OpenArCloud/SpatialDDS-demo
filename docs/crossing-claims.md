# The crossing scene, Batch 2: claims, consumption, and the teeth

The second batch of the crossing scene (the census is
`docs/crossing-census.md`; the scene is `scenarios/crossing/`). The asserters
now make the ruled claims, two controllers drive the protagonists from the
bus alone, and four failable runs show what the claims buy and how they end.
Everything on the pinned SDK (spatialdds 0.7.4); every number below is
measured from the committed sessions in `scenarios/crossing/recording/`
unless it says otherwise.

## 1. What was built

| piece | module | reads | writes |
| --- | --- | --- | --- |
| operators' perception | `operator.py`, `tracks.py` | truth, through `visibility.py` from each fleet car | `ent:<operator>:<thing>`, OBSERVED, with `demo.seen_by` |
| the unit's claims C1, C2, C4 | `mmwave.py`, `zones.py` | its own published track poses | relationships, DERIVED then OBSERVED |
| the gate's claim C3 | `gate_ctl.py` | its own schedule | a relationship, DECLARED |
| the fuser, C5 | `fuser.py` | the relationship lane | a relationship, DERIVED from C3 |
| two controllers | `controller.py` | the bus only | speed caps to the world |
| a live subscriber | `watch.py` | the relationship lane | a JSON-lines log |
| the teeth | `teeth.py` | a session's files | the measurements below |

**Perception.** Each fleet car carries a sensor at its centre, 1.2 m up,
60 m of range, and sees what a straight sight line reaches past buildings,
buses and trains (`visibility.py`, unchanged from Batch 1). An operator
publishes one record per thing any of its cars sees, with `demo.seen_by`
naming which. The roadside unit's tracks and the operators' perceived
records end the same way (`tracks.py`): UNOBSERVED when unseen, with what
hides them, and withdrawn "track lost" after 2 s.

**The controllers see only the bus.** A controller's truth frame is its
tick, dropped unread (`Blind`; a test hands it a frame that fails on any
read). It places its car by its operator's record of it, trusts the claims
`TRUSTED` names by asserter and basis (C1 from mmwave-ut-04, DERIVED or
OBSERVED; C3 from gate-ctl-9, DECLARED), its own car's perception of
pedestrians near the crosswalk, and gate-9's declared state as a floor. For
each zone ahead it caps the speed at `sqrt(2 · 1.0 m/s² · d)` to a stop
1.5 m short; past that point it halts in a crosswalk and clears a crossing.

**What SUMO keeps.** The controllers set speed mode 39: SUMO keeps the gap
to the vehicle ahead and the car's acceleration and braking limits
(4.5 m/s², SUMO's passenger default), and gives up yielding, right of way
and signals. Bit 5 matters: without it SUMO brakes car-a1 for ped-17 from
its own ground truth, measured at 34.9 s, 0.4 s before the controller's
first command (§8, scene finding S2). A cap is lowered no faster than the
car's deceleration (`world.World._enforce_caps`): SUMO meets a sudden
maximum speed with emergency braking, measured at about 8 m/s² before this.

## 2. The claims, as built

| # | asserter | subject | predicate | object | basis | from, to |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | mmwave-ut-04 | `ent:mmwave-ut-04:ped-17` | `geo:sfWithin` | `ent:map:crosswalk` | DERIVED, revised to OBSERVED (P2) | predicted 27.3, in at 34.6, out at 45.2 |
| C2 | mmwave-ut-04 | `ent:mmwave-ut-04:bus-3` | `geo:sfWithin` | `ent:map:stop-zone` | OBSERVED | 20.9 to 53.9 |
| C3 | gate-ctl-9 | `ent:gate-ctl-9:gate-9` | `demo:closes` | `ent:map:crossing-zone` | DECLARED | declared 41.6 for 49.6, to 75.5 |
| C4 | mmwave-ut-04 | `ent:mmwave-ut-04:train-7` | **`geo:sfIntersects`** | `ent:map:crossing-zone` | DERIVED, revised to OBSERVED | predicted 59.0 for 63.8, out at 70.5 |
| C5 | fuser | `ent:map:crossing-zone` | `demo:blockedBy` | `ent:gate-ctl-9:gate-9` | DERIVED from C3 | from 49.6 (C3's), retired with C3 |

Every claim carries `demo.decided_t`, the simulation time it was decided
at (§7). C4 departs from the ruled inventory, for the reason in §8 finding
S1; ruled after Batch 2: approved.

## 3. The committed sessions

`scenarios/crossing/recording/` holds two real-time sessions, seed 42,
rate 1, run at once on two domains:

| | recording | samples (entity, pose, relationship, frames) | dispose records |
| --- | --- | --- | --- |
| `crossing` | 1,064,463 B | 2,798, 23,859, 23, 1 | 117 |
| `crossing-silenced`, mmwave-ut-04 SIGKILLed at t = 25 s | 789,718 B | 2,016, 17,195, 5, 1 | 81 |

Each with its truth log, its provenance and its live subscriber's log;
`MANIFEST.sha256` holds all eight files' digests and its own digest is
pinned in `tests/test_crossing_recording.py`. They replace Batch 1's single
session, which earned its verdict as Batch 1's evidence; these earned
theirs anew:

- **Every payload validated**, served by the pinned gateway
  (`python -m spatialdds.serve --mcap`) and read back by
  `tools/validate_served_owm.py`: 26,681 and 19,217, the recorders' whole
  counts, 0 failures, relationships included (23 and 5).
- **Conformance v1.8.3** against each: 15 checks, 14 passed, 0 failed,
  1 skipped (§8.2, no anchors).
- **Every participant kept up with the world**: each prints how long after
  the world sent a frame it took it up; the session test holds the 99th
  percentile under 0.1 s.
- **Determinism, measured, and where it ends.** Two full sessions on the
  committed code, compared (`python3 -m scenarios.crossing.events A.mcap
  B.mcap`): 481 events in both, none in one only. Identical: every claim
  sample (18 of 18) and each controller's first command per reason (8 of
  8); ground truth 20 of 21, one at 0.1 s. The world is identical to the
  step until the controllers act, and no longer: a cap takes effect on the
  step its bus input arrived in, which varies by one between runs
  (measured: the same caps, a step apart, from 28.0), so the world first
  differs at 30.4, car-a1 by a step, and the traffic behind it follows. By
  the session's end that reaches background tracks going out of view, up
  to 1.2 s apart (13 events beyond one step, all after 87 s: background
  cars' tracks and their withdrawals). The teeth do not move with it:
  across five full sessions (two run before C1's kerb wording, one meta
  string, was corrected) car-a1's claim-driven stop began at 30.3 to
  30.5 and kept 4.89 to 4.92 m from ped-17, car-b2's at 42.3 every time,
  and the native heads braked at 59.6 to 59.7 and 51.9.

The beats of the full session, from its truth log (and read back by the
recording test):

| s | event |
| --- | --- |
| 22.0 | bus-3 stops |
| 27.4 | ped-17 held at the kerb, ahead of the bus |
| 27.6 | car-a1's controller first acts on C1 |
| 34.0, 34.4 | ped-17 released; enters the crosswalk |
| 41.9 | car-b2's controller first acts on C3 |
| 45.4 | ped-17 leaves the crosswalk |
| 46.6, 49.6 | gate warning; arms down |
| 47.3 | car-a1 crosses the crosswalk |
| 63.8–70.5 | train-7 in the crossing zone |
| 72.5, 75.5 | arms rising; open |
| 76.8 | car-a1 and car-b2 cross, from the queue |

## 4. The teeth

Measured by `python3 -m scenarios.crossing.teeth crossing.mcap --silenced
crossing-silenced.mcap`, asserted on the committed pair and on a fresh pair
every run of the scene's suite (`tests/teeth_checks.py`).

### Tooth 1: silence the unit

| car-a1 | with mmwave-ut-04 | mmwave-ut-04 SIGKILLed at 25.0 |
| --- | --- | --- |
| stop initiated (the world applied the controller's first cap below its speed) | **30.4**, cap 12.05 at 12.13 m/s, "claim … (pedestrian in the crosswalk, DERIVED): stop 72.6 m ahead" | **35.3**, cap 4.84 at 12.19 m/s, "own perception: ent:operator-a:ped-17: stop 11.7 m ahead" |
| the recording's own poses show it 1 m/s slower | 31.5 | 35.7 |
| stopped | 41.3, front 1.4 m short of the crosswalk | 37.9, front 3.9 m into it |
| least distance to ped-17 on the approach | **4.90 m** | **1.25 m**, in the crosswalk with ped-17 |
| ped-17 off the crosswalk | 45.4 | 63.3 |

Initiation **4.9 s earlier** with the unit, and **3.65 m** more room. The
silenced car sees ped-17 only once past the bus (35.0, at x = −18.8 m,
20.2 m from ped-17, at 12.1 m/s) and cannot stop at 4.5 m/s²: it halts across ped-17's path, and
SUMO's pedestrian model keeps ped-17 in front of it, edging past, for
17.9 s more (and logs a contact as it does).

**The silenced unit's claim stands.** It held one claim when killed, C2
(`rel:svc:crossing:mmwave-ut-04:91fc9c9467085ee4`, bus-3 in the stop
zone). The live subscriber received its liveliness ending at 33.0, 8.0 s
after the kill, and **no dispose**; the recording holds no dispose and no
RETIRED sample for any of the unit's records, and lists all eight among
the writer departures (its seven entity records and C2).

### Tooth 2: the gate declares

Quoted from the recording (session seconds; a claim's stamp is the wall
clock, §7):

    C3   rel:svc:crossing:gate-ctl-9:47f96f34ebb85d80  CREATED DECLARED
         stamp 41.606  decided_t 41.6  valid_from 49.6
    gate-9 record (DECLARED)  demo.gate_state: warning 46.6, closed 49.6,
                                              opening 72.5, open 75.5

| | stop initiated | recorded 1 m/s slower | last at full speed (truth) | stopped |
| --- | --- | --- | --- | --- |
| **car-b2**, controller on C3 | **42.3** (cap 14.02 at 14.30 m/s) | **43.3** | 42.2 | 55.2 |
| bg-w.3, SUMO-native, head of westbound lane 1 | — | — | **51.9** | 54.9 |
| bg-e.0, SUMO-native, head of eastbound lane 1 | — | — | **59.7** | 62.3 |
| car-a1, controller on C3 | 53.3 | 54.4 | 53.5 | 64.3 |

car-b2 begins its stop **4.3 s before the arms first move** (46.6), on a
declared future; the native heads brake only after the arms are down. car-a1
does not show the tooth: when C3 is declared it is held at the crosswalk for
ped-17, and by the time C3 binds it (46.3) the arms are already moving, so it
is reported as measured rather than claimed for this tooth.

### Tooth 3: the prediction's lifecycle, from the live subscriber

C1, `rel:svc:crossing:mmwave-ut-04:dd530c4a474956c8`, as `watch.py` received
it (received, state, basis, `decided_t`, `valid_from`, confidence, how):

    27.33  CREATED  DERIVED   27.3  27.65  0.9  constant velocity
    27.71  REVISED  DERIVED   27.7  28.15  0.9  constant velocity
    27.83  REVISED  DERIVED   27.8  28.8   0.5  at the kerb
    28.83 … 33.82  six more   (one a second)    +1.0 s each, 0.5, at the kerb
    34.03  REVISED  DERIVED   34.0  36.75  0.9  constant velocity
    34.11  REVISED  DERIVED   34.1  35.2   0.9  constant velocity
    34.32  REVISED  DERIVED   34.3  34.6   0.9  constant velocity
    34.62  REVISED  OBSERVED  34.6  34.6   —    observed by the unit's own track
    45.24  RETIRED  OBSERVED        valid_until 45.2
           "no longer holds: ped-17 is no longer in crosswalk"
    45.24  dispose  (same reason)

Every sample names the same `derived_from`: the unit's two pose records of
ped-17 at 26.8 and 27.3. **P2, the revised edge whole** (stamps are epoch
1793498681.653648 + session seconds), before:

```json
{
  "id": "rel:svc:crossing:mmwave-ut-04:dd530c4a474956c8",
  "subject_ref": "ent:mmwave-ut-04:ped-17",
  "predicate": "http://www.opengis.net/ont/geosparql#sfWithin",
  "object_ref": "ent:map:crosswalk",
  "basis": "DERIVED",
  "asserter": "svc:crossing:mmwave-ut-04",
  "has_confidence": true,
  "confidence": 0.9,
  "derived_from": [
    {"topic": "spatialdds/crossing/world/model_pose/v1",
     "key": "ent:mmwave-ut-04:ped-17",
     "stamp": {"sec": "1793498708", "nanosec": 453647872}},
    {"topic": "spatialdds/crossing/world/model_pose/v1",
     "key": "ent:mmwave-ut-04:ped-17",
     "stamp": {"sec": "1793498708", "nanosec": 953647872}}
  ],
  "stamp": {"sec": "1793498715", "nanosec": 959481088},
  "has_valid_from": true,
  "valid_from": {"sec": "1793498716", "nanosec": 253647872},
  "has_valid_until": false,
  "valid_until": {"sec": "0", "nanosec": 0},
  "meta": [
    {"key": "demo.how",
     "value": "constant velocity over the track's last 0.5 s, heading held"},
    {"key": "demo.decided_t", "value": "34.3"}
  ],
  "state": "REVISED",
  "state_reason": ""
}
```

and after:

```json
{
  "id": "rel:svc:crossing:mmwave-ut-04:dd530c4a474956c8",
  "subject_ref": "ent:mmwave-ut-04:ped-17",
  "predicate": "http://www.opengis.net/ont/geosparql#sfWithin",
  "object_ref": "ent:map:crosswalk",
  "basis": "OBSERVED",
  "asserter": "svc:crossing:mmwave-ut-04",
  "has_confidence": false,
  "confidence": 0.0,
  "derived_from": [
    {"topic": "spatialdds/crossing/world/model_pose/v1",
     "key": "ent:mmwave-ut-04:ped-17",
     "stamp": {"sec": "1793498708", "nanosec": 453647872}},
    {"topic": "spatialdds/crossing/world/model_pose/v1",
     "key": "ent:mmwave-ut-04:ped-17",
     "stamp": {"sec": "1793498708", "nanosec": 953647872}}
  ],
  "stamp": {"sec": "1793498716", "nanosec": 259068928},
  "has_valid_from": true,
  "valid_from": {"sec": "1793498716", "nanosec": 253647872},
  "has_valid_until": false,
  "valid_until": {"sec": "0", "nanosec": 0},
  "meta": [
    {"key": "demo.how", "value": "observed by the unit's own track"},
    {"key": "demo.decided_t", "value": "34.6"}
  ],
  "state": "REVISED",
  "state_reason": ""
}
```

Same id, same proposition; basis, confidence, `valid_from`'s meaning, meta
and stamp changed; **`derived_from` did not**: the observation names the
poses at 26.8 and 27.3, 7.3 s before ped-17 was in the crosswalk, and no
record that shows it there (the pose at 34.6 that did is not named).

### Tooth 4: C5's lineage resolves

C5 (`crossing-zone demo:blockedBy gate-9`) was asserted at 41.706 and
retired at 75.606; both samples carry one reference, and it resolves in the
recording to the record the fuser read:

    ref  spatialdds/crossing/world/oarc.relationship/v1
         rel:svc:crossing:gate-ctl-9:47f96f34ebb85d80  stamp 41.606
      -> svc:crossing:gate-ctl-9 closes, CREATED, DECLARED

Its `valid_from` is C3's, 49.6; its `valid_until`, 75.5, is C3's.

## 5. The probes

**P1, future `valid_from`.** C1 and C3 ride the same mechanics, and a
consumer tells a prediction from an intent by basis alone (DERIVED against
DECLARED), which was enough for these controllers: they treat both alike, a
held claim about the zone ahead is a reason to be able to stop there. What
basis does not carry is how a prediction's time is known. A pedestrian
standing at the kerb has no honest single `valid_from`: the unit revised C1
**seven times in 6.6 s**, each pushing `valid_from` a second on as the last
one passed unconfirmed, and a prediction held past its own `valid_from`
asserts in the present tense something the asserter can see is false.

**P2, a prediction that comes true.** Revised in place as ruled; the cost
the census named is now measured, in the payloads above: the OBSERVED edge
keeps the prediction's lineage.

**P3, "will enter".** `geo:sfWithin` with a future `valid_from` carried both
predictions, and both controllers read them as intended. Where it broke was
not futurity but geometry: §8 finding S1.

## 6. Findings, as proposed 0.2 edits

Each in the form of the working group's register
(`proposals/owm-0.2-relationships/edits.md` in the spec repository, which
this repository does not edit), with its measured origin.

**E1. Say what `derived_from` means on a revision that changes basis.**
*Origin:* §4 tooth 3, P2. A DERIVED edge revised to OBSERVED keeps the
references that established the prediction, 7.3 s old, and names nothing
that observed the fact. The demo's writer does this by rule
(`spatialdds_demo/claims.py`, "the revision is of the opinion, not of the
lineage"), and 0.1's text does not say otherwise. *Proposed:* either a
basis change starts a new lineage (`derived_from` names the evidence for the
new basis, and an OBSERVED edge names what observed it), or an OBSERVED edge
carries no `derived_from` and the prediction's lineage is kept by reference
to the superseded revision. Either is a sentence in the relationship
section; today a reader cannot tell which records showed the fact.

**E2. A prediction needs a window, not an instant.** *Origin:* §5 P1. A
waiting pedestrian forced seven revisions in 6.6 s, each a re-statement of
"not yet", and every one a moment when a held claim's `valid_from` had
passed unconfirmed. *Proposed:* say what a held claim means once its
`valid_from` passes unconfirmed (still a prediction, or a present-tense
assertion), or give a prediction an earliest and an expected time; without
one of them an honest asserter must revise on a clock or retire.

**E3. An oriented extent** — filed in Batch 1 (census §4), unchanged.

**Not proposed, recorded:** the gate controller knows at 70.5 that the zone
opens at 75.5, and could state it as `valid_until` on its live edge. The
0.2 prototype type has the field (`has_valid_until` on a live edge); the
demo's shared writer sets it only when retiring. That is a demo writer
limit, not a spec question.

## 7. The clocks

`claims.ClaimWriter` stamps a claim with the host's wall clock
(`now_time()`); every other record of the scene is stamped on the
simulation's (epoch + t), and `valid_from` is set on it. At rate 1 the two
agree to within the asserter's processing time, and every claim carries
`demo.decided_t` so that is measured, not assumed: over the full session's
18 asserted samples, stamp less decision time is **1 to 28 ms, median
15 ms**. Every sub-second interval quoted above (C1's `valid_from` 0.35 s
ahead of its decision, C3's 8.0 s) is therefore computed on the simulation
clock from `decided_t`, not from the stamp. Terminal samples are stamped by
the SDK's `withdraw()`, also on the wall clock (finding 74).

## 8. Findings from this batch

**S1. `geo:sfWithin` is false for a train in a crossing zone (C4).** The
census approved "train-7 `geo:sfWithin` crossing-zone". A 120 m train in a
zone 12.8 m long is never within it: DE-9IM *within* needs every point of
the subject in the object (`zones.within`; the test
`test_the_train_is_never_within_the_crossing_zone`). Built as
`geo:sfIntersects`, which is true from contact to clearance (63.8 to 70.5).
**Ruled:** `sfIntersects` approved, and the census records why its list
changed. C1 and C2 stay `sfWithin`: a pedestrian and a bus do fit.

**S2. SUMO yields to pedestrians from its own ground truth unless told
not to.** Speed mode 7 (right of way and red lights off) still braked
car-a1 for ped-17 at 34.9, before its controller had seen the pedestrian;
the junction model treats a pedestrian on the crossing as a foe within the
intersection, which only bit 5 disregards (measured in-process, three
modes). The silenced arm was quietly protected by SUMO until mode 39.

**75. A validated `write()` reads every vendored schema from disk, per
sample** (filed: spatialdds-web `WEB-BINDING-FINDINGS.md` 75). 7.04 ms a
pose write validated, 0.01 ms with `validate=False`; `webjson.schemas()`
alone, 3.74 ms a call over 112 files. At about 40 writes a step the
operators fell 1.4 s behind the world, published records stamped on time
and 1.4 s stale, and car-b2, placed by them, stopped inside the closed
crossing; the train braked for it. The scene's pose lane was written with
`validate=False` (`scene.Publisher`), the recording validated end to end
instead, and the session test holds every participant within 0.1 s of the
world.

**75, closed.** spatialdds-py's `next-release` compiles each type's schema
once a process and removes `validate=False`: a validated pose write costs
0.12 ms, against 5.11 ms on that branch before the fix and 0.009 ms written
unvalidated (its `gates/write_validation_cost.py`, budget 1 ms). The pose lane
is validated now, as every write is, on the pin that carries the fix,
`spatialdds` 0.8.3. Measured with that SDK under the pinned SUMO: the scene's suite passes as
on 0.7.4 with the lane unvalidated (72 passed and 39 subtests, both ways); and
with the SDK's caches planted out, the session test fails, a participant
0.759 s behind the world and a claim's latency 109 ms, so the suite holds the
lane's cost.

**Freshness, the evidence.** What finding 75 did to a consumer, measured in
a development session before the mitigation (rate 1, seed 42; its logs were
not committed, the numbers are from them). car-b2's controller placed its
car by operator-b's record of it. From 48 s the record's stamp ran behind
the wall clock by 0.9 s, by 1.6 s at 54 s; every record was stamped with
the step it described, so nothing on the record said it was late. At 53.5
the controller computed "stop 2.9 m ahead" for a car whose front was then
2.7 m past its stop point: it believed the car 5.6 m back from where it
was. car-b2 entered the crossing zone at 53.1, with the arms down since
49.6, and stopped there, front at x = 122.3 inside the zone (118.5 to
124.7). train-7, SUMO-native, braked for it from 20 m/s to 16.0 and
entered the zone 1.4 s late (65.2 against 63.8). A record's stamp says
when the world was as described, not when the record was written; a
consumer acting on it needs to know how old it is when it arrives, and
nothing on the wire told it. That is the case for a refresh or freshness
contract on the fast lane, from the consumer's side.

**76. `take()` takes at most 256 samples while saying it takes every event
that has arrived** (filed, finding 76). Read, not hit: the busiest take
here was 43 events a tick. It was suspected for the lag above first, and
measured out.

## 9. Suites

- The existing host suite, verbatim as CONTRIBUTING runs it, in the base
  environment (spatialdds 0.7.4, no SUMO): **639 passed, 18 skipped**,
  CONTRIBUTING's count. Batch 2 changed no shared code; its one shared
  file is CONTRIBUTING's row for this suite.
- The scene's suite, `python3 -m pytest scenarios/crossing/tests` with the
  pinned SUMO: **63 passed in 132 s**, of which the pair of real-time
  sessions is about 110 s, run at once on domains 62 and 63; without SUMO,
  45 passed and 18 skipped in 7 s, each skip saying what to install. Its fault plants, each
  confirmed in the file before the run: the controller's basis filter
  removed, the controller reading its frame, the operators seeing through
  the bus, the fuser naming no lineage (each failed its test); and, in one
  live run, a SIGTERM for the SIGKILL and the controllers untrusting C3
  (failed teeth 1 and 2).
