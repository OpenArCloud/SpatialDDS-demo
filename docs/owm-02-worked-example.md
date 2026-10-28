# OWM 0.2 relationships: a worked example

Real records from one captured session of the Open World Model demo, each
with a paragraph on what it shows. The point is to feed the 0.2 design
document before SCWG reviews it: what the Relationship shape does when a
running venue uses it, and where the shape needed changing. Section 3
lists the proposed edits, each with the measurement it came from.

The type is `oarc.relationship`, a demo-owned extension in the 0.2
design's shape (`idl/demo18/oarc_owm02.idl`). It is not spec text, and the
spec is unchanged. The census and the ruling behind every choice here are
in [`owm-02-census.md`](owm-02-census.md).

**The session.** `recordings/owm-02-lifecycle.mcap`, 540 s on the stack as
it ships: `spatialdds[dds,server]==0.7.3`, `spatialdds_ros==0.4.1` on the
nav2 tier, and every asserter running. `tools/capture_owm02_lifecycle.sh`
drove it:
1. robot north into the fountain;
2. back to the start;
3. north again;
4. SIGKILL one zone watch;
5. SIGTERM the other;
6. restart both;
7. kill the sim.

Every number below comes from `tools/owm02_film_report.py`, which reads only
that file and its provenance. Records are quoted as the gateway serves them:
decoded by its own XCDR2 reader against the IDL the recording embeds, then
projected to N.1. Times are seconds from the start of the recording.

To reproduce, with the stack and tier up (README §2):

```sh
bash tools/capture_owm02_lifecycle.sh
python3 tools/owm02_film_report.py recordings/owm-02-lifecycle.mcap
```

## 1. The records

### 1.1 Declared: the pond is part of the fountain

```json
{"id": "rel:svc:model:demo.venue:161b39af985a5167",
 "subject_ref": "ent:pond:littlefield",
 "predicate": "http://www.wikidata.org/prop/direct/P361",
 "object_ref": "ent:fountain:littlefield",
 "basis": "DECLARED", "asserter": "svc:model:demo/venue",
 "derived_from": [], "has_valid_from": false, "has_valid_until": false,
 "meta": [], "state": "CREATED", "state_reason": ""}
```

The venue's own composition, asserted by the service that declared both
ends. It uses part-of rather than containment. The two boxes do nest, but
an edge saying so would tell a client only what it can compute from the
extents; "the pond is part of the fountain" says something no box carries.
With no validity set, it means "while asserted". The venue itself is not
an entity, so nothing claims "the venue contains the pond": edges point at
entities, and none was minted to decorate one. If the pond is retired, this
edge is cascaded in front of its tombstone (`tests/test_claims.py`,
`DeclaredEdges`). The cascade had been empty by construction since the 1.8
migration retired the demo's old Relationship type.

### 1.2 Declared, as enforced policy: each duck within the shallows

`ent:duck:east sfWithin ent:shallows:littlefield`, asserted by
`svc:mover:demo/ducks`, basis DECLARED, with meta
`oarc.claim.enforced_by = "duck_mover's clamp into
ent:shallows:littlefield's extent, inset 1 m"`.

This asserter is not the venue but duck_mover. The ducks stay in the
shallows only because duck_mover clamps them there; run it with
`--bounds declared` and they leave. The party that makes a claim true is
its honest asserter, and the edge's object follows that configuration. The
basis is DECLARED because the claim states a rule duck_mover applies: it
is never decided by reading where a duck is. It is also why the edge is
worth publishing when the geometry is already on the bus. A snapshot says
where a duck is; this says where a duck will be kept.

### 1.3 Observed and bitemporal: the robot within the fountain

At 49.04 s, from the zone watch reading the robot's fast lane:

