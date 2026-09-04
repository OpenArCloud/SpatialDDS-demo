# Contributing to SpatialDDS-demo

We welcome contributions to this project! By contributing, you agree to the terms outlined below.

## License Agreement

By contributing code, documentation, or other materials to this project, you agree that your contributions will be licensed under the same MIT License that covers the project.

## Patent Grant

**Explicit Patent Grant**: By submitting a contribution to this project, you hereby grant to the project maintainers and to all recipients of this software a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable patent license to make, have made, use, offer to sell, sell, import, and otherwise transfer your contribution, where such license applies only to those patent claims licensable by you that are necessarily infringed by your contribution alone or by combination of your contribution with the work to which such contribution was submitted.

If you institute patent litigation against any entity (including a cross-claim or counterclaim in a lawsuit) alleging that any contribution to this project or the project itself constitutes direct or contributory patent infringement, then any patent licenses granted to you under this agreement for that contribution or project shall terminate as of the date such litigation is filed.

## How to Contribute

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test your changes (see the test matrix below)
5. Submit a pull request

## Test matrix

Most suites run on the host. The ones needing DDS, ROS 2 or a broker run in
containers.

**`scripts/run_tests.sh` runs them in three tiers**, and is the answer to
"did I break anything":

```bash
scripts/run_tests.sh            # fast     ~20s    host only
scripts/run_tests.sh standard   # + DDS    ~3min   adds the container suites
scripts/run_tests.sh full       # + ROS 2  ~25min  adds the ROS 2 and MQTT tiers
```

Run `standard` before pushing anything that touches `spatialdds_demo/`, a
bridge, or the IDL — three minutes covers every class of failure the host
suite structurally cannot see. Run `full` after touching the ROS 2 bridge.

The script prints PASS/FAIL per suite and exits non-zero if any failed, because
the alternative is what actually happened: a tier ran a file that had been
deleted, took the two tiers after it down with it, and nobody noticed for six
days while the host suite stayed green.

The table below is what those tiers run, if you want one of them alone.

| Suite | How to run | Needs |
|---|---|---|
| Unit + bridge logic | `python3 -m pytest multi_operator_fusion ar_demo/test_ar_demo_services.py bridges/ros2_bridge/test_conversions.py bridges/ros2_bridge/test_bridge_node.py bridges/mqtt_bridge bridges/web_bridge/test_router.py bridges/web_bridge/test_client.py bridges/web_bridge/test_dashboard_routes.py bridges/web_bridge/test_discovery_http.py bridges/web_bridge/test_wellknown_endpoints.py bridges/web_bridge/test_model_cache.py bridges/mcap_bridge tests/ nuscenes/test_nuscenes_shapes.py deepsense/test_deepsense_shapes.py` | host — **360 passed, 11 skipped** |
| Interop probe, both directions | `python3 -m unittest tests.test_interop` | Docker (needs `CYCLONEDDS_URI`) |
| Head-of-line isolation | `python3 -m unittest tests.test_head_of_line` | Docker (needs `CYCLONEDDS_URI`) |
| ROS 2 DDS round-trip | `python3 -m unittest bridges.ros2_bridge.test_dds_roundtrip` | Docker |
| MCAP record → replay | `python3 bridges/mcap_bridge/test_live.py` | Docker + `pip install mcap` |
| AR-demo protocol | `cd ar_demo && ./run_all_tests.sh` | host |
| Cesium web UI | `cd web && npm test` | host + Playwright browsers |
| Web bridge HTTP | `bash run_bridge_http_tests_docker.sh` | Docker |
| IDL compile + protocol | `docker run --rm cyclonedds-python` | Docker |
| ROS 2 bridge, all tiers | `bash bridges/ros2_bridge/run_docker_tests.sh` | Docker, emulates amd64 on Apple Silicon |
| MQTT bridge Tier-2 | `cd bridges/mqtt_bridge && docker compose -f docker-compose.test.yaml up --abort-on-container-exit --exit-code-from tests` | Docker + Mosquitto |

