# Open World Model

The venue as a live model rather than a list of files. Entities with identity,
pose, type, **basis** and relationships; a robot that plans against the venue's
own declarations; ducks that follow the water because the water told them where
it is.

Background and design notes: [`docs/world-model.md`](world-model.md).

---

## Running it

Two terminals. The first is the venue; the second is the robot that plans
against it.

```bash
# 1 — the venue: model service, gnome, ducks, derived pond
SPATIALDDS_MODEL_LAYER=1 SPATIALDDS_POND_WATCH=1 ./run_bridge_server_docker.sh

# 2 — the robot: nav2, the keep-out node, the plaza sim
scripts/run_robot_tier.sh

# 3 — the browser
cd web && npm install && npm run dev        # → http://localhost:5173/
```

The tier takes about 20 s to bring nav2's lifecycle nodes to active. It shares
the bridge container's network namespace, so the bridge has to be up first.

### What to point at

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
