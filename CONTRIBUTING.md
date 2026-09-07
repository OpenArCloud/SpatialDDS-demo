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

**Watch the count, not just the colour.** Every part of this work reports
passed/skipped totals per commit, which reads like bookkeeping until it isn't.
Cleaning up the canonical list in Part 3 put a comment inside a
line-continuation, where `#` ate the rest of the logical line along with
`tests/` and 158 tests. The tier printed `3 passed, 0 failed` and was telling
the truth about what it ran. The only witness that a third of the suite had
vanished was the total dropping from 477 to 319.

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
| `?autostart=0` | the automatic localization on load | a test can drive the exchange itself without racing one, and a person can watch the request happen instead of finding it already done. The bridge serialises localize requests, so a suite where every open page asks for one queues behind itself |
| ~~`?noassetcache=1`~~ | *(retired in P3.0)* | It forced `catalog:<id>` references to resolve through `content_id_in`. Since the model bootstrap runs before any coverage query, there is no cache to hit and every reference resolves by id on every page load — so the switch had nothing left to switch. Retired rather than kept as a control that no longer controls anything |
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

### Two test files with the same basename silently disable one

pytest imports test modules by basename unless the directory is a package, so
`multi_operator_fusion/test_integration.py` and
`bridges/web_bridge/test_integration.py` cannot be collected in the same run:

    import file mismatch: imported module 'test_integration' has this
    __file__ attribute: .../multi_operator_fusion/test_integration.py

Whoever hit that first solved it by leaving one file out of the canonical
list, where it sat passing four tests that nobody ran.

The failure mode is nasty because the suite stays green and the count is
plausible. **Nothing announces the absence of tests that were never asked
for.** Every other kind of breakage shows up as red; this one shows up as
nothing at all, which is why the only defence is looking at what the run
actually collected rather than at whether it passed.

Give test files unique basenames across the repo, which is the fix pytest's
own hint suggests. And **don't name a helper `test_*`**: `test_mocks.py`
contained no tests at all, so pytest collected it, found nothing, and counted
it among the passing files. A helper that looks like a suite is worse than one
with an awkward name; it is now `ros2_mocks.py`.

### Derive fixtures from the seed, never from counting it

Seeding one entity broke ten tests. Not because the entity was wrong -- because
the suite had memorised the venue: entity totals written as `5`, and instance
handles written as `dispose_entity(104)`, which encoded the *seed order* into a
file three directories away from the seeder. Inserting the monument between the
pond and the ducks shifted every handle, and the failure read `expected
ent:duck:east, got ent:duck:west`, which points nowhere near the cause.

Counts come from `len(seed_entities())`. Handles come from a lookup by id.
Landmarks are named (`ent:pond:littlefield`), never indexed
(`seed_entities()[1]`).

**Every literal was a test waiting to fail for the wrong reason on the next
entity** -- and the venue has gained one in three of the last four parts. The
suite should expect growth.

### An optional publisher starting is not a reason for a duck test to fail

Three assertions in the stack suite hard-coded the size of the world: a total
entity count, which entities appear in each `?basis=` view, and how many
catalogue references resolved to how many ids. All three were true of one
configuration and broke the moment an optional service was running -- pondwatch
adds a pond, the robot bridge adds a robot, and a duck test failed because
there were eight things instead of seven.

Assert the property, not the census:

- the entities the venue *seeds* are present and where it put them, rather
  than a total;
- the basis views are **disjoint and complete** over whatever is there,
  rather than four fixed lists;
- every distinct content id **resolved**, rather than "three references over
  one id".

The landmarks can still be named -- the fountain is OBSERVED, the pond is
DECLARED -- but as members of a set, not as the whole of it.

### Assert the settled state, not the race to it

Three tests in this repo asserted something adjacent to what they meant, and
each failed in a way that pointed at the wrong thing:

- **Don't assert the RNG.** The wander test requires two of three ducks to
  have moved in an eight-second window, not three. The walk is random; a duck
  can spend a window stepping back and forth over the same metre.
- **Don't assert the log line.** The live-move test waited for
  `model:moved`, a proxy for a position. When the fast lane went deliberately
  silent, the proxy went false while the truth stayed true. It waits on the
  position now, which is what it was always about.
