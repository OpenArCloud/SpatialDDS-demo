# The web bridge on the 1.8 binding: both lanes, end to end

Acceptance evidence for `bridges/web_bridge/binding.py`, the OWM lane of the
web bridge rebuilt on Appendix N. The census is
[`docs/bridge-1.8-census.md`](../docs/bridge-1.8-census.md), which stopped
this batch twice before it could be built.

The claim: a web client with no DDS and no code from this repository can steer
a robot on a DDS bus and watch the venue react, through one implementation of
the binding.

## What was run, and against what

| | |
| --- | --- |
| recording | `recordings/bridge-littlefield.mcap` |
| sha256 | `1914605c8de1e7bd99f00ca99e2c9f65066a2612e5e309d835df665817d49b5f` |
| captured | 2026-10-18T03:11:59Z, 70.02s of live DDS |
| driven by | tools/capture_bridge_session.sh (web POST -> bus -> N.5) |
| samples | 1334 (174 entity, 750 model_pose, 410 model_command) |
| binding | `spatialdds[server]` **0.5.2 from PyPI**, embedded --- not a checkout |
| conformance suite | `SpatialDDS-spec` at `5ee5f96` |

```sh
docker run --rm -p 8089:8089 -v "$PWD:/app" -w /app cyclonedds-python \
    bash tools/capture_bridge_session.sh
```

**Option 2, as ruled.** The bridge embeds the package's server machinery
against a live DDS backend and implements no part of Appendix N itself. Five
public seams, used as published: `pin.model_from_records` for the wire model,
`publish.apply_sample`/`apply_dispose` for state, `gw_http.notify` for live
subscribers, `commands.Registry` for the upstream lane, and
`make_app(publish_enabled=False, live_subscriptions=True)` for a live lane
with no write surface --- a combination that did not exist before 0.5.0.

Three of those seams were added or fixed *because* this bridge needed them.
The batch stopped twice and filed three defects rather than reimplement any
of them here; that history is in the census and in 0.4.0, 0.5.0, 0.5.1 and
0.5.2.

## 1. Upstream: a web POST becomes a verb on the bus

```
  POST ut-austin-littlefield/world/model_command/v1 -> 202 application/json
    {"outcome": "accepted", "reason": "published 'goto' for 'ent:robot:tb3' on spatialdds/ut-austin-littlefield/world/model_command/v1", "type": "spatial.owm.ModelCommand"}
  POST ut-austin-littlefield/world/model_command/v1 -> 202 application/json
    {"outcome": "accepted", "reason": "published 'goto' for 'ent:robot:tb3' on spatialdds/ut-austin-littlefield/world/model_command/v1", "type": "spatial.owm.ModelCommand"}
```

`tools/post_goto.py` is `requests` and nothing else. It finds the command lane
from the manifest's `accepts_commands` rather than by constructing a path,
POSTs a `goto`, and prints the N.6 outcome verbatim. The bridge's reason names
the DDS topic it published on, so the response says where the command went
rather than only that it was taken.

This is also the test of 0.4.0's stated absence --- the package ships the
command surface and no client helper, on the argument that
`requests.post(url, json=record)` is the whole of it. Read back: it is. The
absence stands.

## 2. Downstream: the venue reaching an N.5 subscriber

`tools/watch_n5.py`, one socket per resource, because N.5 has no pattern
subscribe:

| lane | events | samples | marker |
| --- | --- | --- | --- |
| `entity` | 177 | 176 | 1 |
| `model_pose` | 752 | 751 | 1 |

Per key, the robot is the busiest thing on both lanes: 70
entity updates and 343 poses, with the three ducks at
136 poses each.

**These numbers are the 0.5.2 fix.** On 0.5.1 the same session delivered 79
entity samples and 0 pose samples to the same subscriber, because `notify`
could not wake a parked subscriber from the pump's thread. The bridge's own
heartbeat said `subscribers=2` throughout, which is what made the silence a
puzzle rather than an error.

## 3. The reaction, measured on this capture

```
reaction measured from bridge-littlefield.mcap
  avoiding ent:robot:tb3 within 15.0 m, water = ent:shallows:littlefield
  342 robot pose sample(s); duck poses catalog-pose=136, east=136, west=136

  robot      duck obs  to far corner   to robot
  inside          239         0.85 m     11.4 m
  outside         168         2.69 m     17.2 m

  ducks sit +1.84 m closer to the corner furthest from the robot
  while it is inside the radius than while it is outside — the reaction

  the reported null, so nobody re-derives it as a contradiction:
    per-step 'distance to robot increased' — inside 52% of 236, outside 50% of 168
    flat by design: the mover relocates to the far corner rather than nudging away each step,
    so once a duck is there, per-step change is boundary jitter.
  (1 sample(s) skipped: no robot pose fresher than 3s, which is when the mover stops avoiding too)

  both arms have >= 20 steps, so the comparison stands on its own
```

The chain in one number: a web client POSTed a `goto`, the robot drove to it,
and the ducks ended up **1.84 m closer to the corner of their water furthest
from the robot** while it was inside the 15 m avoidance radius than while it
was outside. The outside arm is the control, from the same recording, so it
cannot have been tuned to agree. The per-step null is printed beside it by the
tool itself, for the reason stated there.

