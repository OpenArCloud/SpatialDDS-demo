# Open World Model

The venue as a live model rather than a list of files. Entities with identity,
pose, type, **basis** and relationships; a robot that plans against the venue's
own declarations; ducks that follow the water because the water told them where
it is.

Background and design notes: [`docs/world-model.md`](world-model.md).

---

## The two-line version

```bash
SPATIALDDS_MODEL_LAYER=1 SPATIALDDS_ROBOT_SIM=1 ./run_bridge_server_docker.sh
cd web && npm install && npm run dev        # → http://localhost:5173/
```

That gives the whole venue: the model service, a second independent publisher
(the gnome), moving ducks, and a kinematic robot you can drive. No ROS needed.

Add `SPATIALDDS_POND_WATCH=1` for the **derived** pond — a second service
publishing its own measured opinion of the same water, next to the venue's
declared one. It is off by default and it is the clearest single illustration
of what `basis` is for, so turn it on if you are showing this to anyone.

### What to point at

Click any entity to open its panel — every field there is read off the entity,
nothing is authored by the client.

- **Four colours, four bases.** OBSERVED locates, DECLARED legislates, DERIVED
  opines, AUTHORED decorates. Two boxes over the same water disagree on purpose.
- **Send Robot: On**, then click the ground. The tap publishes a `goto` on the
  same command lane an operator script would use; the robot routes **around**
  the declared water rather than through it.
- **Drive it along the near edge** and the ducks cross to the far corner. They
  are reacting to the robot's live poses off the fast lane — the only place in
  the demo where one service reacts to another's measurement rather than to a
  venue declaration.

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

The kinematic robot above is a stand-in: it moves in a straight line to where
you tap. The real version runs **nav2** in a ROS 2 container, planning against a
costmap built from the venue's declared extents — so the keep-out is enforced by
a real planner rather than by the thing being planned.

That tier lives in [`robot_tier/`](../robot_tier/) — a Humble image with nav2
and CycloneDDS built from source, a keep-out node that turns declared entities
into a costmap filter mask, and a plaza simulator.

> **Not yet scripted.** There is no launcher for this tier and no README under
> `robot_tier/`. It has been run end to end — the captures in the Part 4 work
> are from it — but bringing it up is currently hand-assembled. A
> `scripts/run_robot_tier.sh` plus a short `robot_tier/README.md` is the
> outstanding piece of work before this half is demo-ready for someone who is
> not already holding the command in their head.

When it is running, `robot_bridge --source ros` owns `ent:robot:tb3` on the bus
and refuses to start if something else already does — so the kinematic robot and
the ROS one cannot both claim the key. Start the tier and leave
`SPATIALDDS_ROBOT_SIM` off.
