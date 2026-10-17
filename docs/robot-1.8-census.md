# The robot demo against SpatialDDS 1.8: a field-level census

The robot lane was deliberately left at the pre-1.8 types when the Open World
Model demo migrated. `b31b4e8` pinned it honestly — explicit
`TOPIC_MODEL_ENTITY_V1_PRE18` constants, a pre-1.8 `venue_frame`, a note at
each import saying why — so the lane names its own era in source rather than
being discovered as an empty bus. This census is the field-level check before
that pin comes out.

Verdicts as always: **HOME** (a 1.8 member carries it as-is), **IMPROVED**
(1.8 carries it better), **ADAPTER** (expressible, with a documented mapping),
**DROPPED** (with the reason), **GAP** (no 1.8 home; filed for the candidates
queue, never invented locally).

## The headline

**The robot lane introduces no type this repository has not already censused.**
It publishes and subscribes a strict subset of `spatial.owm/0.1` — the same
`Entity`, `ModelPose` and `ModelCommand` the OWM pilot moved — so every
member-level verdict is inherited from
[`owm-1.8-census.md`](owm-1.8-census.md) rather than re-derived here. 30 of 37
members HOME, `Relationship` the only GAP, and this lane never touched
`Relationship`.

What is new is three lane-specific facts, which is what the rest of this
document is. A census that re-listed thirty inherited rows to look thorough
would bury them.

| what the lane does | type | verdict |
| --- | --- | --- |
| writes `ent:robot:tb3`, latched | `spatial.owm.Entity` | **HOME**, inherited |
| writes the robot's pose, fast lane | `spatial.owm.ModelPose` | **HOME**, inherited |
| reads operator commands | `spatial.owm.ModelCommand` | **HOME**, inherited — see below |
| refuses to join a second writer | `spatial.owm.Entity` (read) | **HOME**, inherited |
| reads the venue's declared extents | `spatial.owm.Entity` (read) | **HOME**, inherited |
| the ROS tier's own traffic | none — ROS messages only | **out of scope of any SpatialDDS census** |

`robot_tier/plaza_sim_node.py` publishes no SpatialDDS type at all. It is pure
ROS — `Odometry`, `Twist`, TF — importing only two constants from
`spatialdds_demo.plaza`. It is the physics stand-in feeding odometry, and it
is named here so its absence from the type tables is a finding rather than an
oversight.

## 1. `ModelCommand` migrates HOME, and the robot is `goto`'s first consumer

Confirmed, not re-derived: `spatial.owm.ModelCommand` is `@key command_id`,
exactly as the demo-local type was, and all ten members are HOME. The OWM
census established this and corrected the expectation that keying needed work.

The lane-specific fact is the verb. `robot_bridge.py:216` reads exactly one:

```python
if command.verb != "goto":
```

1.8's vocabulary is `"retire" | "move" | "set_extent" | "restore" | "goto"`.
The OWM demo used four of those five; `goto` is the one it never used, and the
module's own comment anticipates it — verbs are defined "over entities only"
until relationships land. **So this lane is the first consumer of `goto` in
this repository**, which makes it the first thing to exercise that member of
the 1.8 verb list against a real publisher and a real subscriber. Nothing
about it needs adapting: the verb is a string, the string is one 1.8 defines,
and the handler already refuses everything else by name.

## 2. `FrameRef`: one write, zero reads, so the scale members are a claim and not a dependency

The brief asks which members the lane actually uses and whether 1.8's metric
scale members matter to it. Audited both directions across
`robot_bridge.py`, `robot_tier/keepout_node.py`, `robot_tier/plaza_sim_node.py`
and `spatialdds_demo/keepout.py`:

| | count | where |
| --- | --- | --- |
| sites constructing a `FrameRef` | 1 | `robot_bridge.py:160`, `frame_ref=venue_frame()` |
| sites reading **any** member of one | **0** | — |

