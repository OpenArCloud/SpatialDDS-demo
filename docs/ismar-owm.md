# Open World Model

The venue as a live model rather than a list of files. Entities with identity,
pose, type, **basis** and lifecycle; a robot that plans against the venue's
own declarations; ducks that follow the water because the water told them where
it is.

Published as `spatial.owm/0.1`, the provisional Open World Model module in
SpatialDDS 1.8, which this demo's own prototype became. Relationships are not
in it: the module excludes them explicitly and reserves them as open design,
so the venue's containment hierarchy is the one thing the migration gave up.

Background and design notes: [`docs/world-model.md`](world-model.md); the
field-level migration census, [`docs/owm-1.8-census.md`](owm-1.8-census.md).

---

## Migration: all three limbs are live

The staged migration is finished. This page described two of three limbs as
pinned at the pre-1.8 types from 2026-10-17 until 2026-10-18, and told the
reader that steps 2 and 3 had "nothing to show today" --- which was true when
written and is not now.

| limb | state |
| --- | --- |
| the venue — model service, ducks, gnome, derived pond, operator scripts | **migrated**, `spatial.owm/0.1` on scene-scoped topics |
| the robot lane — `robot_bridge`, `robot_tier` | **migrated** (`92d9232`), reading `TOPIC_MODEL_*_V1` directly |
| the web bridge and this browser view | **migrated** (`f5f4a6e`, `6c578ac`): state over Appendix N.3, live over N.5, commands over N.6 |

The pre-1.8 topic names and the `_PRE18` constants are gone, with the
`_ModelPump` and `/v1/model` surface that read them. The browser now talks to
two processes: `server.py` on `:8088` for the AR demo's routes, and
`binding.py` on `:8089` for the model. **Step 1 starts both** --- the launcher
brings the binding up with the model layer, since without the venue there is
nothing for it to serve.

Field-level record: [`docs/owm-1.8-census.md`](owm-1.8-census.md) for the
venue, [`docs/bridge-1.8-census.md`](bridge-1.8-census.md) for the bridge.


The migration's field-level account is [`owm-1.8-census.md`](owm-1.8-census.md);
its acceptance evidence is
[`recordings/owm-littlefield.evidence.md`](../recordings/owm-littlefield.evidence.md).

## Running it

Two terminals. The first is the venue; the second is the robot that plans
against it. **Steps 2 and 3 await the batches named above.**

```bash
# 1 — the venue: model service, gnome, ducks, derived pond
SPATIALDDS_MODEL_LAYER=1 SPATIALDDS_POND_WATCH=1 ./run_bridge_server_docker.sh

# 2 — the robot: nav2, the keep-out node, the plaza sim
scripts/run_robot_tier.sh

# 3 — the browser
cd web && npm install
VITE_SPATIALDDS_BINDING_URL=http://localhost:5173 \
VITE_SPATIALDDS_BINDING_TOKEN=bridge-session npm run dev   # → :5173
```

**Those two variables are what make `Send Robot` work**, and the reason is
not obvious. A command is `operator-scoped`, so it carries a credential;
`Authorization` is not CORS-safelisted, so a cross-origin POST preflights; and
the binding routes no `OPTIONS`. A page on `:5173` talking straight to `:8089`
therefore cannot command it at all. Pointing the client at `:5173` sends the
request through this dev server's proxy instead, same-origin, where no
preflight happens --- `server.proxy` in `web/vite.config.ts`, `ws: true` for
N.5's sockets. Reading the venue needs neither variable: those lanes are
public and the client deliberately sends no credential on them.

Plain `npm run dev` still shows the whole venue, live and moving. Only
commanding is blocked, and `tools/post_goto.py` drives the robot from a
terminal in that case. The gap is filed at
[`docs/browser-credential-finding.md`](browser-credential-finding.md),
blocker B, and queued as a gateway fix for `spatialdds-web`.

The tier takes about 20 s to bring nav2's lifecycle nodes to active. It shares
the bridge container's network namespace, so the bridge has to be up first.