```json
{"id": "rel:svc:claims:demo.zone-fast:ac4ade1f9b2a595f",
 "subject_ref": "ent:robot:tb3",
 "predicate": "http://www.opengis.net/ont/geosparql#sfWithin",
 "object_ref": "ent:fountain:littlefield",
 "basis": "OBSERVED", "asserter": "svc:claims:demo/zone-fast",
 "derived_from": [
  {"topic": "spatialdds/ut-austin-littlefield/world/model_pose/v1",
   "key": "ent:robot:tb3",
   "stamp": {"sec": "1792985438", "nanosec": 394633769}},
  {"topic": "spatialdds/ut-austin-littlefield/world/entity/v1",
   "key": "ent:fountain:littlefield",
   "stamp": {"sec": "1792984714", "nanosec": 13921976}}],
 "stamp": {"sec": "1792985438", "nanosec": 480239872},
 "has_valid_from": true,
 "valid_from": {"sec": "1792985438", "nanosec": 394633769},
 "has_valid_until": false,
 "meta": [{"key": "oarc.claim.evidence", "value": "fast"},
          {"key": "oarc.claim.method",
           "value": "point in the object's published Entity.extent (Aabb3)"}],
 "state": "CREATED", "state_reason": ""}
```

Two times, and they are different times. `valid_from` is when the evidence
showed the robot inside: the stamp of the pose record that first did.
`stamp` is when the claim was made, 86 ms later. The record names that pose
in `derived_from`, along with the fountain record whose extent it was tested
against.

**The claim is about the model, not the unmeasured world.** It is computed
against the fountain's published extent, a box the seeder calls "coarse on
purpose". So it says something about the model, which is what every claim
on this bus does. The coarseness becomes visible the moment an edge depends
on it (§1.5), so the claims model makes model quality inspectable.
`keepout.py` records the same gap from the costmap's side: an `Aabb3` is a
locator or a boundary, and nothing on the wire says which.

At 92.70 s it retires:
- `valid_until` …482.093517780, the stamp of the first pose showing the
  robot outside;
- `state_reason` "no longer holds: ent:robot:tb3 left
  ent:fountain:littlefield's extent";
- then its dispose, carrying the same reason.

### 1.4 Two asserters, one proposition, neither wrong

Two zone watches assert the same proposition, the robot within the
fountain. One reads the robot's latched record and the other its fast
lane, so they are two edges with two ids. For each of the six entries in
the session:

| entry | fast `valid_from` | latched `valid_from` | latched − fast |
| --- | --- | --- | --- |
| 20.92 s | …410.293925523 | …410.293925523 | +0.000 s |
| 49.04 s | …438.394633769 | …438.794256210 | +0.400 s |
| 119.09 s | …508.493075132 | …508.793424606 | +0.300 s |
| 147.41 s | …536.793594598 | …536.793594598 | +0.000 s |
| 175.54 s | …564.892996311 | …565.293807983 | +0.401 s |
| 215.70 s | …605.193420886 | …604.797348260 | −0.396 s |

Each is right about its own evidence. The latched record trails the fast
lane by up to its republish interval; when both read the same instant the
times agree. In the last row the latched watch leads, because it was
restarted and read the latched record first. Nothing on the bus crowns one
claim, and nothing should: the panel shows both rows and filters by
asserter (`web/tests/claims-stack.spec.ts`).

### 1.5 The coarse box, made visible

At 20.92 s both edges are born at (25.22, −21.46), and at 22.23 s they
retire at (25.84, −20.90), 1.3 s later and before the robot has gone
anywhere notable. nav2 rounds the pond's east end along x ≈ 25.8, and the
fountain box's east edge is x = 25.81. Two more such pairs follow at 119
and 147 s, at the same corner. So the edge flickers where the robot's
route grazes the box.

This is §1.3's point, measured. A box drawn round a basin "on purpose
coarse" says the robot was in the fountain while it walked the plaza beside
it. Until something depended on the box, that was invisible. Edges make it
inspectable, and the fix belongs in the model (a footprint that is a
boundary), not in the claim.

### 1.6 Derived, with lineage, and with a consequence you can see

At 43.91 s:

