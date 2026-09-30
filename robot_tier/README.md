# The ROS 2 robot tier

A real **nav2** planning against the venue's own declarations. The robot does
not know where the water is; it knows where the model says a robot may not go,
and that is enough to route around it.

This is the half of the [Open World Model demo](../docs/ismar-owm.md) that
plans. The kinematic fallback in the bridge (`SPATIALDDS_ROBOT_SIM=1`) drives
straight lines and refuses steps that would put its footprint inside a declared
keep-out — so it stops at the water's edge. nav2 goes round.

## Running it

```bash
docker build -t spatialdds-robot-tier:latest robot_tier/     # once; slow, see below
./run_bridge_server_docker.sh                                # terminal 1, no ROBOT_SIM
scripts/run_robot_tier.sh                                    # terminal 2
```

`run_robot_tier.sh` shares the bridge container's network namespace
(`--network container:dds_bridge…`) rather than joining a docker network. Both
stacks then see one loopback, which is what lets CycloneDDS discover across them
without multicast — and it is why the bridge has to be up first: a namespace
cannot be shared with a container that does not exist.

It refuses to start if the bridge is running its own kinematic robot. Both own
`ent:robot:tb3`, `already_published()` would make one of them lose, and which
one loses is a race.

About 20 s from launch to nav2's lifecycle nodes reaching active.

## How the keep-out becomes a costmap

```
Entity (DECLARED, MODEL_LATCHED)  ->  build_mask()  ->  OccupancyGrid
        on the SpatialDDS bus              |             /keepout_filter_mask
                                           |                    |
                                     spatialdds_demo.keepout    v
                                     (shared with the           nav2 KeepoutFilter
                                      kinematic fallback)       in both costmaps
```

`keepout_node.py` is one process wearing both hats: a DDS reader of
`TOPIC_MODEL_ENTITY_V1` and a ROS publisher. It reads declared entities, calls
`build_mask` from `spatialdds_demo/keepout.py` — the *same* function the
kinematic fallback uses, so the two robots cannot disagree about the bounds —
and publishes the mask plus a `CostmapFilterInfo` on latched QoS.

Three things in there are load-bearing and were each found the hard way:

- **No mask is not an empty mask.** If no declared entity has bounds, the node
  publishes nothing at all. An empty grid would say *everywhere is free*, which
  is the opposite of not knowing.
- **The static canvas is padded by 25 m** (`CANVAS_MARGIN_M`). `KeepoutFilter`
  only marks cells that fall inside the static layer's window, so a mask that
  fitted the pond exactly marked *nothing*: every keep-out cell was outside the
  map. The `/map` the node publishes is the mask's footprint plus margin.
- **The mask is dilated by the robot radius plus a margin.** nav2's
  `robot_radius: 0.22` inflates obstacles but not filter cells, so without the
  dilation the planner would happily graze the boundary.

The venue frame is the map frame: the plaza's origin *is* `map`, so no static
transform is needed between the model and the planner.

## What is in here

| File | What it is |
|---|---|
| `Dockerfile` | ROS Humble + nav2, and this repo's CycloneDDS 11.0.1 built from source beside ROS's 0.10.5 |
| `_bringup.sh` | in-container launch: sim, keep-out node, `robot_bridge --source ros`, four nav2 servers, lifecycle manager |
| `keepout_node.py` | declared bounds → costmap filter mask |
| `plaza_sim_node.py` | kinematic base: publishes `/odom` and tf, consumes `/cmd_vel` |
| `nav2_params.yaml` | NavFn global planner, DWB controller, `KeepoutFilter` in both costmaps |
| `_*_probe.py` | one-shot diagnostics — bounds, mask, costmap, drive, plan. Not part of the demo |

### No Gazebo, on purpose

Gazebo has no arm64 build for Humble, and emulating amd64 to run a physics
engine over a flat plaza would be a lot of machinery for very little. What the
demo needs from a robot is a pose that moves plausibly and a planner that
respects the model's declared bounds — so: real nav2, kinematic base beneath it.

Nothing here fakes a sensor, which is the whole argument of the keep-out
design. The robot avoids the water because the venue *declared* it, not because
a simulated lidar saw it. Policy, not perception.

## Two CycloneDDS libraries in one container

ROS Humble's `rmw_cyclonedds_cpp` links CycloneDDS 0.10.5. This repo's Python
binding is 11.0.1, and its generated types came from that `idlc`. Both are
installed: the binding loads its own `libddsc` from `/usr/local` and never
shares symbols with the rmw layer's. Built from source because there is no
aarch64 wheel for 11.0.1.

That coexistence is what makes wire-level interop possible at all — and an
earlier version of the Dockerfile missed it by simply not installing the
binding.

## Probes

Each runs inside the container against the live graph:

```bash
docker exec robot_tier bash -lc 'source /opt/ros/humble/setup.bash &&
    PYTHONPATH=/ws python3 /ws/robot_tier/_plan_probe.py'
```

`_bounds_probe` — what the model declares · `_mask_probe` — what became mask
cells · `_costmap_probe` — what nav2 actually believes · `_drive_probe` — does
`/cmd_vel` move it · `_plan_probe` — ask for a path across the water and see
where it goes.
