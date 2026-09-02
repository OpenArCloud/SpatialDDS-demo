import { execSync } from 'node:child_process';
import { expect, test } from '@playwright/test';

/**
 * The model layer against the running stack — the move case, headless.
 *
 * Skips unless the bridge is up with a model publishing behind it, so it
 * costs nothing in the default host run. Bring it up with:
 *
 *     SPATIALDDS_MODEL_LAYER=1 ./run_bridge_server_docker.sh
 *
 * What it asserts is the delta, not the log line. An earlier hand-run of this
 * scenario "passed" while proving nothing: it republished a pose the client
 * already held, and the client dutifully logged `model:moved`, which was true
 * and meaningless. So the position is read before and after, and the target
 * position is asserted to differ from the starting one first.
 */

const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

function container(): string | null {
  try {
    const name = execSync(
      "docker ps --format '{{.Names}}' | grep dds_bridge | head -1",
      { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim();
    return name || null;
  } catch {
    return null;
  }
}

test('a model entity moved on the bus moves in an open browser', async ({ page, request }) => {
  let model: any = { entities: [] };
  try {
    const res = await request.get(`${BRIDGE_URL}/v1/model`, { timeout: 4000 });
    model = await res.json();
  } catch {
    model = { entities: [] };
  }
  test.skip((model.entities || []).length === 0,
            'no model publishing — run with SPATIALDDS_MODEL_LAYER=1');
  const name = container();
  test.skip(name === null, 'model publisher not reachable to drive a move');

  const moved: string[] = [];
  page.on('console', (m) => {
    if (m.text().startsWith('model:')) {
      moved.push(m.text());
    }
  });

  await page.goto('/?debug=1');
  await page.waitForFunction(() => (window as any).__viewer, null, { timeout: 60_000 });
  await page.waitForTimeout(12_000);
  // A tile-load error panel intercepts pointer events; it is not the subject.
  const clear = async () => page.evaluate(() =>
    document.querySelectorAll('.cesium-widget-errorPanel').forEach((e) => e.remove()));
  await clear();
  await page.evaluate(() => document.getElementById('btnLocalize')?.click());
  await page.waitForTimeout(9_000);
  await clear();
  await page.evaluate(() => document.getElementById('btnDiscover')?.click());
  await page.waitForTimeout(12_000);

  const target = 'ent:duck:east';
  const read = () => page.evaluate((id) => {
    const v = (window as any).__viewer;
    const entity = v.entities.values.find((e: any) => String(e.id) === `${id}-model`);
    if (!entity) {
      return null;
    }
    const c = v.scene.globe.ellipsoid.cartesianToCartographic(
      entity.position.getValue(v.clock.currentTime));
    return [c.latitude * 180 / Math.PI, c.longitude * 180 / Math.PI] as [number, number];
  }, target);

  const before = await read();
  expect(before, 'the model duck should be rendered before moving it').not.toBeNull();

  // The destination is derived from where the duck currently is, not
  // hardcoded. A fixed destination makes the test pass once and then quietly
  // become a no-op -- republishing a pose the client already holds, which is
  // exactly the false pass this test was written to rule out. It failed that
  // way on its second run, which is the best argument for deriving it.
  const current = (model.entities || []).find((e: any) => e.entity_id === target);
  expect(current, `${target} should be in the model snapshot`).toBeTruthy();
  const [x, y] = current.pose.t;
  const destination = [x + 3.0, y - 3.0];
  execSync(`docker exec -w /app ${name} python3 scripts/move_duck.py ` +
           `${target} ${destination[0]} ${destination[1]}`, { stdio: 'ignore' });
  await page.waitForTimeout(8_000);

  const after = await read();
  expect(after).not.toBeNull();
  // Assert it landed where it was told, not merely that it twitched. The
  // commanded delta is known, so the observed ground distance should match it
  // -- a bare "moved more than N metres" invites the threshold and the delta
  // to drift apart, which is how the first version of this line failed: a
  // 4.24 m move against a 5 m floor.
  const metres = Math.hypot((after![0] - before![0]) * 111_320,
                            (after![1] - before![1]) * 111_320 * Math.cos(before![0] * Math.PI / 180));
  const commanded = Math.hypot(destination[0] - x, destination[1] - y);
  expect(commanded, 'the destination must differ from the start').toBeGreaterThan(1);
  expect(metres, 'the duck should move the distance it was told to')
    .toBeGreaterThan(commanded - 0.5);
  expect(metres).toBeLessThan(commanded + 0.5);
  expect(moved.some((l) => l.includes(`model:moved ${target}`))).toBe(true);
});
