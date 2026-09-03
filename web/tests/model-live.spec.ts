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

  // Start from a known world. The mover reads the publisher's latched sample
  // while the bridge cache holds whatever has been published since, so
  // "where is the duck now" has two answers -- see the two-writer note in
  // SPEC_COMPLIANCE. Resetting first means this test does not have to care
  // which one it would have got.
  execSync(`docker exec -w /app ${name} python3 scripts/move_duck.py --reset`,
           { stdio: 'ignore' });
  // And wait for it to land. The reset is three hops from here -- bus, bridge
  // reader, cache -- and loading the page before it arrives means the client
  // bootstraps from the pre-reset world, which is how this failed on its
  // first run after the reset was added.
  await expect
    .poll(async () => {
      const res = await request.get(`${BRIDGE_URL}/v1/model`, { timeout: 4000 });
      const e = ((await res.json()).entities || [])
        .find((x: any) => x.entity_id === 'ent:duck:east');
      return e ? `${e.pose.t[0]},${e.pose.t[1]}` : '';
    }, { timeout: 20_000 })
    .toBe('16.5,-10.5');

  const moved: string[] = [];
  page.on('console', (m) => {
    if (m.text().startsWith('model:')) {
      moved.push(m.text());
    }
  });

  await page.goto('/?debug=1');
  await page.waitForFunction(() => (window as any).__viewer, null, { timeout: 60_000 });
  // A tile-load error panel intercepts pointer events; it is not the subject.
  const clear = async () => page.evaluate(() =>
    document.querySelectorAll('.cesium-widget-errorPanel').forEach((e) => e.remove()));
  await clear();

  // Wait on conditions, not on the clock. Fixed sleeps were long enough when
  // this spec ran alone and too short when the whole suite had been working
  // the same bridge, which failed as "the duck was never rendered" -- a real
  // symptom with an unrelated cause.
  await page.evaluate(() => document.getElementById('btnLocalize')?.click());
  await expect
    .poll(async () => Number(/alt=([\d.]+)m/.exec(
      (await page.locator('#readout').getAttribute('data-geopose')) || '')?.[1] ?? NaN),
    { timeout: 40_000 })
    .toBeLessThan(1000);

  await clear();
  await page.evaluate(() => document.getElementById('btnDiscover')?.click());
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id).startsWith('ent:duck'));
  }, null, { timeout: 40_000 });

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

  // A fixed destination, which is safe because the reset above establishes
  // where the duck starts. Deriving it from "current" instead made each run
  // move the duck another few metres from its last position: eight runs had
  // walked it from (16.5, -10.5) to (40.5, -34.5), out of the fountain and
  // onto the plaza. Absolute beats relative when the starting point is known.
  const seeded = [16.5, -10.5];
  const destination = [6.0, -17.0];
  const [x, y] = seeded;
  // Publish, and wait for the client to say it applied the update rather than
  // for a fixed interval -- the browser is three hops from the writer.
  //
  // The republish is not politeness. About one run in three, an update
  // published shortly after a browser subscribes never reaches it: the socket
  // is open, the client has logged that it is watching, the mover reports
  // success, and nothing arrives. A second publish of the same pose always
  // lands. Republishing is idempotent -- same key, same value -- so this
  // proves the path without pretending the first attempt worked, and it says
  // so in the output when it happens. The underlying gap is recorded in
  // SPEC_COMPLIANCE; it is a Part 2 question, not something to paper over.
  const publish = () => execSync(
    `docker exec -w /app ${name} python3 scripts/move_duck.py ` +
    `${target} ${destination[0]} ${destination[1]}`).toString().trim();
  const applied = () => page.waitForFunction((id) => (window as any).__appLogs?.some(
    (l: string) => l.startsWith(`model:moved ${id}`)), target, { timeout: 15_000 });

  console.log('[mover]', publish().split('\n').pop());
  try {
    await applied();
  } catch {
    console.log('[test] first publish did not reach the browser — republishing');
    publish();
    await applied();
  }

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

  // Put it back. The destination is relative to wherever the duck currently
  // is, so without this each run walks it another few metres from the
  // fountain -- eight runs had already taken it from (16.5, -10.5) to
  // (40.5, -34.5), well onto the plaza. A test that leaves the demo worse
  // than it found it is a test people stop running.
  execSync(`docker exec -w /app ${name} python3 scripts/move_duck.py --reset`,
           { stdio: 'ignore' });
});
