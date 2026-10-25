import { execSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import {
  container, inContainer, readyPage, restoreVenue, look,
  moverRunning, startMover, stopMover, venueEntities, sendCommand
} from './model-stack.helpers';

/**
 * Part 4's set pieces, each backed by the assertion it illustrates.
 *
 * A screenshot proves that something was on screen at a moment; it cannot
 * prove why. So every capture here is taken beside a claim measured off the
 * bus, per the lesson from P2.3 -- a picture taken inside a loading window
 * verifies the window.
 *
 * Skips unless the robot tier is up: nav2, the keep-out node and the bridge
 * all have to be running for any of this to mean anything.
 */

const OUT = process.env.P44_OUT || join(tmpdir(), 'spatialdds-robot');
const TIER = 'robot_tier';
// ROBOT_START_XY in spatialdds_demo/plaza.py, copied rather than imported
// because this is TypeScript reading a Python constant.
//
// The previous note here claimed a drift would show up as a failure. It does
// not, and this copy had already drifted: it read (20, -22) while the Python
// constant was (17, -21.5), and nothing anywhere noticed. What the test below
// actually asserts is that the robot *arrives* at whatever this says, which
// is true of any drivable point -- so the copy is unenforced, and plaza.py is
// the source if the two disagree again.
const START: [number, number] = [17.0, -23.0];
// The far side of the water. A straight line from START to here crosses
// the declared pond end to end, so the only legal route is round one end
// of it -- which is the journey worth filming, and the thing that changes
// when the venue changes its mind about where the water is.
const GOAL: [number, number] = [6.0, -3.0];
let stack: string | null = null;

/**
 * Run something inside the robot tier with ROS and the bus on the path.
 *
 * The domain comes from the container's own environment rather than being
 * repeated here. CYCLONEDDS_URI does not: it is not in the image's
 * environment, and pointing ROS's middleware at a missing config file is
 * worse than leaving it unset -- the failure arrives as "rmw handle is
 * invalid", wearing another subsystem's error message.
 */
function inTier(command: string, detach = false): string {
  return execSync(
    `docker exec ${detach ? '-d ' : ''}${TIER} bash -c `
    + `'source /opt/ros/humble/setup.bash && `
    // Appended, not assigned: setup.bash puts rclpy on PYTHONPATH, and
    // overwriting it takes ROS off the path in a way that reports itself
    // as a missing rclpy three commands later.
    + `export PYTHONPATH=/ws:$PYTHONPATH && `
    + `export SPATIALDDS_TRANSPORT=dds && `
    + `export CYCLONEDDS_URI=file:///etc/cyclonedds.xml && ${command}'`,
    { stdio: ['ignore', 'pipe', 'ignore'] }).toString();
}

/**
 * What the global costmap currently forbids, in cells.
 *
 * The standing gate for this part: nothing may be claimed about a path until
 * the costmap has been asked what it thinks. A mask published, received and
 * logged is not a mask the planner is using -- that gap is where a 25.4 m
 * detour was nearly reported as a demonstration while the costmap was empty.
 */
function lethalCells(): number {
  const out = inTier('python3 /ws/robot_tier/_costmap_probe.py');
  const match = /global:.*?(\d+) lethal cells/.exec(out);
  return match ? Number(match[1]) : -1;
}

// The tier's robot is `spatialdds_ros.robot_node` since the ROS adoption
// (d6413d7). This probe looked for `spatialdds_demo.robot_bridge`, the
// process that replaced, until 2026-10-25: it never matched again, and the
// three tests below skipped as "robot tier not running" with the tier up.
function tierRunning(): boolean {
  try {
    const out = execSync(
      "docker exec robot_tier pgrep -c -f 'spatialdds_ros.robot_node' 2>/dev/null",
      { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim();
    return Number(out) > 0;
  } catch {
    return false;
  }
}

async function robotOnBus(request: any): Promise<[number, number] | null> {
  const r = (await venueEntities(request))
    .find((e: any) => e.entity_id === 'ent:robot:tb3');
  return r ? [r.pose.t[0], r.pose.t[1]] : null;
}

async function sendGoto(request: any, x: number, y: number) {
  // The navigator plans in 2D and ignores the z, but it should still be the
  // ground rather than a number nothing stands on: GROUND_Z in
  // spatialdds_demo/plaza.py.
  return sendCommand(request, 'goto', 'ent:robot:tb3',
                     { t: [x, y, -1.15], q: [0, 0, 0, 1] });
}

// POND_MIN/POND_MAX in spatialdds_demo/model_service.py: the box the venue
// declares around the water, which the robot is kept out of.
const inPond = (p: [number, number]) =>
  p[0] >= -1.5 && p[0] <= 25.5 && p[1] >= -20.0 && p[1] <= -4.75;

test.describe.configure({ mode: 'serial' });

let moverWasRunning = false;

test.beforeAll(async () => {
  stack = container();
  if (!stack || !tierRunning()) {
    stack = null;
    return;
  }
  // Start from a still world however the stack was launched -- the mover
  // runs by default now, and `restoreVenue` waits for ducks to be back at
  // their seeded poses, which never happens while something is moving them.
  // Borrowed, not inherited: put it back afterwards, because stopping the
  // motion on a stack somebody is demonstrating is how a suite becomes the
  // reason a demo looks broken.
  moverWasRunning = moverRunning(stack);
  stopMover(stack);
});

test.afterAll(async () => {
  if (stack && moverWasRunning) {
    startMover(stack);
  }
});

test.beforeEach(async ({ request }) => {
  test.skip(stack === null, 'robot tier not running — see robot_tier/README');
  await restoreVenue(request, stack as string);
  inContainer(stack as string, 'python3 scripts/reshape_pond.py --restore');
  inContainer(stack as string, 'python3 scripts/reshape_pond.py '
              + '--entity ent:shallows:littlefield --restore');
});

test('a journey around the water, with the law in the costmap', async ({ page, request }) => {
  test.setTimeout(420_000);
  await readyPage(page);
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id) === 'ent:robot:tb3');
  }, null, { timeout: 60_000 });
  await look(page);

  // Put it back at the start first, and prove it got there. Without this the
  // test passes trivially whenever a previous run left the robot on the goal:
  // the arrival check fires on the first sample, the journey is one sample
  // long, and a screenshot of a stationary robot illustrates nothing.
  await sendGoto(request, START[0], START[1]);
  await expect.poll(async () => {
    const p = await robotOnBus(request);
    return p ? Math.hypot(p[0] - START[0], p[1] - START[1]) : 99;
  }, { timeout: 180_000, intervals: [500] }).toBeLessThan(1.5);

  const before = (await robotOnBus(request))!;
  expect(Math.hypot(before[0] - GOAL[0], before[1] - GOAL[1]),
         'the journey must start somewhere other than its destination')
    .toBeGreaterThan(10);
  await page.screenshot({ path: join(OUT, 'robot-1-before.png') });
  await sendGoto(request, GOAL[0], GOAL[1]);

  // Sampled off the bus while it drives: the picture shows a robot somewhere,
  // the samples show where it has been.
  const track: [number, number][] = [];
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const p = await robotOnBus(request);
    if (p) {
      track.push(p);
      if (Math.hypot(p[0] - GOAL[0], p[1] - GOAL[1]) < 1.0) break;
    }
    await page.waitForTimeout(400);
  }
  await page.screenshot({ path: join(OUT, 'robot-2-after.png') });

  const wet = track.filter(inPond);
  console.log(`  journey: ${track.length} samples from `
    + `(${before[0].toFixed(1)}, ${before[1].toFixed(1)}) to `
    + `(${track[track.length - 1][0].toFixed(1)}, `
    + `${track[track.length - 1][1].toFixed(1)}); ${wet.length} in the water`);
  expect(track.length, 'a journey is more than one sample').toBeGreaterThan(20);
  expect(wet.length, 'the robot must not cross the declared water').toBe(0);
  expect(Math.hypot(track[track.length - 1][0] - GOAL[0],
                    track[track.length - 1][1] - GOAL[1])).toBeLessThan(1.5);
});

