# The robot reads the claims: spatialdds-ros's consuming half, on the tier

The claims batch (2026-11-08). `spatialdds-ros` was writable for it, which the
Oct 19 adoption batch was not; this repository is the consumer. The library's
half is reasoned in its own `docs/claims-census.md`; this is the consumer's
half, and the tier run that is the batch's tooth.

**The doctrine, from the crossing.** Claims are advisory: the costmap layer
biases the planner, the robot's own stack decides, and nothing here actuates a
robot because somebody else said so. Consumers filter by asserter and basis,
by explicit configuration (`docs/crossing-claims.md`, its controllers'
`TRUSTED`). Observed claims age out when unrefreshed, and a publisher crash is
never a retirement.

## 1. Two premises the brief was written on had moved

Both are recorded because the brief's staging assumed them, and the batch
built on what was actually there.

- **The library was at 0.4.2, released**, not "toward 0.4.0". The census pin
  the brief names, `e8e5ec2`, was 0.1.0, not 0.3.0. 0.5.0 is this batch's
  version, prepared on the `claims-consumer` branch and not tagged.
- **The blocker was cleared, and the adoption landed, on 2026-10-19.** The
  library made `content_refs` and `external_refs` configurable that day
  (`e7b897d`, Config fields defaulting empty, carried verbatim into the
  record, with tests both ways), and the swap landed in this repository as
  `d6413d7`. `requirements.txt` has pinned the library since, through 0.2.1,
  0.3.0, 0.4.1 and 0.4.2. What had not moved was `robot_bridge.py`'s header,
  which still said the swap was blocked; it is corrected in this batch.

So Stage A's first item and Stage B were verification, re-run and quoted
below, and the batch's building was the claims layer, perception publishing
and the tooth.

## 2. The re-pin: `e8e5ec2` (0.1.0) to `cb38ec2` (0.5.0, unreleased)

25 library commits. What moved, by release:

| release | what the robot lane gained |
|---|---|
| 0.2.0, `e7b897d` | `content_refs`, `external_refs`; the anchorless note reworded |
| 0.2.1, `aa52a74` | `take_samples` reads sample state (adoption finding 1); topics take default QoS (finding 2) |
| 0.3.0, `c029276` | a distribution on PyPI that installs without ROS |
| 0.4.0, `f36958b` | awareness, the pose floor, writers that do not dispose on exit |
| 0.4.1, `a85bfea` | TF age read honestly; nav2's answers reach the client (findings 10, 11) |
| 0.4.2, `13cca00` | a success far from the goal is a refusal (finding 12) |
| 0.5.0, `cb38ec2` | the claims layer, perception publishing, the relationship type (this batch) |

**Against the four-seam census (`docs/ros-adoption-census.md`), nothing is
invalidated, and three of its filings are closed.**

- Seam 1, the keep-out in no-hardware mode: holds. The demo still feeds
  `PlazaSim` and its mask to the adapter, and the library still owns no venue
  geometry. The claims layer does not change that: a claim is not a keep-out,
  and the two filters are separate (`robot_tier/nav2_params.yaml`).
- Seam 2, georeference ownership: holds, and its filing is closed. The
  library's anchorless note now says the adapter "makes no claim about where
  that frame sits on the earth. Another participant may own that claim"
  (`e7b897d`).
- Seam 3, `nav.status`: holds, and is stronger: 0.4.2 also refuses a success
  reported far from the goal.
- Seam 4, command coexistence: holds; `test_robot_lane.py`'s both-ways tests
  pass on 0.5.0 (§7).
- The blocker's other two filings are closed: the property-key convention is
  the library README's "Property keys" table (`label`, `nav.status`, "the
  convention"), and the anchorless wording above.
- Outside the four: the commit pin is back for this window
  (`requirements.txt`, and `tests/test_topic_registry.py` now checks a commit
  pin against the commit pip recorded); the two generated trees' gate now
  covers `oarc_owm`, the relationship type 0.5.0 vendors, against this
  repository's own (`tests/test_idl_trees_agree.py`); the domain is passed as
  before; the ROS tier runs the library's node, as ruled.

## 3. What the tier runs now

`robot_tier/_bringup.sh` starts `spatialdds_ros.robot_node` with claims on:

```
-p claims:=true
-p claims_trust:="['svc:hazard:plaza-cam OBSERVED']"
-p claims_shoulder_m:=0.5
```

and `nav2_params.yaml` gives the **global** costmap a second keep-out filter,
`claims_filter`, reading the node's `/spatialdds_robot/claims/filter_info`.
The keep-out stays lethal on both costmaps; a claim is 50 of 100 on the
global one only, painted as cost 127 of 254.