`Dockerfile`'s base image is not in the registry — only the superseded
`0.10.5-ubuntu22.04` tag was ever pushed, and that one is arm64-only. Build the
base locally first, on any host:

```bash
docker build -t ghcr.io/openarcloud/cyclonedds-python-base:11.0.1-ubuntu22.04 -f Dockerfile.base .
docker build -t cyclonedds-python .
```

The container suites all take the same shape:

```bash
docker run --rm -v "$PWD:/app" -w /app -e PYTHONPATH=/app \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \
  python3 -m unittest tests.test_interop
```

### Switches for exercising a path deliberately

Some paths only run when the demo cannot take a shortcut, and the shortcut is
usually a coincidence of the seed data rather than a design. Each of these
turns one off so a test proves the thing it claims to:

| Switch | Turns off | So that |
|---|---|---|
| `?noassetcache=1` | the catalogue rows the coverage query returned | `catalog:<id>` references must resolve through `content_id_in`. The duck's row and the duck's entity are in the same plaza, so the cached lookup always hits and the by-id path is otherwise never exercised |
| `?catalogpose=1` | model placement | the legacy catalogue-pose path can be compared side by side |
| `?basis=…` | entities whose basis is not named | a filtered view can be captured without editing what is published |

Add one whenever a demo works for a reason you cannot otherwise
distinguish from the reason you intend. Future content — the pond, Part W's
SCG nodes — will want the same switch as the model layer grows and more
references point outside the area a client happens to have queried.

### A guard isn't done until it has been seen to fail

A new test that asserts something important should be watched failing before
it is kept: break the thing it guards, confirm it goes red, put the thing
back. Mutation testing by hand, and the difference between a test and a
decoration — several checks in this repo were written, passed immediately,
and only later turned out to assert nothing. The cheapest moment to find that
out is while you still have the change in your head.

### A screenshot taken too early verifies the loading window, not the app

The AR demo's markers were being clamped to the ellipsoid, roughly 143 m
beneath Austin, because this deployment carries no terrain provider for
`CLAMP_TO_GROUND` to resolve against. The names still rendered in the right
place — until the 3D tiles finished loading and the clamp resolved, at which
point they silently dropped underground.

That bug survived several rounds of "verified by screenshot" because every one
of those screenshots was taken inside the loading window. It was caught by the
first capture that waited for `tilesLoaded` before shooting.

The lesson generalises past Cesium: a view that resolves asynchronously —
tiles, fonts, images, lazy-loaded panels, anything clamped or measured against
late-arriving data — looks correct in the interval before it settles. When
capturing evidence, wait on the condition rather than the clock, and be
suspicious of any check that passes faster than the thing it is checking.

Two related traps, both of which cost real time here:

- **Playwright serves the built bundle.** `web/playwright.config.ts` runs
  `npm run build && npm run preview` on port 4173 with `reuseExistingServer`.
  A dev server on 5173 is not what the specs are driving; rebuild before
  re-running a spec, or you will debug code that is not loaded.
- **A clamped entity lies about where it is.** `scene.cartesianToCanvasCoordinates`
  projects the entity's *stated* position while the primitive draws at the
  clamped one, so a probe can confidently report on-screen coordinates for
  something that is not on screen.

### A suite that finds a server it did not start will test that one

`run_bridge_http_tests.py` starts a VPS, a catalogue and a bridge, then talks
to `http://localhost:8088`. It never checked that the thing answering was the
thing it started. With a bridge left running from
`./run_bridge_server_docker.sh`, the suite bound its own servers to a port
already taken, then happily tested the *other* bridge: health ok, localize ok,
and a catalogue query returning a different deployment's content.

The failure that produced was specific, plausible and pointed at the wrong
place — `catalog missing expected content: [two ids]`, immediately after a
change to the catalogue filter, which was fine. It survived a bisect against
an older commit, because the stray bridge was still running for that too.

The suite now refuses to start when the port is occupied, and says which
command to run. Two habits are worth keeping from this:

- **Check what is listening before believing a live-stack failure.**
  `docker ps` and `curl localhost:8088/health` cost nothing.
