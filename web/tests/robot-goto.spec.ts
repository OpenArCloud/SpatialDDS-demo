import { expect, test } from '@playwright/test';
import {
  BINDING_URL, SCENE, browserCanCommand, container, readyPage, restoreVenue,
  venueEntities
} from './model-stack.helpers';

/**
 * Tap-to-send, against the running stack.
 *
 * Skips unless the robot bridge is publishing. The claim is that a click in a
 * browser and a command from a terminal are the same act: the tab publishes
 * on the same lane, with the same verb, and the robot moves because the model
 * changed -- not because the page told it to.
 */

let stack: string | null = null;

test.beforeAll(async ({ request }) => {
  stack = container();
  if (!stack) {
    return;
  }
  try {
    if (!(await venueEntities(request))
           .some((e: any) => e.entity_id === 'ent:robot:tb3')) {
      stack = null;
    }
  } catch {
    stack = null;
  }
});

test.beforeEach(async ({ request }) => {
  test.skip(stack === null,
            'no robot publishing — run with SPATIALDDS_ROBOT_SIM=1');
  await restoreVenue(request, stack as string);
});

const robotPose = async (request: any): Promise<[number, number]> => {
  const robot = (await venueEntities(request))
    .find((e: any) => e.entity_id === 'ent:robot:tb3');
  return [robot.pose.t[0], robot.pose.t[1]];
};

test('the mode is off until asked for, and says so', async ({ page }) => {
  await readyPage(page);
  const button = page.locator('#btnSendRobot');
  await expect(button).toHaveText('Send Robot: Off');
  await button.click();
  await expect(button).toHaveText('Send Robot: On');
  const logs: string[] = await page.evaluate(() => (window as any).__appLogs || []);
  expect(logs.some((l) => l.startsWith('robot:send on'))).toBe(true);
});

test('a tap on the ground sends the robot there', async ({ page, request }) => {
  test.setTimeout(300_000);
  // The browser's half of N.6 is unreachable until the gateway answers the
  // CORS preflight an `Authorization` header forces. Skipped with the reason
  // rather than failed, and re-armed automatically when that changes:
  // docs/browser-credential-finding.md, blocker B.
  test.skip(!(await browserCanCommand(request)),
            'the binding answers no CORS preflight, so a cross-origin page '
            + 'cannot POST a credentialed command — blocker B');
  await readyPage(page);
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id) === 'ent:robot:tb3');
  }, null, { timeout: 40_000 });

  const before = await robotPose(request);

  // Aim at a known point in the venue frame rather than at a screen pixel:
  // the assertion is about the command the tab publishes, and a pixel would
  // make it about the camera as well.
  //
  // **Derived from the venue, not written down.** This was `[24.0, -20.0]`
  // from 2026-09-06 until 2026-10-18, and `749243e` enlarged the declared
  // pond the very next day -- from (9.5,-18)..(20,-10) to
  // (-1.5,-20)..(25.5,-4.75) -- which put that point exactly on the water's
  // southern edge. The robot drives straight lines and declines declared
  // cells, so it stalled about 5.8 m short and this test could not pass. It
  // went unnoticed for five weeks because it skips unless the robot is
  // publishing, and the stack is not normally launched that way: a test that
  // never runs is not a test that passes.
  //
  // So the target is now computed from the extents the venue is currently
  // declaring, with a margin, and asserted clear of all of them. The next
  // time the water changes shape this follows it instead of rotting.
  const declared = (await venueEntities(request))
    .filter((e: any) => e.basis === 'DECLARED' && e.extent);
  const MARGIN = 4.0;              // comfortably outside the inflated mask
  const southOf = Math.min(...declared.map((e: any) => e.extent.min_xyz[1]));
  const target: [number, number] = [22.0, southOf - MARGIN];
  for (const e of declared) {
    const [nx, ny] = e.extent.min_xyz;
    const [xx, xy] = e.extent.max_xyz;
    expect(target[0] < nx || target[0] > xx || target[1] < ny || target[1] > xy,
           `the target must be outside ${e.entity_id}'s declared extent`)
      .toBe(true);
  }
  console.log(`  target (${target[0]}, ${target[1].toFixed(2)}) — `
    + `${MARGIN} m clear of the southernmost declared extent (${southOf})`);
  await page.evaluate(() => document.getElementById('btnSendRobot')?.click());
  const sent = await page.evaluate(async ([x, y, bindingUrl, scene]: any[]) => {
    const v = (window as any).__viewer;
    const Cartesian3: any = v.camera.position.constructor;
    // The same conversion the tap does, from a point we chose rather than one
    // the mouse found: local metres -> earth-fixed -> back through the tap.
    // From the page's own origin, cross-origin to the binding -- which works
    // only because these lanes are classified `public-cacheable` and so carry
    // `Access-Control-Allow-Origin`. An operator-scoped lane would not be
    // readable here at all: docs/browser-credential-finding.md, blocker B.
    const entities = await (await fetch(
      `${bindingUrl}/${scene}/world/entity/v1`)).json();
    const robot = entities.find((e: any) => e.entity_id === 'ent:robot:tb3');
    const frames = (await (await fetch('http://localhost:8088/v1/frames')).json()).frames;
    const frame = frames[robot.frame_ref.fqn];
    const [qx, qy, qz, qw] = frame.pose.q;
    const rot = (q: number[], v: number[]) => {
      const [a, b, c, d] = q;
      const t = [2 * (b * v[2] - c * v[1]), 2 * (c * v[0] - a * v[2]),
                 2 * (a * v[1] - b * v[0])];
      return [v[0] + d * t[0] + (b * t[2] - c * t[1]),
              v[1] + d * t[1] + (c * t[0] - a * t[2]),
              v[2] + d * t[2] + (a * t[1] - b * t[0])];
    };
    const local = [x, y, robot.pose.t[2]];
    const e = rot([qx, qy, qz, qw], local);
    const ecef = new Cartesian3(e[0] + frame.pose.t[0], e[1] + frame.pose.t[1],
                                e[2] + frame.pose.t[2]);
    await (window as any).__sendRobotTo(ecef);
    // What the page says it did, not the fact that it returned. `sendRobotTo`
    // bails early -- no frame, no credential -- by logging and returning, so
    // `return true` here reported success for a command that was never sent:
    // on 2026-10-18 this read green while the build carried no token and the
    // POST never happened. The log line is the only observable that
    // distinguishes the two.
    return ((window as any).__appLogs || [])
      .filter((l: string) => l.startsWith('robot:goto') ||
                             l.startsWith('robot:send'))
      .slice(-1)[0] || 'nothing logged';
  }, [target[0], target[1], BINDING_URL, SCENE]);
  expect(sent, 'the page must report the bridge accepted the command')
    .toContain('accepted');

  // It moves, and towards the point that was asked for.
  await expect.poll(async () => {
    const [x, y] = await robotPose(request);
    return Math.hypot(x - target[0], y - target[1]);
  }, { timeout: 120_000 }).toBeLessThan(1.5);

  const [x, y] = await robotPose(request);
  console.log(`  asked for (${target[0]}, ${target[1]}), arrived at ` +
              `(${x.toFixed(2)}, ${y.toFixed(2)}) — from ` +
              `(${before[0].toFixed(2)}, ${before[1].toFixed(2)})`);
});