- **Don't assert the clock.** The two-tab test compared duck positions while
  the mover was running and called a 0.19 m difference a disagreement. Two
  windows sampled milliseconds apart legitimately differ by one in-flight
  update; the claim is that they *converge*. Stop the motion, let it settle,
  then compare — and then 1e-9 degrees is a fair tolerance.

The general form: **assert the settled state, not the race to it.** If a
property only holds once things stop, stop them. If it holds continuously,
assert the property and not a signal that happens to accompany it.

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

### One world, so one worker: files race even when their tests do not

`model-stack.spec.ts` collects its tests into a single serial file because
they share a venue, and its header says so. What that cannot do is keep a
*second file* out of the same world: Playwright runs files in parallel by
default, and the venue does not know which spec is talking to it.

It fired when a capture asked the robot for a journey to the far side of the
basin while `robot-goto.spec.ts` was taking a tap on the ground and sending
the same robot somewhere else. The capture reported

    journey: 442 samples from (22.0, -8.0) to (23.8, -19.7); 0 in the water

-- four hundred samples, no arrival, and a failure that pointed at the
journey rather than at the second writer. Two writers of one goal, which is
the same defect the robot bridge refuses at startup for `ent:robot:tb3`, at a
level the bridge cannot see.

The three specs that drive the running stack are now a Playwright project of
their own with `workers: 1`, which is that setting's documented purpose. The
rest of the suite still runs as wide as the machine allows. The whole run
went from 4.9 to 9.0 minutes, which is what a shared world costs when the
harness stops pretending there are several of them.

The general form: **serial-within-a-file is not isolation.** If two spec
files can reach the same running thing, they are one test suite, and the
harness has to say so.

### A test can pass because the world was already in the end state

The Part 4 journey capture drives the robot to a point and asserts it got
there without crossing the declared water. Its first green run reported:

    journey: 1 samples from (6.3, -20.1) to (6.3, -20.1); 0 in the water

A previous run had left the robot on the goal, so the arrival check fired on
the first sample, the "journey" was one reading of a stationary robot, and
the screenshot beside it showed a robot that had not moved. Green, and
illustrating nothing.

This is the determinism lesson from the section above wearing its plainest
costume. Restoring the venue makes a test reproducible; it does not make the
test *ask anything*. Here the fix was to make the journey a distance rather
than a state -- drive to the start, prove it arrived, assert the start is
more than ten metres from the goal, and assert the track is more than one
sample long:

    expect(track.length, 'a journey is more than one sample').toBeGreaterThan(20);

The general form: **when a test asserts that something happened, assert the
band it happened across, not the state it ended in.** An end state is
reachable by doing the work and by having already been there, and a passing
test cannot tell you which one it saw.

### Rendered look is untested surface; captures are its only gate

This suite asserts semantics and is deliberately blind to appearance. It
checks how many entities exist, where they are, what basis they claim, what
their labels say, and what the bus carried. It does not check what any of it
looks like, and that is the right division: an assertion about a colour is
usually an assertion about a decision already tested somewhere better.

The blind spot is real anyway, and three defects walked straight through it,
all of them in Part 4:

- **Every duck rendered white.** Setting `colorBlendMode: REPLACE` with no
  colour makes Cesium replace the model's colour with the default, which is
  white. Entity counts, positions, labels and bases were all still correct.
- **A reshaped pond kept its old box.** The ducks crowded into the new bounds
  and the drawn volume stayed the size of the old ones, because the update
  path carried positions and not dimensions. Every semantic test passed.
- **The watching tab never greyed the robot.** UNOBSERVED tinting is chosen
  where an entity is *drawn*, and an update only moved what was already on
  screen -- so a tab that watched a robot go silent kept drawing it live,
  while a tab opened a second later drew it grey. The two disagreed about the
  same latched sample, and the state assertions passed on both.

None of the three could have been caught by a test this suite would sensibly
write. All three were caught by taking a screenshot, and the first was caught
by a screenshot taken for an unrelated reason entirely.

So: **rendered look is untested surface, and captures are its only gate.**
That is the reason captures are part of acceptance here rather than
decoration on top of it -- they are not illustrations of work already
verified, they are the verification for a layer nothing else covers. When a
capture is taken, look at it. When appearance carries the claim -- a grey
robot, a shrinking box -- assert it in the capture spec, where it is the
subject rather than an incidental property.

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