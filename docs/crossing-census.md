# The 6G crossing scene: Batch 1 census

The first stop of the crossing demo: the world is built, running and
recorded, and these are the decisions the ruling settles before claims are
built on it. Each section ends in what was proposed and the ruling it took
(2026-10-31). Everything was measured on 2026-10-31 against the repository's pinned
`spatialdds[dds,server]==0.7.4`, with SUMO 1.28.0, at demo `fd39f45` plus
this batch.

## 1. Where it lives, and what it touches

`scenarios/crossing/`, a second scene beside the fountain, in the same era.

| file | what it is |
| --- | --- |
| `scenario.json` | the scene, its frame and nominal georeference, seed and step, choreography, fleets, sensing unit, gate timing, authored zones and buildings |
| `net/crossing.{nod,edg,con}.xml` | the street, hand-authored; `crossing.net.xml` built from them |
| `net/crossing.rou.xml` | the actors and their plans; the two beats are timed here |
| `build_net.py` | builds the network; `--check` fails if it is stale |
| `world.py` | **the seam**: SUMO behind plain data |
| `world_service.py` | the seam's process: SUMO paced to the wall clock, ground truth over TCP |
| `truth.py`, `participant.py` | a participant's feed, and the loop every participant runs |
| `scene.py` | names, lanes, frame, georeference, types, and `Publisher` |
| `visibility.py` | range and 2.5-D line of sight |
| `map_author.py`, `operator.py`, `mmwave.py`, `gate_ctl.py` | the asserters |
| `record.py` | the scene's recorder |
| `run_session.py` | one session, recorded; every process stopped by PID |
| `events.py` | the scene's events from truth and recording; two sessions compared |

**The fountain is untouched.** Shared modules are imported, never edited:
`spatialdds_demo.lanes` (profiles, `RELATIONSHIP`, `RELATIONSHIP_STREAM`,
`open_reader`, `schema_record`) and `spatialdds_demo.claims`
(`ASSERTER_STOPPED`; Batch 2 adds its `ClaimWriter`). One shared file changes,
additively: `tests/test_topic_registry.py` classifies the scene's four lanes.

**The recorder is the scene's own.** `tools/record_owm_session.py` is bound
to the fountain's scene and lanes, and generalizing it would edit the
fountain. `record.py` writes the same file from the same shared pieces
(§6 shows the gateway and the validator reading it back).

**Toolchain.** The repository's pins, unchanged. The scene adds
`scenarios/crossing/requirements.txt`: `eclipse-sumo`, `traci` and `sumolib`
at 1.28.0 (2026-10-08; wheels for macOS arm64 and manylinux x86_64/aarch64),
headless. They are new pins, not bumps, and nothing installs them into the
demo images.

**Proposed:** confirm `scenarios/crossing/`, and the scene's own recorder.
**Ruled:** approved.

**The session is committed** in `scenarios/crossing/recording/`, per the
exhibit precedent (`tests/cross_era/MANIFEST.sha256`): the recording, its
provenance and the world's truth log, with a `MANIFEST.sha256` whose own
digest is pinned in `scenarios/crossing/tests/test_crossing_recording.py`.
588,969 and 126,502 bytes, far under the ~20 MB ceiling the ruling set.

## 2. The map

Hand-authored, axis-aligned, in the scene frame `map/crossing` (metres,
ENU, declared): an east–west arterial (two lanes each way, sidewalks), a
signalless crosswalk at x = 0 with pedestrian priority, a bus stop whose bus
dwells with its nose 8 m short of it, and a north–south rail line meeting the
arterial at a SUMO `rail_crossing` at x = 120. Six buildings, with heights,
are authored for line of sight.

Axis-aligned is the reason not to take an OSM extract: every zone is then an
exact box, and the tests hold the authored zones to the built network (the
crosswalk zone is the crossing SUMO built, the crossing zone is the
junction, the bus stops inside the stop zone). An Austin extract would need
a bus stop just before a signalless crosswalk just before a level crossing,
and approximating zones in it.

**The georeference is nominal** (30.2672, −97.7431, 150 m, in
`scenario.json`, which says so): the street is schematic and claims no
surveyed place.