```json
{"id": "rel:svc:claims:demo.near:2316487536395375",
 "subject_ref": "ent:robot:tb3",
 "predicate": "https://demo.spatialdds.org/owm/0.2/predicate/near#15m",
 "object_ref": "ent:duck:east",
 "basis": "DERIVED", "asserter": "svc:claims:demo/near",
 "derived_from": [
  {"topic": "spatialdds/ut-austin-littlefield/world/model_pose/v1",
   "key": "ent:robot:tb3",
   "stamp": {"sec": "1792985433", "nanosec": 293680429}},
  {"topic": "spatialdds/ut-austin-littlefield/world/model_pose/v1",
   "key": "ent:duck:east",
   "stamp": {"sec": "1792985433", "nanosec": 321656227}}],
 "valid_from": {"sec": "1792985433", "nanosec": 321656227},
 "meta": [{"key": "oarc.near.threshold_m", "value": "15"},
          {"key": "oarc.near.metric",
           "value": "geof:metricDistance in the venue plane (x, y), as duck_mover measures it"}],
 "state": "CREATED"}
```

**The lineage resolves.** Across the session, 140 of 140 `derived_from`
references stamped inside the recording's window resolve to a sample
actually in the file, by topic, key and stamp. Six more name samples from
before the window opened: three edges born before the recorder started
and replayed to it. `valid_from` is the later of the two records, because
before both existed nothing showed the claim. The references name pose
records and not entities, since the robot's latched entity is not
restamped on every pose: an entity id alone would not say which evidence
was read.

**This edge explains what you see.** The threshold is duck_mover's own
`AVOID_RADIUS_M`, imported rather than copied, the distance inside which
the ducks flee the robot. When the panel shows a near edge born, the ducks
start to swim away. They swim until they are 15 m off, which ends the edge:
at 44.11 s it retires, "no longer holds: 15.0 m from ent:duck:east, not
under 15 m". The fleeing stops, the ducks wander back in, and the edge is
born again. The near fuser made 73 such edges in 540 s, the most
consequential churn on the lane, and every one is the ducks' behaviour
written down. There is no hysteresis, because duck_mover's rule has none;
§3, E10, asks whether an edge should.

GeoSPARQL 1.1 has no nearness relation, only `geof:metricDistance`. So
`near` is the demo's own, on the demo's own domain, with the threshold in
the URI's fragment: near at 15 m and near at 6 m are two propositions.

### 1.7 Three endings, three reasons

Each retirement is a terminal sample, RETIRED with its reason, followed by
a dispose with the same reason. The three causes are told apart by the
reason's prefix, and each sets `valid_until` the way it promises:

| at | edge | `state_reason` | `valid_until` |
| --- | --- | --- | --- |
| 92.70 s | zone-fast | no longer holds: ent:robot:tb3 left ent:fountain:littlefield's extent | …482.093517780, the first pose outside |
| 214.33 s | zone-fast | asserter stopped: svc:claims:demo/zone-fast stopped cleanly | unset: the world was not consulted |
| 220.81 s | zone-fast | subject unobserved: ent:robot:tb3 is UNOBSERVED (no pose from svc:robot:demo/tb3 for 1.1s; …); it was last seen within ent:fountain:littlefield | …609.703, the last pose that showed it inside |

Across the session the lane carried 84 disposes:
- 79 no longer holds;
- 4 subject unobserved;
- 1 asserter stopped;
- none without a cause.

The third row is spatialdds_ros 0.4.1's fix (its finding 10) reaching the
claims record. Before it, a dead sim stayed ACTIVE, restamped forever at its
last pose, and this edge could never have ended this way. Here the sim was
killed at about 219 s, and 1.1 s later the robot was UNOBSERVED. That is
the robot's own learned silence threshold, quoted in the reason. Every edge
about the robot then retired, with `valid_until` as a lower bound on when
it stopped being true.

### 1.8 A killed asserter's edge stands

