import { expect, test } from '@playwright/test';
import { BINDING_URL, SCENE } from './model-stack.helpers';

/**
 * A refusal the page was told about has to reach the person who asked.
 *
 * `goto` is accepted onto the bus before anyone can know whether it is
 * drivable: whether a straight line crosses declared water depends on where
 * the robot is when it meets the boundary. So the acceptance is honest and
 * the refusal arrives later, on the robot's own latched record as
 * `demo.nav_status`.
 *
 * Before this existed the page said `robot:goto accepted` and then nothing.
 * The robot's reason went to its stdout on the server, which a browser cannot
 * read -- so a tap on the far lawn looked ignored, and the only way to learn
 * why was to open a container log. That is the gap this covers, from the
 * page's side.
 *
 * The record is injected rather than driven, because the half under test is
 * "the page surfaces what the record says". Whether the robot publishes it is
 * the Python suite's question (tests/test_robot_bridge.py), and a spec that
 * needed both would prove neither on its own.
 */
const REFUSAL = 'goal (6.00, 3.00) abandoned at (17.56, -21.20) — a straight '
  + 'line from here enters the venue\'s declared keep-out';

test('a refusal on the robot\'s record is reported, once', async ({
  page, request
}) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1280, height: 900 });

  // The real venue is the fixture: taking a live record and adding one
  // property keeps every other field honest, and means this cannot pass
  // against a shape the binding does not actually serve.
  let live: any[] | null = null;
  try {
    const r = await request.get(`${BINDING_URL}/${SCENE}/world/entity/v1`,
                                { timeout: 4000 });
    live = r.ok() ? await r.json() : null;
  } catch {
    live = null;
  }
  test.skip(!live || !live.some((e: any) => e.entity_id === 'ent:robot:tb3'),
            `no robot on the binding at ${BINDING_URL} to borrow a record from`);

  const withRefusal = (live as any[]).map((e: any) =>
    e.entity_id === 'ent:robot:tb3'
      ? { ...e,
          properties: [...(e.properties || []),
                       { key: 'demo.nav_status', value: REFUSAL }] }
      : e);

  await page.route(`${BINDING_URL}/${SCENE}/world/entity/v1`, (route) =>
    route.fulfill({ json: withRefusal }));

  await page.goto('/?debug=1');
  await page.waitForFunction(() => (window as any).__viewer, null,
                             { timeout: 90_000 });
  await page.waitForFunction(
    () => (window as any).__appLogs?.some(
      (l: string) => l.startsWith('model:loaded')),
    null, { timeout: 90_000 });
  await page.waitForTimeout(2000);

  const logs: string[] = await page.evaluate(
    () => (window as any).__appLogs || []);
  const refusals = logs.filter((l) => l.startsWith('model:refused'));

  expect(refusals.length,
         `the refusal must be reported: ${logs.slice(-6).join(' | ')}`)
    .toBeGreaterThan(0);
  expect(refusals[0], 'and it must carry the reason, not just the fact')
    .toContain('abandoned');
  expect(refusals[0]).toContain('ent:robot:tb3');
  console.log(`  ${refusals[0]}`);

  // **Once.** The record is latched: a new subscriber replays it and the
  // cadence rewrites it, so reporting on arrival rather than on change would
  // turn one refusal into a running commentary down the log.
  expect(refusals.length,
         `one refusal, reported once -- got ${refusals.length}`).toBe(1);
});