| | |
| --- | --- |
| seed | 42 (`--seed` moves the background: 14 vs 20 unaffiliated cars at 42 and 43) |
| step | 0.1 s; 100 s per session, paced 1:1 to the wall clock |
| operator-a | car-a1 (protagonist), car-a2, car-a3 |
| operator-b | car-b1, car-b2 (protagonist), car-b3 |
| unaffiliated | two seeded flows, 0.12 veh/s each way, 5–80 s; published by no one |
| ped-17 | walks to the crosswalk, waits at the kerb 27.4–34.0 s, crosses |
| bus-3 | dwells 22.0–52.0 s |
| train-7 | northbound, 120 m, 20 m/s, departs 40 s |

**ped-17's wait is held by the world service.** SUMO parks a person's stop
beside the sidewalk, 3 m from the kerb (measured: (−4.0, −10.9)), where the
bus hides it only from 15 m away. The wait is a speed hold on the
crosswalk's walking area instead, at (0.9, −6.9): the scenario's
choreography, in `scenario.json`.

**The protagonists** keep the lane they were inserted in (TraCI lane-change
mode 0) and take speed commands through the seam. In Batch 1 nobody commands
them, so they drive SUMO's own car-following; Batch 2's consumers take over.

**The gate controller owns the crossing.** The node is a `rail_crossing`, and
from the first step its road links are held in the state the controller last
commanded: detection at the track circuit (rail y = −450), warning lights at
detection + 5 s, arms down at + 8 s, arms up 2 s after the train clears, open
3 s later. Commands carry `at`, so the arms move on the declared step.

**Proposed:** hand-authored map; nominal georeference; seed 42; the fleet
split above. **Ruled:** approved; the nominal georeference, with the scenario
file saying so, is the honest form.

## 3. Lanes and ids

| lane | type | registry |
| --- | --- | --- |
| `spatialdds/crossing/world/entity/v1` | `model_entity` | registered row |
| `spatialdds/crossing/world/model_pose/v1` | `model_pose` | registered row |
| `spatialdds/crossing/world/oarc.relationship/v1` | `oarc.relationship` | demo-owned extension, unregistered |
| `spatialdds/crossing/frames/frame_transform/v1` | `frame_transform` | spec type, no registered name |

Classified in the topic gate, which reads them from `scene.py` by value; a
planted unclassified lane there fails it.