The latched zone watch held `rel:svc:claims:demo.zone-latched:3d9b48d10f04507c`
from 175.90 s, and was SIGKILLed at about 199 s (03:33:08Z, the driver's log). The recorder logged its
writer leaving at 03:33:17Z, and then nothing: no terminal sample, no
dispose. The next sample on that id is the restarted watch's CREATED at
215.70 s.

Claims inherit the transport's crash semantics. A killed asserter
withdraws nothing, because it cannot, and its edges stand with its
liveliness gone. A clean stop withdraws (§1.7). A reader has to be able to
tell the two apart, so the binding now passes writer liveliness on to the
browser. The panel marks the edge "asserter not alive"
(`tests/test_relationship_binding.py` kills a real asserter process under
the binding). Before this batch the pump dropped liveliness, and a killed
asserter's edge looked exactly like a live one.

## 2. How it was checked

**Payloads.**
- `tools/validate_served_owm.py` served the recording with
  `spatialdds.serve --mcap`. It validated 9,104 pinned-type payloads against
  the gateway's own published schemas, with 0 failures: 1,135 Entity,
  2,887 ModelCommand, 5,082 ModelPose.
- It could not validate the 176 relationship payloads, because the gateway
  publishes no schema for an extension, live or replayed (spatialdds-py
  finding 72, S19).
- So the film report checks them by two implementations agreeing. The
  gateway's XCDR2 decoder and N.1 projector, against the recording's
  embedded IDL, must read back through the SDK's strict `from_json` to
  exactly CycloneDDS's own deserialization, and `sd.dds.validate()` must
  pass. All 176 do. A plant that mutates one member of the comparison makes
  all 176 fail.

**The binding, live.** Against the running stack:
- `GET /ut-austin-littlefield/world/oarc.relationship/v1` answered 200,
  `Cache-Control: public, max-age=300`, `Access-Control-Allow-Origin: *`,
  with 7 edges.
- A WebSocket subscription replayed 7 with `end_of_replay`, then in 30 s
  carried 16 live samples and 4 disposes. Each dispose had its reason, for
  example "no longer holds: 15.2 m from ent:duck:west, not under 15 m".

**Tests.** Each has a planted fault, confirmed present before it counted.

| file | what it holds | plants caught |
| --- | --- | --- |
| `tests/test_claims.py` (13) | identity, revision, the three causes, SIGKILL vs clean stop in a real subprocess, both zone watches, near lineage, the declared cascade, duck_mover's edge | 7 |
| `tests/test_relationship_binding.py` (2) | the lane through `make_app` and the real pump: GETs, WS replay, dispose with reason, liveliness after SIGKILL | 4 |
| `web/tests/claims-store.spec.ts` (6) | the panel's store against a fixture the binding's own projection wrote | 2 |
| `web/tests/claims-stack.spec.ts` (2) | the panel, the filter, the robot driven in and out, the fountain lit and unlit | 1 |

**Suites.**
- The full tier: 13 passed, 0 failed. Its host suite: 631 passed and 20
  skipped, including this batch's 15 tests.
- Playwright, against the stack and the nav2 tier:
  - the stack project 14 of 14, in its normal order;
  - the isolated project 85 passed and 3 skipped, the same three as before
    the batch.

## 3. Proposed edits to the 0.2 design document

Each is filed against the design document, which is in the spec repo as a
proposal: [`proposals/owm-0.2-relationships/`](https://github.com/OpenArCloud/SpatialDDS-spec/tree/main/proposals/owm-0.2-relationships). E1, E2, E3 and E5's
causes were ruled and are made there; the rest are in its `edits.md`, open
for the working group. Each names its origin.

- **E1. Consumption: silence is not a contrary claim.** Verbatim, as ruled.
  Example: two near asserters at 6 m and at 15 m. Between 6 and 15 m one
  asserts and the other is silent; the silent one has not said "not near",
  and a consumer must not read it so. *Origin:* census §1.2.
- **E2. `derived_from` references evidence records, claims or entities, not
  claims only,** as (topic, key, stamp). *Origin:* §1.6, where the near
  fuser reads fast-lane poses, which an entity id cannot name.
- **E3. Open question six: parameterized predicates or identity-bearing
  qualifiers.** The prototype puts the threshold in the predicate's fragment
  (`near#15m`), and in meta for machine reading, so that proposition
  identity stays (subject, predicate, object). The working group decides,
  with this as the measurement. *Origin:* census §2.
- **E4. Name the lifecycle members as Entity names them, `state` and
  `state_reason`.** The SDK's `withdraw()` finds a terminal sample's members
  by those names. Spelled `lifecycle`/`reason`, as drafted, a withdrawal
  rewrites the edge as it last was, and its reason survives only in a
  recording's metadata. *Origin:* `Writer._terminal`, read before the type
  was written.
- **E5. Three retirement causes, distinguishable, each with its
  `valid_until` rule:**
  - no longer holds: the evidence that showed it;
  - subject unobserved: the last evidence that showed it true, a lower
    bound;
  - asserter stopped: unset.

  A killed asserter withdraws nothing, and its edges stand with liveliness
  lost; the document should say a consumer reads that as "nobody asserts
  this now", not as a retirement. *Origin:* §1.7 and §1.8.
- **E6. A cascade is per asserter.** Retiring an entity cascades the
  retiring service's own edges in front of its tombstone. Other asserters'
  edges about it dangle until each sees the withdrawal: a ghost window
  bounded only by their reading. *Origin:* model_service against duck_mover
  and the zone watches (§1.1).
- **E7. The basis of a computed geometric edge.** The robot within the
  fountain is computed from an observed pose and an observed extent. The
  prototype calls it OBSERVED, as ruled, but it is a computation over two
  observations. Does computing make an edge DERIVED? *Origin:* §1.3.
- **E8. Declared as enforced policy.** An edge whose truth its asserter
  maintains, rather than measures, is DECLARED, and is worth publishing
  because a snapshot cannot compute it. *Origin:* §1.2.
- **E9. A claim against a published extent is a claim about the model.**
  The edge makes the locator-versus-boundary gap visible (§1.5) and
  inherits it; cross-reference the module's footprint question. *Origin:*
  §1.3 and §1.5, and `keepout.py`.
- **E10. Hysteresis.** An edge on a threshold that a behaviour pushes
  things across churns at that threshold: 73 near edges in 540 s. Should a
  derived predicate carry hysteresis in its definition, or is churn the
  honest record of a behaviour that has none? *Origin:* §1.6.
- **E11. A reader needs liveliness, not only samples.** A binding that
  drops writer liveliness makes a killed asserter's edge indistinguishable
  from a live one. The web binding's event set already has `liveliness`,
  and the design should require that a relationship lane carry it.
  *Origin:* §1.8, where the demo's pump dropped it until this batch.

## 4. What else this found, and what was deferred

- **Filed against spatialdds-py, as finding 72:**
  - S18: `withdraw()` cannot set a type's other terminal members, so every
    retirement here writes two terminal samples;
  - S19: no schema is published for an extension;
  - S20: `serve --mcap` serves an extension lane volatile and
    operator-scoped;
  - two observations: registrations cannot be looked up, and `extend()`
    resolves includes from the pin's root.
- **Filed against spatialdds-ros, as its finding 12:** a goal sent while
  nav2 is still finishing the last one is a preemption, and nav2 reported
  the *new* goal succeeded at the old goal's position in the same tick. The
  robot never moved, and SUCCEEDED owes no refusal, so nothing reached the
  record. Measured from nav2's log; `claims-stack.spec.ts` and the film
  driver now wait for the robot to stand still before the next goal.
- **Seen on the robot tier, not filed:** a goal sent mid-route at the
  pond's south-east corner left the robot inside the keep-out's shoulder.
  From there nav2 abandoned every later goal, and each abandonment reached
  the robot's record as a refusal (spatialdds_ros 0.4.1), until the tier
  restarted. Seen twice while writing `claims-stack.spec.ts`.
- **Deferred, as the brief states:**
  - same-as, paired with the awareness adoption;
  - HTTP claim publishing;
  - any spec or module text;
  - SceneScape occupancy edges.