Not `coord_convention`, not `has_scale`, not `meters_per_unit`, not
`display_unit`, not `scale_status`. The lane writes the frame whole and never
looks inside it, which means **the four members 1.8 added cannot change its
behaviour**. They change what it declares: the robot's pose arrives in a frame
that now states on the wire that it is metric, instead of leaving every
consumer to assume it.

That makes this migration's riskiest-looking type change the cheapest part of
the work — one line, from `pre18.venue_frame` back to
`model_service.venue_frame`, which is precisely the import `b31b4e8` pinned.
The cost of the pin coming out is one symbol.

## 3. `content_refs`: this lane owns the OWM census's single ADAPTER instance

`robot_bridge.py` publishes `content_refs=[f"catalog:{ROBOT_CONTENT_ID}"]`.
The OWM census marked the member HOME and its *values* ADAPTER, and recorded
that the whole scope of the adapter in this demo is one entity — the robot's.
This batch inherits that row and does not apply it, for the reason the OWM
census already gave and which has not changed:

> a `spatialdds://` URI names an authority that resolves it, and this demo has
> no resolver and no manifest store. Writing a URI that resolves nowhere would
> be worse than a `catalog:` ref that is honestly local.

The trigger stated there was a resolver in the demo. There still isn't one —
the gateway can serve resolution with `--authority`, but that is the
validation instrument, not the demo, and nothing in this repository stores a
manifest. So the row stays **ADAPTER-documented**, carried forward rather than
silently dropped, and the first batch that gives the demo a resolver is the
batch that owns applying it.

## 4. Topics: the same scene, because it is the same venue

| pre-1.8 | 1.8, §3.3.1 five-segment |
| --- | --- |
| `spatialdds/model/entity/v1` | `spatialdds/ut-austin-littlefield/world/entity/v1` |
| `spatialdds/model/pose/v1` | `spatialdds/ut-austin-littlefield/world/model_pose/v1` |
| `spatialdds/model/command/v1` | `spatialdds/ut-austin-littlefield/world/model_command/v1` |

The same scene the OWM pilot settled on, not a second one. The robot stands in
the Littlefield plaza beside the fountain it plans around; a separate scene
would claim it was somewhere else and would give the venue two entity
namespaces for one venue.

## What the bridge batch inherits

The `_PRE18` constants exist for consumers that have not migrated. After this
batch the robot lane is no longer one, so the survivor set should be the
bridge's alone.

That was the prediction, and the audit corrected it: two consumers were
missing from it — the robot lane's own tests, which migrate with the lane they
test. The prediction's *conclusion* holds, its enumeration did not, and the
table below is the audit's rather than the prediction's.

| pre-1.8 symbol | consumers today | after this batch |
| --- | --- | --- |
| `TOPIC_MODEL_ENTITY_V1_PRE18` | `web_bridge/server.py`, `robot_bridge.py`, `keepout_node.py`, `test_robot_bridge.py` | the bridge |
| `TOPIC_MODEL_POSE_V1_PRE18` | `web_bridge/server.py`, `robot_bridge.py`, `test_robot_bridge.py` | the bridge |
| `TOPIC_MODEL_COMMAND_V1_PRE18` | `web_bridge/server.py`, `robot_bridge.py`, `test_command_lane.py`, `test_robot_bridge.py` | the bridge |
| `TOPIC_MODEL_RELATIONSHIP_V1_RETIRED` | `web_bridge/server.py` | the bridge |
| `pre18.seed_relationships` | `web_bridge/test_model_cache.py` | the bridge's fixtures |
| `pre18.venue_frame` | `robot_bridge.py` | **nobody** |

(`spatialdds_demo/topics.py` defines them all and is not a consumer.)

`pre18.venue_frame` existed for exactly one consumer — the robot lane — so it
retires with the pin. `pre18.seed_relationships` stays, because the bridge
still builds fixtures from it, and the module keeps its name for that one
reason. Every constant that remains has a named consumer beside it; the one
that does not goes.