**Trusted is one camera, on OBSERVED only, and nothing in the running stack
is that camera.** The venue's own asserters are deliberately not trusted. In
a private run of the whole stack the layer saw up to 8 of their claims at
once (the near fuser's DERIVED `near#15m`, the duck mover's DECLARED
`sfWithin`, the venue's DECLARED part-of) and drew none, each logged "no
trust rule admits it". A duck near the pond is not a reason for a robot to
plan round it.

## 4. Stage B: the behaviour bar, verbatim, on 0.5.0

The census's own bar: kinematic robot, goto from the web, refusal reaching
the asker, duck reaction intact, through the library. Each was run on this
branch with `cb38ec2` overlaid on the images' installed library, on a private
stack (domain 73, host ports 18088 and 18089, its own tier) beside the
running one, which was not touched.

- **Kinematic robot.** `tools/capture_robot_session.sh` (domain 54),
  `spatialdds_ros 0.5.0` in its log: both legs driven, the second abandoned
  at (12.47, -21.25) before the shallows, exactly as the 0.3.0 capture did
  (`docs/ros-adoption-findings.md` §4, the stale header noted there).
- **Duck reaction.** `tools/measure_duck_reaction.py` on that capture: ducks
  sit **+1.51 m** closer to the corner furthest from the robot while it is
  inside the radius than while it is outside (214 against 156 observations).
  On 0.3.0 the same instrument gave +1.49 m.
- **Goto from the web.** `web/tests/robot-goto.spec.ts`, "a tap on the ground
  sends the robot there", against the private stack and its nav2 tier:
  "asked for (22, -24), arrived at (20.99, -23.89) — from (17.00, -23.00)".
  On 0.3.0: arrived at (20.97, -23.88).
- **Refusal reaching the asker.** End to end, through the binding: a `goto`
  into the pond posted to `model_command/v1` with the binding's token (202),
  and 22.3 s later the robot's served record carried `nav.status = "goal
  (14.00, -14.00) abandoned: nav2 could not reach it (no plan, or the
  controller gave up)"`. The page's half, `web/tests/nav-refusal.spec.ts`
  ("a refusal on the robot's record is reported, once"), passed.
- **The robot renders as a robot, not a pin**, the blocker's own test:
  `web/tests/robot-render.spec.ts`, "the TurtleBot draws from its content
  manifest", passed. The record it drew from, as the binding served it:
  `content_refs: ["catalog:dcef7c31-1d5f-54aa-9344-899912f9d34a"]`,
  `external_refs: [{"key": "wikidata", "value": "Q4810574"}]`, `label`
  TurtleBot, OBSERVED, ACTIVE, `svc:robot:demo/tb3`.

**Two harness defects, found running these and fixed.**
`robot-render.spec.ts` and `nav-refusal.spec.ts` both read the running venue
and sat in Playwright's `isolated` project, so they ran beside the stack
specs: both timed out waiting for the page when run beside robot-goto, and
both passed serially. They are in the serial `stack` project now
(`web/playwright.config.ts`). And the specs found their stack by `grep
dds_bridge | head -1` and `robot-captures.spec.ts` by the literal name
`robot_tier`, which it kills and restarts the sim inside: with two stacks up,
aim-by-position. `SPATIALDDS_STACK_CONTAINER` and `SPATIALDDS_TIER_CONTAINER`
now name them; unset, the old defaults stand. `localize-failure.spec.ts`
intercepted `localhost:8088` while the page used the built URL, so against
any other stack it was no longer hermetic; it intercepts `BRIDGE_URL` now.

## 5. Stage C: the tooth

`tools/claims_tooth.sh OUT` runs the demo's own tier image in a container of
its own (domain 71, its own network), with the library mounted from a
checkout. It starts the venue's model service, the tier as `_bringup.sh`
starts it, a lane recorder (`tools/claims_watch.py`), and then a scripted
camera (`tools/hazard_asserter.py`, the library's `ObstaclePublisher`): one
spill, 2.0 x 1.6 m at (24, -23), on the robot's eastbound route from its start
to (31, -23), and one OBSERVED claim, `ent:plaza-cam:spill-1 near#5m
ent:pond:littlefield`. True where it is placed (the spill's north edge is
2.2 m from the pond's declared box) and naming only the venue's own entities.
A plan is asked of nav2's planner in each phase (`robot_tier/_claims_probe.py`),
beside the mask the filter received and the global costmap's cost at the
spill. `tools/claims_teeth.py OUT` judges the recording.

The final run, on the committed library (`cb38ec2`, from `git archive`):

| phase | route | in the spill | mask | cost at the spill |
| --- | --- | --- | --- | --- |
| before, no hazard | 278 poses, 14.00 m, y -23.00 throughout | 41 poses | none | 0 |
| claimed, the camera's OBSERVED claim held | 264 poses, 15.04 m, north to y -21.55 | 0 | 760 cells at 50 | 127 |
| after-kill, the camera SIGKILLed | 278 poses, 14.00 m, y -23.00 | 41 | 0 cells | 0 |
| untrusted, the stranger's identical claim | 278 poses, 14.00 m, y -23.00 | 41 | 0 cells | 0 |

**Arm 1, the route.** Before:
`(17.0,-23.0) (19.0,-23.0) (21.0,-23.0) (23.0,-23.0) (25.0,-23.0) (27.0,-23.0) (29.0,-23.0) (31.0,-23.0)`.
While claimed:
`(17.0,-23.0) (19.0,-22.7) (21.0,-22.3) (22.9,-21.6) (24.9,-21.6) (26.9,-21.6) (28.7,-22.0) (30.4,-22.6) (31.0,-23.0)`,
1.45 m from the original at its furthest, round the spill's north side, with
the global costmap at 127 there: a cost, not a wall (lethal is 254).

**Arm 2, the silenced asserter.** The claim's lifecycle as the lane delivered
it, seconds from the kill:

```
 -5.38s  ALIVE                 CREATED
+10.00s  NOT_ALIVE_NO_WRITERS  (no payload)
```

No `NOT_ALIVE_DISPOSED`, no RETIRED sample. The robot node:
"svc:hazard:plaza-cam's liveliness ended; an OBSERVED claim nobody is still
making leaves the costmap. Kept, not retired". The mask was drawn clear, the
costmap went back to 0, and the re-plan is the original route to 0.000 m.

**Arm 3, the filter.** The stranger's claim reached the lane, CREATED,
OBSERVED, the same proposition; the node: "not drawing
rel:svc:hazard:stranger:ad777d9217cb53ab: svc:hazard:stranger, OBSERVED,
https://demo.spatialdds.org/owm/0.2/predicate/near#5m: no trust rule admits
it". Mask 0 cells, cost 0, the original route to 0.000 m.

**Each arm fails when its condition is broken.** Four plants, each run as a
whole tooth, each confirmed in its run before its result counted:

| plant | confirmed by | failed |
| --- | --- | --- |
| the claims filter removed from the global costmap (`NAV2_PARAMS`) | no `Initialized costmap filter "claims_filter"` in that run's log | arm 1: cost 0 at the spill, the route straight through |
| the layer ignores liveliness (a planted library copy) | the plant in the mounted file | arm 2: mask still 760, cost 127, the re-plan 1.448 m off; and arm 3 with it |
| SIGTERM where the arm wants SIGKILL (`KILL_SIGNAL=TERM`) | `kill-TERM` in its events; the lane: RETIRED "asserter stopped: SIGTERM, a clean stop", then `NOT_ALIVE_DISPOSED` | arm 2: disposed, retired, no age-out |
| the stranger trusted too (`SPATIALDDS_CLAIMS_TRUST`) | the node's startup note trusting both | arm 3: mask 760, cost 127, the route 1.448 m off |

**One arm is not independent of another, and it is said rather than
softened.** With age-out disabled, arm 3 fails as well as arm 2: the dead
camera's region is still painted when the stranger arrives. Arm 3 is clean
only when arm 2 holds; its own plant (the trust rule widened) fails it with
arm 2 passing.

**Arm 1 failed first for real.** The first live run's mask carried 3036
cells, the filter logged it received, and the global costmap stayed 0 at the
spill: nav2 Humble paints a keep-out mask only on its costmap's grid. Found
by this arm, measured to the mechanism (library finding 14), and fixed in the
library before release.

## 6. Filings

**To spatialdds-ros, its own `FINDINGS.md`:**
- **13.** A trust rule names an asserter nothing authenticates: `asserter` is
  a payload string, so anyone can type a trusted name. Not closable in the
  library; the advisory rule bounds it (a forged claim can bend a plan, and
  cannot make a region lethal).
- **14.** nav2 paints a keep-out mask only on its costmap's grid. Found by
  the tooth's first arm, measured to its mechanism, closed in 0.5.0 before
  release.

**To spatialdds-py (`spatialdds-web/WEB-BINDING-FINDINGS.md`, on that
repository's `ros-claims-filings` branch):**
- **80.** Three readers of the relationship lane, three meanings of a killed
  asserter: this repository's live view keeps and marks the edge, the
  library's layer stops planning by an OBSERVED one, and the crossing's
  controllers (`scenarios/crossing/controller.py:143-154`) never notice. The
  SDK could compute a claim's assertion state once.

**To the OWM 0.2 register, as proposed edits with their origins**, in the
register's form (`proposals/owm-0.2-relationships/edits.md` in the spec
repository, which this repository does not edit). The age-out and filter arms
are E1 and E11 territory.

- **E11, extended: say what an edge whose asserter's liveliness has ended
  means to a consumer.** *Origin:* §5 arm 2, and finding 80. E11 asks that a
  relationship lane carry liveliness; this batch is what a consumer does with
  it. Measured: SIGKILL, `NOT_ALIVE_NO_WRITERS` 10.0 s later (the default
  lease), never a dispose, never a RETIRED sample. The live view kept the edge
  and marked it unasserted; a planner acting on the present stopped drawing
  it, kept it, and would draw it again on the asserter's return. *Proposed:*
  name the state, "in force, unasserted", distinct from retired and from
  withdrawn, so that "a crash is never a retirement" has a third state to land
  in; and say whether its meaning depends on basis. This batch's consumer
  answers by basis: an OBSERVED edge nobody is still making is not about the
  present, and a DECLARED one stands until withdrawn (a crashed gate
  controller has not reopened its crossing).
- **E11, open question: DERIVED.** A derived edge is computed from evidence
  that may itself still be live. Whether its asserter's liveliness loss should
  age it out as OBSERVED's does, or let it stand as DECLARED's does, is not
  answered by this batch; the library treats it as DECLARED for now and says
  so.
- **E1, applied in consumption: neither the filter nor the age-out is a
  contrary claim.** *Origin:* §5 arms 2 and 3. A claim an untrusted asserter
  makes is ignored, not contradicted: the layer skips it with "no trust rule
  admits it" and the trusted asserters' claims are untouched. An aged-out
  edge is not read as "no longer holds": nothing is retired, `valid_until` is
  not inferred, and the record is kept. E1's sentence about two asserters
  holds for one asserter's silence across time as well.
- **New: the asserter is a payload string.** *Origin:* library finding 13.
  A consumer's trust rule, the crossing's `TRUSTED` included, admits by the
  `asserter` member, which any participant can type. *Proposed:* the 0.2
  design should say the asserter is established by the transport or the
  credential, never typed into the payload, which is the web binding design
  note's fourth carrier obligation; until it does, every consumer's trust
  filter defends against honest strangers only.
- **E3, evidence from a consumer: an oriented extent.** *Origin:* the claims
  layer's geometry (`claims-census.md` §3). To plan round a long thing turned
  across the axes, the layer reads the crossing's `demo.obb.*` meta, because
  the enclosing `Aabb3` of a 2 x 0.4 m box at 45° is 3.6 times its footprint.
  A second reader of a demo-namespaced workaround, and the first outside this
  repository: the crossing's controllers read `demo.obb.length_m`
  (`scenarios/crossing/controller.py`), and now the library reads both
  dimensions.

## 7. Suites

Each verbatim as `CONTRIBUTING.md` runs it, on this branch, with the
library at `cb38ec2` (a scratch venv over the base environment, the library
installed from that commit so the pin check reads a real recorded commit; the
host's own `spatialdds_ros` was left at 0.4.2).

- **Host suite** (unit and bridge logic): **639 passed, 18 skipped**,
  CONTRIBUTING's count. Its first run failed one test,
  `test_census_reproduces`: `docs/sdk-adoption-census.md`'s shrink line
  counts `spatialdds_demo/`'s lines, and this batch's `robot_bridge.py` header
  is 10 lines shorter. Regenerated by its own tool (`--write-doc`, 5805 to
  5795), not retyped.
- **Crossing scene**: `python3 -m pytest scenarios/crossing/tests`, with the
  pinned SUMO: **72 passed in 171 s**. No file under `scenarios/` changed.
- **Fountain, the web suite**: `npm test` against the private stack and its
  tier, both named by the new environment variables: **109 passed, 2 failed,
  4 skipped, 5 did not run** on the first run; then the 2 and the 5, run
  serially, **8 passed**. Neither failure was the branch's. `model-stack`'s
  basis test wanted `ent:pond:observed`, which `pond_watch` publishes, and the
  private stack had not started it (the running stack does, with
  `SPATIALDDS_POND_WATCH=1`); its serial file then stopped, so 5 did not run.
  `synthetic-frame` saw two `vps_query` where it wants one, beside the stack
  specs, and passed serially: the same symptom class as the two specs moved
  into the `stack` project (§4), recorded here rather than moved on one
  observation. The 4 skips say why (`deployed`, `owm-standalone`, `smoke`,
  and `claims-overlay-stack`'s pre-overlay comparison).
- **The library**: `python3 -m pytest test/ -q` in `spatialdds-ros`:
  **255 passed, 74 subtests passed**, against 226 and 61 at 0.4.2.
- **The tooth**: §5, all three arms on the committed library.

Fountain and crossing, untouched by every stage, as the brief required: no
change to their modules, lanes, tests, recordings or web clients. This batch's
web changes are harness only (`playwright.config.ts`, the stack and tier
names, one spec's interception URL), and its demo-side code is the tier's
configuration and the tooth's own tools.