## 4. The conformance suite, verbatim

```
conformance self-check OK (112 schemas load; canonical-form helpers available).
  PASS     schemas endpoint is self-describing
  PASS     every resource validates against its published schema  (9 latched instance(s) across 1 lane(s); UNEXERCISED latched (empty collection — streamed data checked over N.5): ['spatialdds/ut-austin-littlefield/world/model_command/v1', 'spatialdds/ut-austin-littlefield/world/model_pose/v1'])
  PASS     every streamed payload validates against its published schema  (1497 streamed payload(s) across 3 lane(s))
  PASS     canonical form on the wire  (int64_string=9, enum_name=45, guard_literal=36, union_tagged=0; UNEXERCISED by this endpoint's data: ['union_tagged'])
  PASS     latched state is GET with ETag, Cache-Control and 304  (1 latched resource(s))
  PASS     access classes, both directions  (0 public, 3 operator-scoped; UNEXERCISED: public-cacheable (no public resource to test the cacheable path))
  PASS     DDS-join material optional-and-validated  (bootstrap=404, connection=absent, resolver=404)
  PASS     resolver is a checked surface (§7.5.2)  (resolver 404 — the endpoint is not an authority (169fabd); the resolve surface is UNEXERCISED (zero observations))
  PASS     service manifests match §8.2.3  (1 manifest(s))
  PASS     commands are POST, or cleanly absent  (1 advertised command lane(s), body synthesized from published schema: ['model_command=202 accepted']; selected by accepts_commands, not by position)
  PASS     the carried QoS subset is observable, the rest absent  (durability ['TRANSIENT_LOCAL', 'VOLATILE'] — both kinds carried)
  PASS     §8.1 hello spatial in ten lines  (10 lines, 9 replayed sample(s), marker then live)
  SKIPPED  §8.2 anchor resolve flow
           no served GeoAnchor carries a manifest_uri (the 1.8-revision field). UNEXERCISED: the anchor-resolve flow needs an anchor-referenced manifest to follow, and none is present. The reference gateway gains one when spatialdds-web re-authors the fountain with an anchor-referenced duck.
  PASS     §8.3 backend-blind, the same suite for both shapes  (no backend branch; runner inputs ['connect', 'endpoint', 'token'])
  PASS     §8.4 third-party reader, published schemas only  (9 instance(s) read from the published schemas alone)

15 checks: 14 passed, 0 failed, 1 skipped (skipped: §8.2 anchor resolve flow)
```

**15 checks: 14 passed, 0 failed, 1 skipped.** Two of those checks are new
since this batch began and both exist because of findings filed from here:

- `every streamed payload validates against its published schema` --- 1497
  payloads across 3 lanes. Before this check the suite validated only latched
  instances, which on this endpoint is 9 of them, and the volatile lanes
  carrying 88% of the traffic went unexamined
  ([`docs/conformance-volatile-lane-finding.md`](../docs/conformance-volatile-lane-finding.md)).
- `commands are POST, or cleanly absent` --- now reported as *"selected by
  accepts_commands, not by position"*, with the body synthesized from the
  published schema. It had POSTed to `discover(ep)[0]` and so could never
  reach a command lane that was not first; it then briefly failed this bridge
  for want of a record on a VOLATILE lane. Both were reported and fixed
  spec-side, not patched around here.

The one skip is §8.2's anchor flow: it needs a `GeoAnchor` carrying a
`manifest_uri` and this demo publishes no anchors. UNEXERCISED, which is the
suite's own vocabulary for a check whose subject is absent.

## 5. Every served payload against the published schemas

```
validated against the endpoint's own published schemas
  bound: each lane read for 20s (a live endpoint: these counts are a FLOOR, not a total)

  spatial.owm.Entity               58 payload(s) validated (9 replayed, 49 live)
  spatial.owm.ModelCommand        118 payload(s) validated (0 replayed, 118 live)
  spatial.owm.ModelPose           214 payload(s) validated (0 replayed, 214 live)
  non-sample events (dispose/marker): 3

OK: 390 payload(s) across 3 type(s), 0 failures
```

**390 payloads, 0 failures**, and the bound is on the page: against a live
endpoint each lane is read for a fixed window, so these counts are a floor
rather than a total. That distinction is itself a correction --- the first
run of this validator reported 0 payloads on two live lanes because its
silence-timeout was tuned for recordings, where a quiet socket means a
finished file.

## What this does not prove

- **The browser view.** `web/src/` still speaks the pre-1.8 custom surface:
  `GET /v1/model` and a `spatialdds/model/*` pattern subscribe that N.5 has no
  equivalent for. Rewiring it to per-resource sockets is real client work and
  is not in this batch, so the Vite tab does not light up yet. The census
  itemises exactly what it needs.
- **The AR demo's seven routes.** Localize, catalogue, frames and the rest
  stay on `server.py`'s own surface; Appendix N has no RPC shape to move them
  to, and they are 1.9 entry 5's input rather than debt.
- **nav2 and the plaza physics.** ROS nodes, unexercised here, as in the robot
  batch.
- **Soak.** 70.02 seconds on loopback DDS with one subscriber.
