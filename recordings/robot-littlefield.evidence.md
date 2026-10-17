# The robot demo on SpatialDDS 1.8, and a severed behaviour measured back

Acceptance evidence for the robot lane's migration from the pre-1.8 types to
`spatial.owm/0.1`. The census is [`docs/robot-1.8-census.md`](../docs/robot-1.8-census.md).

Two claims here, and the second is the one that needed care: the lane migrated,
and **the duck-reaction behaviour the staged migration severed is back** --
asserted by measurement out of the committed artifact, not by watching.

## What was run, and against what

| | |
| --- | --- |
| recording | `recordings/robot-littlefield.mcap` |
| sha256 | `dfe39d5da91397555ad83986397f2ad7592c7cddee0f1c9745bd5f488d276edb` |
| captured | 2026-10-17T17:09:40Z, 70.01s of live DDS |
| driven by | tools/capture_robot_session.sh (scripted operator session) |
| samples | 1331 across 3 topics |
| robot source | `--source kinematic` — no ROS, no nav2, no hardware |
| gateway | `spatialdds-web` at `c6e147a`, **unmodified** |
| conformance suite | `SpatialDDS-spec` at `5dc27b0` |

```sh
docker run --rm -v "$PWD:/app" -w /app cyclonedds-python \
    bash tools/capture_robot_session.sh
```

Same ordering contract as the OWM capture — recorder in the foreground, every
publisher killed only after it returns — for the same reason, which
`tools/capture_owm_session.sh` states in full.

## 1. The reconnection, as a count

`duck_mover` reads robot poses on
`spatialdds/ut-austin-littlefield/world/model_pose/v1`. Before this batch the
robot wrote them on `spatialdds/model/pose/v1`, so there was nothing on that
lane to react to.

| | robot pose samples on the scene-scoped lane |
| --- | --- |
| **before** — `owm-littlefield.mcap`, 243 pose samples | **0** |
| **after** — this recording | **341** |

The before figure is zero by construction, not by inspection: that session ran
the model service, the gnome, `pond_watch` and `duck_mover`, and no robot
bridge at all. The after figure is counted off this artifact by
`tools/measure_duck_reaction.py`, which prints it in the block below, so both
halves of the comparison are checkable against committed files rather than
against this paragraph:

```sh
python3 tools/measure_duck_reaction.py recordings/robot-littlefield.mcap
```

## 2. The reaction, with its control

```
reaction measured from robot-littlefield.mcap
  avoiding ent:robot:tb3 within 15.0 m, water = ent:shallows:littlefield
  341 robot pose sample(s); duck poses catalog-pose=136, east=135, west=136

  robot      duck obs  to far corner   to robot
  inside          233         0.94 m     11.5 m
  outside         174         2.97 m     16.8 m

  ducks sit +2.03 m closer to the corner furthest from the robot
  while it is inside the radius than while it is outside — the reaction

  the reported null, so nobody re-derives it as a contradiction:
    per-step 'distance to robot increased' — inside 51% of 230, outside 52% of 174
    flat by design: the mover relocates to the far corner rather than nudging away each step,
    so once a duck is there, per-step change is boundary jitter.

  both arms have >= 20 steps, so the comparison stands on its own
```

**Ducks sit 2.03 m closer to the corner of their water furthest from the robot
while the robot is inside the 15 m avoidance radius than while it is outside
it.** 0.94 m against 2.97 m, over 233 and 174 duck observations.

The control is what makes that a measurement. Ducks wander, so "the ducks were
further away" proves nothing on its own; the outside-the-radius arm is the same
ducks in the same recording with the logic not engaged, so it cannot have been
tuned to agree.

**The reported null matters as much as the figure.** The first measure built
for this -- per duck step, did distance to the robot increase -- returns 51%
inside against 52% outside. That is the measure being wrong, not the behaviour:
`step_for` steers for "as far from it as this water gets", a relocation to the
far corner, after which per-step distance change is boundary jitter. The null
is printed by the tool itself so that nobody recomputes it later and reads it
as a contradiction. Classified as an **instrument defect**, mine, this batch.

The final latched state shows the same thing without any arithmetic: the three
ducks end at x 17.4-18.7, y -12.1 to -12.7, the north-east of the shallows,
while the robot sits at (12.44, -21.24) to the south-west.

## 3. Served by the unmodified gateway

```
recording     /Users/fiberhog/AGENT_DEV/OARC/SpatialDDS-demo/recordings/robot-littlefield.mcap
              sha256 dfe39d5da91397555ad83986397f2ad7592c7cddee0f1c9745bd5f488d276edb
types         the recording's own embedded IDL (robot-littlefield.mcap)
              SpatialDDS-spec 9c3e1f2
serving       http://127.0.0.1:8851
bearer token  robot-accept

  3 resource(s) in 1 domain(s), 9 latched instance(s), 1330 live event(s)

  operator-scoped   /ut-austin-littlefield/world/entity/v1
                         1 replayed,    9 current,    172 live
  operator-scoped   /ut-austin-littlefield/world/model_command/v1
                         0 replayed,    0 current,    410 live
  operator-scoped   /ut-austin-littlefield/world/model_pose/v1
                         0 replayed,    0 current,    748 live
```

