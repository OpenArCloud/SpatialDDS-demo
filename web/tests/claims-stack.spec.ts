import { expect, test } from '@playwright/test';
import {
  BINDING_URL, SCENE, container, readyPage, sendCommand, venueEntities
} from './model-stack.helpers';

/**
 * The OWM 0.2 prototype's claims, as a person sees them, against the stack.
 *
 * The panel and the highlight, end to end: the binding serves the
 * relationship lane, the page subscribes to it, and what is on screen
 * follows what the asserters say. The pure half (which edges are in force,
 * which zone is lit) is claims-store.spec.ts, against a fixture; this is the
 * same thing through a real gateway, a real bus and a real robot.
 *
 * Skips on a probe of the lane itself, so it re-arms the day the stack runs
 * with claims, and the robot test also needs the robot publishing.
 */

const LANE = `${BINDING_URL}/${SCENE}/world/oarc.relationship/v1`;
const FOUNTAIN = 'ent:fountain:littlefield';
let stack: string | null = null;
let robot = false;

test.beforeAll(async ({ request }) => {
  stack = container();
  if (!stack) {
    return;
  }
  try {
    const r = await request.get(LANE);
    if (!r.ok() || !(await r.json()).length) {
      stack = null;
      return;
    }
    robot = (await venueEntities(request))
      .some((e: any) => e.entity_id === 'ent:robot:tb3' && e.state === 'ACTIVE');
  } catch {
    stack = null;
  }
});

test.beforeEach(() => {
  test.skip(stack === null, 'no relationship lane served — start the stack '
            + 'with the model layer (claims are on by default)');
});

const rows = (page: any) => page.locator('#claimsOverlayBody table').first()
  .locator('tr[data-claim-id]');

test('the panel lists every asserter, and the filter shows one', async ({ page }) => {
  await readyPage(page);
  // The table is a detail view of the overlay, which is on by default.
  await expect(page.locator('#btnToggleClaims')).toHaveText('Claims: On');
  await page.locator('#btnClaimsTable').click();
  // The declared edges are always there: two part-of, three ducks within.
  await expect.poll(async () => rows(page).count(), { timeout: 20_000 })
    .toBeGreaterThanOrEqual(5);
  const asserters = await page.locator('#claimsAsserter option')
    .evaluateAll((os: HTMLOptionElement[]) => os.map((o) => o.value));
  expect(asserters).toEqual(expect.arrayContaining(
    ['', 'svc:model:demo/venue', 'svc:mover:demo/ducks']));
  await page.locator('#claimsAsserter').selectOption('svc:model:demo/venue');
  await expect(rows(page)).toHaveCount(2);
  for (const text of await rows(page).allInnerTexts()) {
    expect(text).toContain('part of');
    expect(text).toContain('DECLARED');
  }
});

test('the robot in the fountain: two rows, and the fountain lit', async ({ page, request }) => {
  test.skip(!robot, 'no robot publishing — run scripts/run_robot_tier.sh');
  // Two legs of up to 180 s each, from wherever the previous robot spec
  // left it: 240 s was measured too little in the full suite.
  test.setTimeout(480_000);
  await readyPage(page);
  await page.locator('#btnClaimsTable').click();
  const lit = () => page.evaluate((id: string) => {
    const box = (window as any).__viewer.entities.getById(`${id}-extent`)?.box;
    return box ? box.outlineWidth.getValue() : null;
  }, FOUNTAIN);

  const near = (x: number, y: number) => async () => {
    const r = (await venueEntities(request))
      .find((e: any) => e.entity_id === 'ent:robot:tb3');
    return Math.hypot(r.pose.t[0] - x, r.pose.t[1] - y);
  };
  // Arrived means nav2 has finished, and the honest signal for that here is
  // the robot standing still: within 1.5 m is not nav2's tolerance. A goal
  // sent while nav2 is still finishing the last one is a preemption, and
  // measured on this tier nav2 then reported the *new* goal succeeded at the
  // old goal's position ("Reached the goal!" in the same tick), so the robot
  // never moved and nothing said so.
  const settled = (x: number, y: number) => async () => {
    const a = await near(x, y)();
    await new Promise((r) => setTimeout(r, 3000));
    const b = await near(x, y)();
    return a < 1.5 && Math.abs(a - b) < 0.05;
  };
  const within = async () => (await rows(page).allInnerTexts())
    .filter((t: string) => t.includes('within') && t.includes(FOUNTAIN)
            && t.includes('ent:robot:tb3')).length;

  // Each leg waits for the robot to arrive before asserting anything, and
  // before the next goal. Both orders were measured wrong. Asserted on the
  // way, the rows appear and vanish at the fountain box's south-east corner,
  // which nav2's route crosses (the box's east edge is x = 25.81, the route
  // x = 25.8): a race. And a goal sent mid-route at that corner leaves the
  // robot inside the keep-out's shoulder, where nav2 plans nothing and every
  // later goal is abandoned (the refusal reaches the record), so one failure
  // poisons every rerun until the tier restarts.
  const north = await sendCommand(request, 'goto', 'ent:robot:tb3',
                                  { t: [12, -2, -0.84], q: [0, 0, 0, 1] });
  expect((await north.json()).outcome, await north.text()).toBe('accepted');
  await expect.poll(settled(12, -2), { timeout: 180_000, intervals: [1000] })
    .toBe(true);
  // Both zone watches, as two rows about one proposition.
  await expect.poll(within, { timeout: 10_000 }).toBe(2);
  await expect.poll(lit, { timeout: 10_000 }).toBe(4);

  // Back to the start: both retire, with their reason, and the light goes out.
  const back = await sendCommand(request, 'goto', 'ent:robot:tb3',
                                 { t: [17, -23, -0.84], q: [0, 0, 0, 1] });
  expect((await back.json()).outcome, await back.text()).toBe('accepted');
  await expect.poll(settled(17, -23), { timeout: 180_000, intervals: [1000] })
    .toBe(true);
  await expect.poll(within, { timeout: 10_000 }).toBe(0);
  await expect.poll(lit, { timeout: 10_000 }).toBe(2);
  const retired = await page.locator('#claimsOverlayBody tr.retired').allInnerTexts();
  expect(retired.some((t: string) => t.includes(FOUNTAIN)
                      && t.includes('no longer holds'))).toBe(true);
});
