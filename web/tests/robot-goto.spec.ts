import { expect, test } from '@playwright/test';
import {
  BRIDGE_URL, container, readyPage, restoreVenue
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
    const model = await (await request.get(`${BRIDGE_URL}/v1/model`,
                                           { timeout: 4000 })).json();
    if (!(model.entities || []).some((e: any) => e.entity_id === 'ent:robot:tb3')) {
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
  const model = await (await request.get(`${BRIDGE_URL}/v1/model`)).json();
  const robot = (model.entities || []).find((e: any) => e.entity_id === 'ent:robot:tb3');
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
  await readyPage(page);
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id) === 'ent:robot:tb3');
  }, null, { timeout: 40_000 });

  const before = await robotPose(request);

  // Aim at a known point in the venue frame rather than at a screen pixel:
  // the assertion is about the command the tab publishes, and a pixel would
  // make it about the camera as well.
  const target: [number, number] = [24.0, -20.0];
  await page.evaluate(() => document.getElementById('btnSendRobot')?.click());
  const sent = await page.evaluate(async ([x, y]) => {
    const v = (window as any).__viewer;
    const Cartesian3: any = v.camera.position.constructor;
    // The same conversion the tap does, from a point we chose rather than one
    // the mouse found: local metres -> earth-fixed -> back through the tap.
    const model = await (await fetch('http://localhost:8088/v1/model')).json();
    const robot = model.entities.find((e: any) => e.entity_id === 'ent:robot:tb3');
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
    return true;
  }, target);
  expect(sent).toBe(true);

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