- **Reproducing an "it fails here too" on an older commit proves nothing if
  the contamination is in the environment rather than the tree.** Bisecting
  found the same failure at the previous commit and I read that as
  "pre-existing" when it meant "still running".

### A determinism fix can hide a bug instead of fixing it

The live move test was made deterministic by resetting the venue before each
run, which was the right change and had a side effect nobody looked for: the
bug it was flaking over was a *durability* bug, and resetting first meant no
test ever asked what a reader joining later would see.

The move survived only as long as the tool that published it. Written from a
short-lived process, the new pose died with its writer while the service's
seed stayed latched, so a browser open at the time followed the duck and the
next one to load got it back at its starting position. Measured: fresh reader
`(6.50, -8.00)`, bridge cache `(3.00, -19.00)`. One duck, two positions, and a
green suite for weeks.

Resetting before each test is exactly how a durability bug hides from a
deterministic suite: both make the world reproducible, and only one of them
makes it correct. When a test is stabilised by controlling the starting state,
ask separately what a participant that *arrives afterwards* would observe --
that question is not asked by any test that begins by putting things back.

The guard added afterwards says it in its name:
`test_a_move_outlives_the_tool_that_asked_for_it`.

### Things that look like failures but aren't

- **Stale `.pyc` files across the host/container boundary.** The repo is
  bind-mounted into the demo image and the two run different Pythons, so
  bytecode written by one used to be read by the other as truncated — once as
  `EOFError: EOF read where object expected` from a service that was fine,
  once as a test failure that vanished on re-run. Both runners now set
  `PYTHONDONTWRITEBYTECODE=1`. If you invoke pytest directly and see something
  inexplicable after editing a module, clear `__pycache__` before believing it.

- **Don't run `pytest bridges` wholesale.** `test_integration.py` exists under
  both `multi_operator_fusion/` and `bridges/web_bridge/`. Both pass when run
  individually.
- **Duplicate module basenames are a live hazard.** `publisher.py` exists three
  times. `spatialdds_types.py` existed twice until a `sys.path` collision
  silently turned a whole test file into a no-op — it reported "converters
  unavailable" and *skipped*, while having imported the wrong module. The
  nuScenes/DeepSense one is `sensor_types.py` now, and tests that must not be
  shadowed import by file path. A skip that hides a failing test is worse than
  the failure.
- **`ar_demo` scripts are cwd-sensitive.** Run `spatialdds_demo_tests.py` from
  `ar_demo/` (it opens `catalog_seed.json` relatively) and
  `comprehensive_test.py` from the repo root (it compiles `idl/v1.7/*.idl`).
  Wrong directory looks like a failure and isn't.
- **The DDS suites skip loudly without `CYCLONEDDS_URI`.** cyclonedds will
  build a participant on a host with no usable networking config, so the gate
  is the environment variable rather than the import — otherwise they fail
  late and confusingly instead of skipping.
- Three `test_`-prefixed files aren't pytest modules, so "no tests ran" is
  correct: `ros2_bridge/test_mocks.py` is a mock library, and
  `mcap_bridge/test_live.py` and `test_with_deepsense.py` are scripts needing
  live DDS and the DeepSense dataset.
- **Real-time lanes drop samples on purpose.** `DET_RT`, `POSE_RT`,
  `LIDAR_RT`, `IMU_RT` and `RADAR_RT` are BEST_EFFORT per §3.3.3, so a burst
  loses some. A test asserting zero loss on one of those is asserting the
  wrong thing; assert it on a reliable lane.
- The two web specs skip each other by design; see `web/README.md`.
- `run_bridge_http_tests_docker.sh` can report `COVERAGE_RESPONSE timeout`. Its
  discovery query is sent once with no retry. Re-run it, or use the pytest
  variant.
- `idlc -l py` writes nothing for `ar_demo/spatialdds.idl` and ignores `-o`, and
  there's no C++ backend in the image. See `ar_demo/DOCKER_GUIDE.md` for the
  commands that do work.

## Code of Conduct

Please be respectful and professional in all interactions related to this project.

## Questions

If you have questions about contributing, please open an issue or contact the maintainers.