**Ids: one asserter, one record per thing, `ent:<asserter>:<thing>`.**
TRANSIENT_LOCAL history belongs to the writer that wrote it, so two
participants writing one key would be two records fighting over one
instance (the fountain's `ModelPublisher` docstring). ped-17 as the sensing
unit sees it is `ent:mmwave-ut-04:ped-17`; as operator-a would see it in
Batch 2, `ent:operator-a:ped-17`. Whether the two are one pedestrian is a
same-as question this scene does not ask.

| asserter (`svc:crossing:…`) | records | basis |
| --- | --- | --- |
| `map` | 8 zones, 6 buildings, the georeference | AUTHORED |
| `operator-a`, `operator-b` | their fleets | OBSERVED (each car's own positioning) |
| `mmwave-ut-04` | its tracks; itself | OBSERVED; DECLARED |
| `gate-ctl-9` | `gate-9`, with `demo.gate_state` | DECLARED |

Types are Wikidata items, each read from Wikidata on 2026-10-31: car Q1420,
bus Q5638, train Q870, pedestrian Q221488, building Q41176, bus stop
Q953806, level crossing Q171448, pedestrian crossing Q8010, lane Q3222002,
railway line Q728937, boom barrier Q852010, roadside unit Q140775115.

**The sensing unit is emulated and says so.** Its tracks are ground truth
filtered by range (160 m) and line of sight from a 6 m mount; no radio
model, no noise, no misses. Every track carries `demo.emulation` with that
sentence. A track out of sight goes UNOBSERVED, with what hides it, and is
withdrawn "track lost" after 2 s.

**Proposed:** the scene name `crossing-6g`, the lanes, the id rule.
**Ruled:** the scene is `crossing`, not `crossing-6g`: lane strings outlive
pitches, and the 6G framing belongs in docs and on the page, never on the
wire. Renamed (scene and frame, `map/crossing`) and re-recorded before
anything carried the strings further; every number here is the re-recorded
session's. The id rule is approved: publisher-namespaced ids are 0.1's rule,
and they set up same-as for later. A claim's subject and object name the
asserter's own entity records where it has them, and the map author's zone
ids otherwise.

## 4. Entity extent on the wire

**Native field, measured from both.** The spec (v1.8 at the SDK's pin
`4b5652c`): `Entity.extent` is an `Aabb3` behind `has_extent`
(`idl/v1.8/provisional/owm.idl:80-81`), which Appendix E calls "the
axis-aligned fallback" (`appendix-e.md:442`); area and region entities
"describe their footprint with `events::SpatialZone`", and `Entity` has no
field that can point at one. The SceneScape sidecar (`7cce702`): **absent** on
moving tracks (`has_extent=false`, `sidecar/owm.py:164-168`: a box built
from the track's size "would assert an orientation the producer never
states"); **native** on regions, as the fallback (`owm.py:248-253`), plus a
**meta convention**, `footprint_zone_id`, naming the region's `SpatialZone`
(`owm.py:112-128`, "an adapter convention, pending a module decision").

This scene publishes the native extent everywhere. Zones and buildings are
axis-aligned, so their box is exact and no `SpatialZone` is needed to be
truthful. Vehicles carry the box **enclosing** the footprint rotated by the
heading the simulator gives, which asserts no orientation (the heading is in
the pose) but is refreshed only with the latched record, about 1 Hz, while
the pose moves at 10 Hz.

**Proposed for ruling:** native on zones and buildings; on moving vehicles,
either keep the enclosing box (as now) or follow the sidecar, absent, with
length and width in properties. No `SpatialZone` lane.

**Ruled:** keep the native `Aabb3` on vehicles, honestly inflated as the box
enclosing the rotated footprint, and carry the oriented dimensions as
namespaced meta so a viewer can draw the true box. Built: every track carries
`demo.obb.length_m`, `demo.obb.width_m` and `demo.obb.height_m`, the box
centred on the pose and turned by its yaw (`scene.OBB_KEYS`), and the
whole-session test holds every track's `Aabb3` to enclosing it.

**Evidence for 1.9 / OWM 0.2: an oriented extent.** Filed here for the
working group's register (`proposals/owm-0.2-relationships/edits.md` in the
spec repository, which this repository does not edit). 0.1's `Entity` can
state a moving object's size only as an axis-aligned box in the scene
frame, which is either an over-claim of the space it occupies or nothing.
Two implementations met that and resolved it differently, both measured:
- the SceneScape sidecar leaves extent off moving tracks entirely
  (`sidecar/owm.py:164-168` at `7cce702`): "absent beats over-claimed";
- this scene publishes the inflated box and needs a meta workaround,
  `demo.obb.*`, for the true one (`scenarios/crossing/scene.py`), which a
  consumer can only find by reading this repository.
An oriented extent (dimensions in the entity's own frame, oriented by its
pose) would let both state what they know on the wire.

## 5. Claims for Batch 2

All on the relationship lane, through `claims.ClaimWriter` unchanged. As
built in Batch 2, with C4's predicate changed and held for ruling, in
`docs/crossing-claims.md` §2 and §8 (S1).

| # | asserter | subject | predicate | object | basis | validity |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | mmwave-ut-04 | `ent:mmwave-ut-04:ped-17` | `geo:sfWithin` | `ent:map:crosswalk` | DERIVED, confidence attached, `derived_from` its own track records | `valid_from` the predicted entry, ahead of `stamp` |
| C2 | mmwave-ut-04 | `ent:mmwave-ut-04:bus-3` | `geo:sfWithin` | `ent:map:stop-zone` | OBSERVED | from arrival; retired on departure |
| C3 | gate-ctl-9 | `ent:gate-ctl-9:gate-9` | `demo:closes` | `ent:map:crossing-zone` | DECLARED | `valid_from` the arms-down time, declared at detection, 8 s ahead |
| C4 | mmwave-ut-04 | `ent:mmwave-ut-04:train-7` | `geo:sfWithin` | `ent:map:crossing-zone` | DERIVED, then per P2 | `valid_from` the predicted arrival |
| C5 | fuser | `ent:map:crossing-zone` | `demo:blockedBy` | `ent:gate-ctl-9:gate-9` | DERIVED, `derived_from` C3's record | from C3's arms-down time; retired when C3 is |

**Ruled:** C1, C2 and C4 approved. `demo:closes` and `demo:blockedBy` are
demo-namespaced URIs (`https://demo.spatialdds.org/predicate/closes`,
`…/blockedBy`), explicitly **not** proposed spec vocabulary; the evidence
files. C5 must be truthful at every instant it is asserted.

**C5, the minimal truthful set (recorded):** one edge, *crossing-zone
blocked by gate-9*, derived from C3 alone, valid from the arms-down time C3
declares, retired "no longer holds" when C3 retires. The train is not its
object: before train-7 reaches the zone, "blocked by train-7" is false, and
the gate is what blocks it from the first instant. C4 is not folded into C5's
lineage later: `derived_from` names the records that *established* a claim,
and `ClaimWriter` keeps that lineage across revisions, so evidence arriving
after the edge is born would be a lineage revision, which is P2's question
and is measured there. No second train-naming edge is asserted: nothing in
Batch 2 consumes one.

## 6. The three probes

- **P1, future `valid_from`.** Build C1 and C3 on the same mechanics and
  measure what a consumer can tell apart. The proposal's two clocks already
  allow a future `valid_from`; what Batch 2 checks is whether basis alone
  (DERIVED against DECLARED) separates a prediction from an intent, and what a
  consumer does with a prediction whose `valid_from` passes unconfirmed.
- **P2, a prediction that comes true.** Proposed: **revise the same id**,
  DERIVED → OBSERVED, `valid_from` moved to the observed entry. The proposal's
  convention keys identity on (asserter, subject, predicate, object)
  ("revises the same edge id", `design-options.md:86` in the spec
  repository's `proposals/owm-0.2-relationships/`), basis is part of
  the opinion, and `ClaimWriter.hold` already revises on a basis change.
  The cost to measure: `ClaimWriter` keeps the first lineage on revision, so
  the OBSERVED revision would still name the prediction's evidence.
- **P3, "will enter".** Start from `geo:sfWithin` with a future `valid_from`
  (C1, C4); if a consumer cannot read it right, that is the finding.

**Ruled:** P1, P2 and P3 approved as proposed. On P2 the full payloads of the
revised edge, before and after, are recorded: whether `derived_from` should
survive a DERIVED-to-OBSERVED revision is the 0.2 evidence P2 exists for.

## 7. Measured in this batch

**Two real-time sessions, seed 42, compared**
(`python3 -m scenarios.crossing.events A.mcap B.mcap`): 197 events in both,
none in one only, largest difference 0.10 s against a stated tolerance of one
step (0.1 s). The 21 ground-truth events are identical to the step. Of the 176
on the bus, 171 are identical and 5 differ by 0.1 s, every one on the wall
clock: 3 disposes, which carry no stamp and are timed by the recorder's
arrival, and 2 terminal samples, which the pinned `withdraw()` stamps itself
(finding 1). In-process, the same
seed gives identical events and another seed moves the background
(`scenarios/crossing/tests/test_crossing_world.py`).

The beats, from ground truth:

| s | event |
| --- | --- |
| 22.0 | bus-3 stops |
| 27.4 | ped-17 held at the kerb, ahead of the bus |
| 31.8 | car-a1 within 60 m; ped-17 hidden by bus-3 |
| 34.0 | ped-17 released; 34.4 enters the crosswalk |
| 35.0 | car-a1, at x = −18.9 m and 11.3 m/s, first sees ped-17 |
| 41.0 | car-a1 crosses the crosswalk (SUMO's own yielding: no claims yet) |
| 45.4 | ped-17 leaves the crosswalk |
| 46.6 | gate warning (detected 41.6, declared schedule) |
| 49.6 | arms down |
| 52.0 | bus-3 leaves |
| 63.8–70.5 | train-7 in the crossing zone |
| 72.4 | queue at the gate reaches 9 |
| 72.5, 75.5 | arms rising; open |
| 76.3 | car-a1 and car-b2 cross, from the queue |

**The recorded session**, Batch 1's (`run_session.py`, rate 1, every
process exit 0): 13,857 samples (1,384 entity, 12,472 pose, 1 georeference,
0 relationship), 48 dispose records, 588,969 bytes. Committed in Batch 1;
Batch 2 replaced it with the pair in `docs/crossing-claims.md` §3, which
earned its own validation. The numbers here are as measured then.

**Its payloads**, served by the pinned gateway (`python -m spatialdds.serve
--mcap`) and read back by `tools/validate_served_owm.py`: 13,857 validated
against the endpoint's own published schemas (FrameTransform 1, Entity 1,384,
ModelPose 12,472), 0 failures, the recorder's whole count. The spec's
conformance suite at v1.8.3 against it: 15 checks, 14 passed, 0 failed,
1 skipped (§8.2, no anchors), as the fountain's model recording.

**Suites.** The existing host suite, verbatim as CONTRIBUTING runs it, in the
base environment (spatialdds 0.7.4, no SUMO): **639 passed, 18 skipped**,
CONTRIBUTING's count, with the topic gate's additive entries in it. The
scene's own suite (`python3 -m pytest scenarios/crossing/tests`, with the
pinned SUMO): 24 passed in 80 s, of which one unpaced whole session is about
65 s and the rest 15 s; without SUMO, 14 passed and 10 skipped, each skip
saying what to install. It lives in the scene's folder, not `tests/`, so the
existing suite's count is unchanged by it.

**The Docker suites, run once as ruled** (two shared files changed, so
"nothing they load changed" had to be measured): `scripts/run_tests.sh full`
on this tree, with the demo stack stopped for its duration and restarted
after, **13 suites passed, 0 failed** in 5 min 46 s: the host suite, ar_demo
protocol, web typecheck, IDL compile and protocol, the cross-era probe, the
deadline and head-of-line probes, ROS 2 round-trip, MCAP live record and
replay, web-bridge HTTP, the departure probes, MQTT tier 2 and ROS 2 all
tiers. That tier's own host step counts 634 passed, 27 skipped; the
pre-batch tree (`fd39f45`, in a worktree, same environment) counts the same,
634 and 27, with 18 fewer subtests, which are the topic gate's new
crossing entries.

## 8. Findings so far

1. **The pinned `withdraw()` stamps the wall clock and refuses a stamp.** An
   asserter on a simulation clock cannot stamp its own withdrawal; at rate 1
   the clocks agree, so the scene records at rate 1 and `events.py` reads a
   terminal sample on the wall clock. It crashed three of five participants
   on first contact, which no smaller test had reached; the whole-session
   test (`scenarios/crossing/tests/test_crossing_session.py`) is the one
   that does, and fails on the replanted fault. **Filed** to spatialdds-py as
   a candidate, `WEB-BINDING-FINDINGS.md` finding 74 in spatialdds-web: either
   accept the caller's stamp, or state the wall-clock contract where a caller
   meets it, instead of a `ValueError` from inside its loop.
2. **A SUMO person's stop is beside the sidewalk**, not at the kerb (§2).
3. **SUMO drops an out-of-order route entry with a warning.** Both background
   flows were silently absent behind `--no-warnings`, which is now off.
4. **A zone a few metres too wide put a waiting car on the tracks.** The
   first crossing zone (x 116–127) held car-b2 at its stop line "inside" for
   29 s; it is now the junction, and a test holds it there.
5. **The first beat timing hid ped-17 for 0.6 s.** car-a1 came into its 60 m
   range only as ped-17 stepped out; with the kerb release at 34 s the bus
   hides it for 3.2 s (31.8–35.0), and car-a1 first sees it from
   x = −18.9 m, 20.3 m from it, at 11.3 m/s, measured on ground truth.
   (Corrected in Batch 2: this said "from 18.9 m", which is the position.)

## 9. Not in this batch

Batch 3's sources, `crossing-look-test.html` and `owm-hero-prototype.html`,
are in Downloads (the look test also in the local-only `directions/`).