// `a duck is a decoration, not an obstacle` stood here until 2026-10-18. It
// read `GET /v1/keepout`, which retired with the bridge's pre-1.8 model
// readers -- and the route's docstring claimed a ROS consumer it no longer
// had: `robot_tier/keepout_node.py` joins the bus directly on
// TOPIC_MODEL_ENTITY_V1 and calls `build_mask` itself, so nothing fetched the
// mask over HTTP by the time it went.
//
// The claim is not lost, and it was never well made here: this venue's
// AUTHORED entities have no extent at all, so "AUTHORED contributes nothing"
// passed trivially against them. It is asserted properly in
// `tests/test_keepout.py::test_an_authored_region_contributes_nothing`, which
// gives an AUTHORED entity a box first and so can actually fail.

test('the venue changes its mind about the water, mid-journey',
     async ({ page, request }) => {
  /**
   * `reshape_pond.py` sends one `set_extent` and stops. Nothing in it knows
   * nav2 exists; the path bends because the keep-out node rebuilds its mask
   * from the venue's declaration, and nav2 replans against that.
   *
   * The ducks do *not* move, and that is asserted here rather than left to
   * be noticed. They read the shallows -- the venue's separate claim about
   * where its ducks go -- so a change to the water is not addressed to them.
   * One model, two claims, two consumers, and each one answers the claim it
   * was reading. (The ducks have their own version of this: shrink the
   * shallows and they crowd while the robot's route is untouched. It is in
   * model-stack.spec.ts.)
   *
   * The timing is rehearsed rather than hoped for: the mask repaints in
   * about three seconds, so the change has to land while there is still
   * route left to bend.
   */
  test.setTimeout(600_000);
  await readyPage(page);
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id) === 'ent:robot:tb3');
  }, null, { timeout: 60_000 });
  await look(page);

  startMover(stack as string);
  try {
    await sendGoto(request, START[0], START[1]);
    await expect.poll(async () => {
      const p = await robotOnBus(request);
      return p ? Math.hypot(p[0] - START[0], p[1] - START[1]) : 99;
    }, { timeout: 180_000, intervals: [500] }).toBeLessThan(1.5);

    const declared = lethalCells();
    expect(declared, 'the seeded water must already be law in the costmap')
      .toBeGreaterThan(5000);
    const drawnBox = () => page.evaluate(() => {
      const b = (window as any).__viewer
        ?.entities.getById('ent:pond:littlefield-extent');
      const d = b?.box?.dimensions?.getValue();
      return d ? [d.x, d.y, d.z] : null;
    });
    const boxBefore = await drawnBox();
    expect(boxBefore, 'the declared water should be drawn as a volume')
      .not.toBeNull();

    await sendGoto(request, GOAL[0], GOAL[1]);
    const track: [number, number][] = [];
    let fired = -1;
    const deadline = Date.now() + 240_000;
    while (Date.now() < deadline) {
      const p = await robotOnBus(request);
      if (p) {
        track.push(p);
        // Halfway up a side walkway, heading north, with the whole northern
        // leg of the detour still ahead of it. Rehearsed, because this
        // trigger has been wrong three times -- each time it was tuned to a
        // route that later moved, and each time the failure was a capture
        // that fired nothing and asserted nothing. The third: it keyed on the
        // east walkway (x = 26), and the two detours are near enough equal
        // that nav2 now takes the west one (x = -2.6) from where the robot
        // stops short of START, measured 2026-10-25. So it keys on the part
        // of the route that is the same every run whichever side: the robot
        // passes the pond to the east or to the west on its way north, or it
        // does not go at all. Shrinking the pond about its centre gives water
        // back on both flanks, so the bend is there to see on either.
        if (fired < 0 && (p[0] > 25.0 || p[0] < -1.5) && p[1] > -15.0) {
          await page.screenshot({ path: join(OUT, 'robot-3-declared.png') });
          inContainer(stack as string,
                      'python3 scripts/reshape_pond.py --shrink 0.4');
          fired = track.length;
        }
        if (fired > 0 && track.length === fired + 18) {
          // The mask has repainted and the route has been replanned by now.
          await page.screenshot({ path: join(OUT, 'robot-4-reshaped.png') });
        }
        if (fired > 0 && Math.hypot(p[0] - GOAL[0], p[1] - GOAL[1]) < 1.0) break;
      }
      await page.waitForTimeout(400);
    }
    expect(fired, 'the law never changed — the trigger point was never reached')
      .toBeGreaterThan(0);

    // 1. The costmap moved, live, in the middle of a journey. This is the
    //    assertion the part turns on: a mask that repaints but that the
    //    planner is not reading would pass every other check here.
    const reshaped = lethalCells();
    expect(reshaped, 'the smaller pond must forbid fewer cells')
      .toBeLessThan(declared / 2);

    // 2. The ducks are where the shallows say, and the shallows did not
    //    change. A command addressed to the water is not addressed to them.
    const entities = await venueEntities(request);
    const pond = entities
      .find((e: any) => e.entity_id === 'ent:pond:littlefield');
    const [nx, ny] = pond.extent.min_xyz;
    const [xx, xy] = pond.extent.max_xyz;
    const shallows = entities
      .find((e: any) => e.entity_id === 'ent:shallows:littlefield');
    expect(shallows.extent.min_xyz.slice(0, 2),
           'the shallows are a different claim and must be untouched')
      .toEqual([9.5, -18.0]);
    const ducks = entities
      .filter((e: any) => e.entity_id.startsWith('ent:duck:'));
    for (const duck of ducks) {
      const [x, y] = duck.pose.t;
      expect(x, `${duck.entity_id} left the shallows`)
        .toBeGreaterThan(shallows.extent.min_xyz[0] - 0.2);
      expect(x).toBeLessThan(shallows.extent.max_xyz[0] + 0.2);
      expect(y).toBeGreaterThan(shallows.extent.min_xyz[1] - 0.2);
      expect(y).toBeLessThan(shallows.extent.max_xyz[1] + 0.2);
    }

    // 3. The box on screen is the box in the model. This is an assertion
    //    about appearance, which the suite otherwise avoids -- but here the
    //    shape *is* the claim, and the update path carried positions only:
    //    the ducks crowded correctly into bounds the picture still drew at
    //    their old size, and every semantic test passed while it did.
    const boxAfter = await drawnBox();
    expect(boxAfter![0], 'the drawn water did not follow the declared water')
      .toBeLessThan(boxBefore![0] - 1.0);
    expect(boxAfter![0]).toBeCloseTo(xx - nx, 1);
    expect(boxAfter![1]).toBeCloseTo(xy - ny, 1);

    // 4. The robot drove through water that was law a moment ago, and
    //    through none of the water that is law now. Both halves are needed:
    //    the first is the bend, the second is that it still obeys.
    const after = track.slice(fired);
    const wasWater = after.filter(inPond);
    const isWater = after.filter(([x, y]) =>
      x >= nx && x <= xx && y >= ny && y <= xy);
    console.log(`  reshape: ${declared} -> ${reshaped} lethal cells; the water `
      + `is now x ${nx.toFixed(1)}..${xx.toFixed(1)}, `
      + `y ${ny.toFixed(1)}..${xy.toFixed(1)}; `
      + `${ducks.length} ducks still in the shallows`);
    console.log(`  path: ${wasWater.length} of ${after.length} samples after `
      + `the change are inside the old bounds, ${isWater.length} inside the new`);
    expect(wasWater.length,
           'the route never used the water the venue gave back').toBeGreaterThan(0);
    expect(isWater.length, 'the robot entered water that is still declared').toBe(0);
  } finally {
    stopMover(stack as string);
    inContainer(stack as string, 'python3 scripts/reshape_pond.py --restore');
  }
});