### What to point at

Every item here reads the model through the browser, over the web binding.
The measured numbers are the acceptance record for the robot tier.

Click any entity to open its panel — every field there is read off the entity,
nothing is authored by the client.

- **Four colours, four bases.** OBSERVED locates, DECLARED legislates, DERIVED
  opines, AUTHORED decorates. Two boxes over the same water disagree on purpose.
- **Send Robot: On**, then click the grass on the far side of the pond. The tap
  publishes a `goto` on the same command lane an operator script would use, and
  nav2 **routes around the water** — it has built a costmap from the venue's
  declared extents. Measured on a live stack: 67 samples from (17.0, −21.5) to
  the far grass, **zero inside the declared water**, with the path swinging out
  to x = −2.7 where a straight line would have stayed between 6 and 17.
- **Drive it along the near edge** and the ducks cross to the far corner. They
  are reacting to the robot's live poses off the fast lane — the only place in
  the demo where one service reacts to another's measurement rather than to a
  venue declaration.

Which way it goes round is just geometry: the planner takes the nearer end, so
a start east of the pond sweeps east and one in front of it sweeps west.

### Without ROS

`SPATIALDDS_ROBOT_SIM=1` on the bridge gives a kinematic stand-in and needs no
second terminal. **It does not plan.** It drives straight lines and declines
any step that would put its footprint inside a declared keep-out — so it stops
at the water's edge rather than going round it.

That is enough for looking at the model, and it is not the demonstration. The
routing is the demonstration, and the routing is nav2.

### Change the world from a terminal

```bash
C=$(docker ps --format '{{.Names}}' | grep dds_bridge | head -1)

# shrink the water — the robot re-routes, the box on screen shrinks
docker exec -w /app $C python3 scripts/reshape_pond.py --shrink 0.4

# shrink the duck area — the ducks crowd in; the robot is unaffected
docker exec -w /app $C python3 scripts/reshape_pond.py \
    --entity ent:shallows:littlefield --shrink 0.4

# move one duck; retire one and watch it go grey, then disappear
docker exec -w /app $C python3 scripts/move_duck.py ent:duck:west 12.0 -13.0
docker exec -w /app $C python3 scripts/retire_entity.py ent:duck:east "winter"

# put everything back
docker exec -w /app $C python3 scripts/reshape_pond.py --restore
docker exec -w /app $C python3 scripts/retire_entity.py --restore
```

**Two declared boxes, on purpose.** The pond covers the whole water, because a
keep-out has to *cover* what it forbids — outside it must mean dry ground. The
shallows sit inside the water, because a duck is clamped *into* its box and
would otherwise land on paving. The margins point opposite ways, and no single
rectangle does both jobs.

## The ROS 2 robot tier

**nav2** in a ROS 2 container, planning against a costmap built from the venue's
declared extents — so the keep-out is enforced by a real planner rather than by
the thing being planned. A Humble image with nav2 and CycloneDDS built from
source, a keep-out node that turns declared entities into a costmap filter mask,
and a kinematic base with no physics and no sensors under it.

How the declaration becomes a costmap, what the probes are for, and the three
details in the keep-out node that were each found the hard way:
[`robot_tier/README.md`](../robot_tier/README.md).

One robot per key. `robot_bridge --source ros` owns `ent:robot:tb3` and refuses
to start if something else already does, so the kinematic fallback and the ROS
one cannot both claim it. `run_robot_tier.sh` checks for the fallback up front
rather than letting the race decide. Start the tier and leave
`SPATIALDDS_ROBOT_SIM` off.

### Measured on this stack

```
bridge up                     6 s      9 entities, all four bases
nav2 lifecycle active        14 s
keep-out mask         350x233 cells at 0.10 m, origin (-5.5, -24.0)
                      415 m2 keep-out + 74 m2 shoulder
                      from ent:pond:littlefield, ent:shallows:littlefield
tap-to-goto           asked (24, -20), arrived (23.06, -20.92)
route behind the pond 67 samples, 0 inside the declared water
```