## 4. The conformance suite, verbatim

```
conformance self-check OK (112 schemas load; canonical-form helpers available).
  PASS     schemas endpoint is self-describing
  PASS     every resource validates against its published schema  (9 instance(s))
  PASS     canonical form on the wire  (int64_string=9, enum_name=45, guard_literal=36, union_tagged=0; UNEXERCISED by this endpoint's data: ['union_tagged'])
  PASS     latched state is GET with ETag, Cache-Control and 304  (1 latched resource(s))
  PASS     access classes, both directions  (0 public, 3 operator-scoped; UNEXERCISED: public-cacheable (no public resource to test the cacheable path))
  PASS     DDS-join material optional-and-validated  (bootstrap=404, connection=absent, resolver=404)
  PASS     resolver is a checked surface (§7.5.2)  (resolver 404 — the endpoint is not an authority (169fabd); the resolve surface is UNEXERCISED (zero observations))
  PASS     service manifests match §8.2.3  (1 manifest(s))
  PASS     commands are POST, or cleanly absent  (POST -> 405 (no command lane advertised))
  PASS     the carried QoS subset is observable, the rest absent  (durability ['TRANSIENT_LOCAL', 'VOLATILE'] — both kinds carried)
  PASS     §8.1 hello spatial in ten lines  (10 lines, 1 replayed sample(s), marker then live)
  SKIPPED  §8.2 anchor resolve flow
           no served GeoAnchor carries a manifest_uri (the 1.8-revision field). UNEXERCISED: the anchor-resolve flow needs an anchor-referenced manifest to follow, and none is present. The reference gateway gains one when spatialdds-web re-authors the fountain with an anchor-referenced duck.
  PASS     §8.3 backend-blind, the same suite for both shapes  (no backend branch; runner inputs ['connect', 'endpoint', 'token'])
  PASS     §8.4 third-party reader, published schemas only  (9 instance(s) read from the published schemas alone)

14 checks: 13 passed, 0 failed, 1 skipped (skipped: §8.2 anchor resolve flow)
```

The skip is §8.2's anchor flow, UNEXERCISED for want of an anchor this demo
does not publish — the same honest skip the OWM pilot reported.

## 5. Every served payload against the endpoint's published schemas

```
validated against the endpoint's own published schemas

  spatial.owm.Entity              173 payload(s) validated (1 replayed, 172 live)
  spatial.owm.ModelCommand        410 payload(s) validated (0 replayed, 410 live)
  spatial.owm.ModelPose           748 payload(s) validated (0 replayed, 748 live)
  non-sample events (dispose/marker): 12

OK: 1331 payload(s) across 3 type(s), 0 failures
```

1331 payloads, 0 failures, reconciling exactly with the recorder's provenance.

## 6. The demo-able artifact

```
ent:duck:catalog-pose        FAST    AUTHORED (  17.44, -12.10)  ACTIVE  Waddles
ent:duck:east                FAST    AUTHORED (  17.51, -12.73)  ACTIVE  Skipper
ent:duck:west                FAST    AUTHORED (  18.73, -12.14)  ACTIVE  Bobbin
ent:robot:tb3                FAST    OBSERVED (  12.44, -21.24)  ACTIVE  TurtleBot
ent:gnome:visitor            SLOW    AUTHORED (  20.00, -21.00)  ACTIVE  
ent:pond:observed            SLOW    DERIVED  (  12.11, -12.40)  ACTIVE  Pond
ent:fountain:littlefield     STATIC  OBSERVED (  11.71,  -5.23)  ACTIVE  Littlefield Fountain
ent:pond:littlefield         STATIC  DECLARED (  12.00, -12.38)  ACTIVE  Pond
ent:shallows:littlefield     STATIC  DECLARED (  14.75, -14.00)  ACTIVE  Shallows

9 entities in spatialdds-web-ut-austi, frame map/ut-littlefield-fountain (ENU, 1 m/unit, SCALE_DECLARED)
```

Nine entities now, the robot among them as `OBSERVED` — it is looking at the
world rather than fusing other people's claims about it — and the venue frame
still declaring itself metric on the wire.

## What this does not prove, and it is half the demo

- **nav2 routing around declared keep-outs.** `robot_tier/keepout_node.py`
  imports `rclpy` and `nav2_msgs` at module scope and publishes a costmap
  filter; it needs a ROS environment and is not exercised here. The measured
  path-avoidance numbers in `docs/ismar-owm.md` remain the record for it, and
  reproducing them is the ROS tier's own batch.
- **The plaza physics.** `plaza_sim_node.py` is a ROS node. The kinematic sim
  inside `robot_bridge` is the stand-in, and it drives straight lines rather
  than planning — which is why the tier is still the demonstration.
- **The browser view.** Awaiting the bridge batch.
- **Soak.** 70.01 seconds on loopback DDS.

The stand-in is the demo's own documented no-ROS path, not something invented
for this batch, and this document covers the robot's conversation with the
world over DDS. The half that runs on ROS is named rather than implied.