test('the sim dies, and both the watching tab and a late one are told',
     async ({ page, request, browser }) => {
  /**
   * UNOBSERVED is not absence, and there are two claims here rather than one.
   *
   * The tab that is watching must grey the robot where it stands, because
   * the entity is republished with a state and a reason and a client that
   * kept drawing it live would be showing a stale pose as a current one.
   *
   * The tab that arrives *during* the outage must show the same thing --
   * and nothing about that follows from having been present. It works
   * because the bridge republishes on a latched topic, so the last thing
   * anyone knew is still there to be joined to. A client that only learned
   * from the live stream would show a late-joiner an empty plaza.
   */
  test.setTimeout(600_000);
  // This failed on spatialdds_ros 0.3.0 and 0.4.0: their nav2 node read the
  // latest TF with no age check, so a dead sim stayed ACTIVE, restamped every
  // few seconds at its last pose (spatialdds-ros finding 10). It was marked
  // test.fail until 0.4.1, which fixed it; the mark's flip to "Expected to
  // fail, but passed" was that release's acceptance.
  const onBus = async () => (await venueEntities(request))
    .find((e: any) => e.entity_id === 'ent:robot:tb3');
  const drawnRobot = async (p: any) => {
    await p.waitForFunction(() => {
      const v = (window as any).__viewer;
      return v && v.entities.values.some((e: any) => String(e.id) === 'ent:robot:tb3');
    }, null, { timeout: 60_000 });
  };
  const tint = (p: any) => p.evaluate(() => {
    const m = (window as any).__viewer?.entities.getById('ent:robot:tb3-model');
    return m?.model?.color ? String(m.model.color.getValue()) : null;
  });
  /**
   * What the info panel is showing, read from inside Cesium's iframe.
   *
   * `page.locator('.cesium-infoBox-description')` finds nothing here and
   * waits for the whole test timeout while doing it: the InfoBox renders its
   * description inside an iframe, so the panel that is plainly visible in
   * the screenshot is invisible to a locator on the top-level document.
   */
  const panelText = (p: any) => p.frameLocator('.cesium-infoBox-iframe')
    .locator('.cesium-infoBox-description').innerText({ timeout: 15_000 });
  const select = (p: any) => p.evaluate(() => {
    const v = (window as any).__viewer;
    v.selectedEntity = v.entities.getById('ent:robot:tb3-model')
      || v.entities.getById('ent:robot:tb3');
  });

  await readyPage(page);
  await drawnRobot(page);
  await look(page);
  expect((await onBus()).state, 'it should be alive to start with').toBe('ACTIVE');
  expect(await tint(page), 'a live robot must not be drawn grey').toBeNull();

  let restarted = false;
  try {
    execSync(`docker exec ${TIER} pkill -f plaza_sim_node.py`, { stdio: 'ignore' });

    // The threshold is derived from the feed's own cadence, so this waits for
    // the bridge to decide rather than for a number written down here.
    await expect.poll(async () => (await onBus())?.state,
                      { timeout: 90_000, intervals: [1000] }).toBe('UNOBSERVED');
    const silent = await onBus();
    expect(silent.state_reason).toContain('last seen');

    // The tab that watched it happen -- no reload. Before the update path
    // learned to carry a state change, this stayed live-coloured forever and
    // only a fresh tab ever showed the grey.
    await expect.poll(() => tint(page), { timeout: 30_000 }).not.toBeNull();
    await select(page);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: join(OUT, 'robot-5-unobserved.png') });
    expect(await panelText(page),
           'the watching tab must say why, not just go grey').toContain('last seen');

    // The tab that arrives afterwards, in its own context so it shares
    // nothing with the first: no cache, no storage, and no live stream it
    // could have been listening to when the robot went quiet.
    const late = await browser.newContext();
    const latePage = await late.newPage();
    try {
      await readyPage(latePage);
      await drawnRobot(latePage);
      await look(latePage);
      await select(latePage);
      await latePage.waitForTimeout(1500);
      await latePage.screenshot({ path: join(OUT, 'robot-6-late-join.png') });
      expect(await tint(latePage),
             'the late-joiner drew it as though it were live').not.toBeNull();
      expect(await panelText(latePage),
             'the reason has to reach the person, not just the bus')
        .toContain('last seen');
      console.log(`  late join during the outage: ${silent.state_reason}`);
    } finally {
      await late.close();
    }

    // And back: the sim returns, poses resume, the grey comes off the tab
    // that never reloaded.
    inTier('python3 /ws/robot_tier/plaza_sim_node.py', true);
    restarted = true;
    await expect.poll(async () => (await onBus())?.state,
                      { timeout: 120_000, intervals: [1000] }).toBe('ACTIVE');
    await expect.poll(() => tint(page), { timeout: 30_000 }).toBeNull();
    await page.evaluate(() => { (window as any).__viewer.selectedEntity = undefined; });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: join(OUT, 'robot-7-recovered.png') });
  } finally {
    if (!restarted) {
      inTier('python3 /ws/robot_tier/plaza_sim_node.py', true);
    }
  }
});