The 1.7 build keeps compiling `idl/demo/oarc_model.idl` for those consumers,
as it has since the OWM pilot.

## What runs locally, and what needs ROS

The brief asks for the duck-reaction behaviour if it is exercisable, and for
a plain statement of which parts are not.

| part | locally? | why |
| --- | --- | --- |
| the robot entity and its poses on the bus | **yes** | `--source kinematic` — `rclpy` is imported inside the ROS function, not at module scope, deliberately so this path needs no ROS |
| `goto` through the command lane | **yes** | same path; the kinematic navigator takes the goal |
| **the ducks reacting to the robot** | **yes** | `duck_mover` reads robot poses off the fast lane; nothing in that path is ROS |
| nav2 routing around declared keep-outs | **no** | `robot_tier/keepout_node.py` imports `rclpy` and `nav2_msgs` at module scope and publishes a costmap filter for nav2 |
| the plaza physics | **no** | `plaza_sim_node.py` is a ROS node; the kinematic sim inside `robot_bridge` is the stand-in |

The stand-in is the demo's own documented no-ROS path, not something invented
for this batch — `docs/ismar-owm.md` already offers `SPATIALDDS_ROBOT_SIM=1`
for looking at the demo without the nav2 tier. A labeled stand-in that already
exists beats anything built for the occasion.

So the acceptance recording can carry the reaction and cannot carry the
costmap. Stated here so the evidence is read for what it covers.

## The severance, and why it is the staged approach working

Today `duck_mover` reads robot poses on
`spatialdds/ut-austin-littlefield/world/model_pose/v1` while `robot_bridge`
writes them on `spatialdds/model/pose/v1`. The ducks have nothing to react to.

**That severance is the staged migration working as designed, not a defect
discovered.** The lane was pinned explicitly rather than left to inherit
renamed constants, the cost was named in the OWM report, in
`docs/world-model.md` and in the bridge's own README, and it was accepted as
the price of not dragging four out-of-scope demos into one batch. A staged
migration that hides its costs is the failure mode; this one wrote them down.

What this batch adds is the other half: the cost **measured**. The committed
OWM capture is the before — 243 pose samples, all duck poses, zero robot poses
on the lane, by construction, since that session ran the model service, the
gnome, `pond_watch` and `duck_mover` and no robot bridge at all. The after
capture has to show robot poses on the scene-scoped topic and duck motion
correlated with robot proximity, as a number with a control, not as something
that looked right. The measure, with its control: of the duck moves issued
while the robot is inside the reaction threshold, the fraction that increase
duck-robot distance, against the same fraction while the robot is outside it.
A single "ducks moved away" percentage would not do — ducks wander anyway, so
without the far-field baseline beside it the number cannot tell reaction from
coincidence. Both come out of the one recording, so the control is not a
separate run that could be tuned. It lands in
`recordings/robot-littlefield.evidence.md`, which the migration commit
creates.

## The stop condition

**No verdict blocks the robot demo's core behaviour.** Nothing is DROPPED,
nothing is GAP, the one ADAPTER row is inherited and still correctly
unapplied, and the only IMPROVED row — `FrameRef`'s four new members — cannot
change this lane's behaviour because the lane reads none of them. The riskiest
part of the OWM migration is the cheapest part of this one.

The honest caveat is scope, not risk: half of what the robot demo *shows* at
ISMAR runs on ROS, and this batch neither migrates nor exercises that half. It
migrates the robot's conversation with the world over DDS, which is what the
directive scoped, and the evidence will say which half it covers.

## What this census does not cover

- **The bridge, both lanes.** Out of scope by the directive; its inheritance
  is tabulated above.
- **Live web serving and the browser view.** Awaiting the bridge batch.
- **The ROS tier itself.** `keepout_node.py` and `plaza_sim_node.py` publish
  no SpatialDDS types; migrating the costmap path is its own batch and needs a
  ROS environment to prove anything.
- **SceneScape and deployment.** Untouched.
