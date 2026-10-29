import { execSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import {
  BINDING_URL, SCENE, container, look, readyPage, sendCommand, venueEntities
} from './model-stack.helpers';

/**
 * The claims overlay against the running venue: edges in the scene, the
 * strip, the provenance card and the one switch, through a real gateway, a
 * real bus and the nav2 robot.
 *
 * One serial file, because the tests are one journey: the robot drives into
 * the fountain, is looked at, has one of its asserters killed and
 * restarted, drives out, and then the switch is thrown. The decisions the
 * overlay makes (how a bar ends, what a card says) are claims-view.spec.ts,
 * against the binding's own fixture; this is that they reach the screen.
 *
 * Skips on a probe of the lane and of the robot, so it re-arms the day the
 * stack runs with both.
 */

const LANE = `${BINDING_URL}/${SCENE}/world/oarc.relationship/v1`;
const ROBOT = 'ent:robot:tb3';
const FOUNTAIN = 'ent:fountain:littlefield';
const SF_WITHIN = 'http://www.opengis.net/ont/geosparql#sfWithin';
const ZONE_LATCHED = 'svc:claims:demo/zone-latched';
const ZONE_FAST = 'svc:claims:demo/zone-fast';
const WITHIN = `${ROBOT} ${SF_WITHIN} ${FOUNTAIN}`;
/** A build of the commit before the overlay, served, for the switch's check. */
const PRE_BATCH_URL = process.env.PRE_BATCH_URL || '';

let stack: string | null = null;
let robot = false;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  stack = container();
  if (!stack) {
    return;
  }
  try {
    const r = await request.get(LANE);
    if (!r.ok()) {
      stack = null;
      return;
    }
    robot = (await venueEntities(request))
      .some((e: any) => e.entity_id === ROBOT && e.state === 'ACTIVE');
  } catch {
    stack = null;
  }
});

test.beforeEach(() => {
  test.skip(stack === null, 'no relationship lane served — start the stack '
            + 'with the model layer (claims are on by default)');
  test.skip(!robot, 'no robot publishing — run scripts/run_robot_tier.sh');
});

/** Every console error a test's page logs; each test asserts none. */
function watchConsole(page: any): string[] {
  const errors: string[] = [];
  page.on('console', (m: any) => {
    if (m.type() === 'error') {
      errors.push(m.text());
    }
  });
  page.on('pageerror', (e: Error) => errors.push(String(e)));
  return errors;
}

/** The robot standing still within 1.5 m of a point: nav2 has finished. */
async function driveTo(request: any, x: number, y: number): Promise<void> {
  const res = await sendCommand(request, 'goto', ROBOT,
                                { t: [x, y, -0.84], q: [0, 0, 0, 1] });
  expect((await res.json()).outcome, await res.text()).toBe('accepted');
  const at = async () => {
    const r = (await venueEntities(request)).find((e: any) => e.entity_id === ROBOT);
    return Math.hypot(r.pose.t[0] - x, r.pose.t[1] - y);
  };
  await expect.poll(async () => {
    const a = await at();
    await new Promise((r) => setTimeout(r, 3000));
    const b = await at();
    return a < 1.5 && Math.abs(a - b) < 0.05;
  }, { timeout: 180_000, intervals: [1000] }).toBe(true);
}

/** The lane's edges in force, as the binding serves them. */
async function laneEdges(request: any): Promise<any[]> {
  const r = await request.get(LANE);
  return r.ok() ? r.json() : [];
}

/** What the overlay has in the scene: Cesium entity ids and whether shown. */
const sceneEdges = (page: any) => page.evaluate(() => {
  const v = (window as any).__viewer;
  return v.entities.values
    .filter((e: any) => String(e.id).startsWith('claim:'))
    .map((e: any) => ({ id: String(e.id), show: e.show,
                        label: e.label ? e.label.text.getValue() : null }));
});

const bars = (page: any, proposition: string) =>
  page.locator(`.claimGroup[data-proposition="${proposition}"] .claimBar`);

test('entry: the observed edges appear in the scene and the strip; near at the threshold', async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors = watchConsole(page);
  await readyPage(page);
  await driveTo(request, 12, -2);

  const within = (await laneEdges(request)).filter((c: any) =>
    c.subject_ref === ROBOT && c.predicate === SF_WITHIN && c.object_ref === FOUNTAIN);
  expect(within.map((c: any) => c.asserter).sort()).toEqual([ZONE_FAST, ZONE_LATCHED]);

  // In the scene: a ring under the robot per asserter, shown.
  await expect.poll(async () => (await sceneEdges(page))
    .filter((e: any) => within.some((c: any) => e.id === `claim:${c.id}#ring`) && e.show)
    .length, { timeout: 20_000 }).toBe(2);
  // On the strip: two open bars in one group.
  await expect(bars(page, WITHIN).and(page.locator('[data-end-kind="open"]')))
    .toHaveCount(2, { timeout: 20_000 });

  // Near, at its threshold, as the ducks react: an edge from the robot to a
  // duck appears as a line, and its bar is derived.
  await expect.poll(async () => {
    const near = (await laneEdges(request)).filter((c: any) =>
      c.subject_ref === ROBOT && c.predicate.includes('/predicate/near#'));
    const drawn = (await sceneEdges(page)).map((e: any) => e.id);
    return near.some((c: any) => drawn.includes(`claim:${c.id}#line`));
  }, { timeout: 60_000, intervals: [1000] }).toBe(true);
  expect(errors).toEqual([]);
});

test('coexistence: two asserters about one proposition, grouped, both named', async ({ page }) => {
  const errors = watchConsole(page);
  await readyPage(page);
  const group = page.locator(`.claimGroup[data-proposition="${WITHIN}"]`);
  await expect(group.locator('.asserterBadge')).toHaveText(['zone-fast', 'zone-latched'],
                                                           { timeout: 20_000 });
  await expect(group.locator('.claimBar')).toHaveCount(2);
  // In the scene, side by side and named once.
  const badges = (await sceneEdges(page)).filter((e: any) => e.id.endsWith('#badges'));
  expect(badges.map((b: any) => b.label)).toContain('zone-fast  ·  zone-latched');
  expect(errors).toEqual([]);
});

test('provenance cards: a declared, an observed and a derived edge; the derived lights its evidence', async ({ page, request }) => {
  const errors = watchConsole(page);
  await readyPage(page);
  const card = page.locator('#claimCard');
  const value = (label: string) => card.locator(`tr[data-label="${label}"] td`);
  const edges = await laneEdges(request);

  const check = async (c: any) => {
    await page.locator(`.claimBar[data-claim-id="${c.id}"]`).click();
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute('data-claim-id', c.id);
    await expect(value('subject')).toHaveText(c.subject_ref);
    await expect(value('predicate URI')).toHaveText(c.predicate);
    await expect(value('object')).toHaveText(c.object_ref);
    await expect(value('asserter')).toHaveText(c.asserter);
    await expect(value('basis')).toHaveText(c.basis);
  };

  const declared = edges.find((c: any) => c.basis === 'DECLARED'
    && c.predicate.endsWith('/P361'));
  await check(declared);
  await expect(value('predicate')).toHaveText('part of');
  expect(await page.evaluate(() => (window as any).__claimsOverlay.citedEntityIds()))
    .toEqual([]);

  const observed = edges.find((c: any) => c.asserter === ZONE_LATCHED);
  await check(observed);
  await expect(value('predicate')).toHaveText('within');
  await expect(value('valid until')).toHaveText('while asserted');

  // A near edge churns as the ducks flee, so take whichever is in force now;
  // the card holds it even if it retires while open.
  let derived: any;
  await expect.poll(async () => {
    derived = (await laneEdges(request)).find((c: any) => c.basis === 'DERIVED');
    return derived && await page.locator(`.claimBar[data-claim-id="${derived.id}"]`).count();
  }, { timeout: 60_000, intervals: [1000] }).toBeTruthy();
  await check(derived);
  await expect(value('derived from')).toContainText(ROBOT);
  const keys = [...new Set(derived.derived_from.map((r: any) => r.key))].sort();
  expect((await page.evaluate(() => (window as any).__claimsOverlay.citedEntityIds())).sort())
    .toEqual(keys);
  await expect(card.locator('.claimCardCited')).toBeVisible();
  // Closing the card puts the evidence out.
  await card.locator('.claimCardClose').click();
  await expect(card).toBeHidden();
  expect(await page.evaluate(() => (window as any).__claimsOverlay.citedEntityIds()))
    .toEqual([]);
  expect(errors).toEqual([]);
});

test('crash: a SIGKILLed zone watch leaves its edge standing, marked; a clean restart clears it', async ({ page, request }) => {
  test.setTimeout(240_000);
  const errors = watchConsole(page);
  await readyPage(page);
  const edge = (await laneEdges(request)).find((c: any) => c.asserter === ZONE_LATCHED);
  expect(edge, 'the robot must still be in the fountain').toBeTruthy();
  const bar = page.locator(`.claimBar[data-claim-id="${edge.id}"]`);
  await expect(bar).toHaveAttribute('data-end-kind', 'open');

  // Anchored: the container's PID 1 is the shell whose command line contains
  // this string (model-stack.helpers stopMover has the history).
  execSync(`docker exec ${stack} pkill -9 -f `
           + `'^python3 -m spatialdds_demo.zone_watch --evidence latched'`);
  try {
    await expect(bar).toHaveAttribute('data-writer-gone', 'true', { timeout: 60_000 });
    // Nothing disappeared: the ring is still drawn, and says why it is dim.
    const drawn = (await sceneEdges(page)).map((e: any) => e.id);
    expect(drawn).toContain(`claim:${edge.id}#ring`);
    expect(drawn).toContain(`claim:${edge.id}#gone`);
    await expect(bar).toHaveAttribute('data-end-kind', 'open');
    await bar.click();
    await expect(page.locator('#claimCard tr[data-label="asserter"] td'))
      .toContainText('asserter not alive');
  } finally {
    // The clean restart: same asserter, same deterministic edge id, so it
    // revises the edge it held.
    execSync(`docker exec -d -w /app ${stack} sh -c 'python3 -m spatialdds_demo.zone_watch `
             + `--evidence latched >/tmp/zone_latched_restart.log 2>&1'`);
  }
  await expect(bar).not.toHaveAttribute('data-writer-gone', 'true', { timeout: 60_000 });
  await expect.poll(async () => (await sceneEdges(page))
    .some((e: any) => e.id === `claim:${edge.id}#gone`), { timeout: 20_000 }).toBe(false);
  expect(errors).toEqual([]);
});

test('exit: both observed edges retire with their reason, as the relationship ending', async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors = watchConsole(page);
  await readyPage(page);
  const ids = (await laneEdges(request)).filter((c: any) =>
    c.subject_ref === ROBOT && c.predicate === SF_WITHIN && c.object_ref === FOUNTAIN)
    .map((c: any) => c.id);
  expect(ids).toHaveLength(2);
  await driveTo(request, 17, -23);
  for (const id of ids) {
    const bar = page.locator(`.claimBar[data-claim-id="${id}"]`);
    await expect(bar).toHaveAttribute('data-end-kind', 'relationship', { timeout: 20_000 });
    expect(await bar.getAttribute('title')).toContain('no longer holds');
  }
  await expect.poll(async () => (await sceneEdges(page))
    .filter((e: any) => ids.some((id: string) => e.id.startsWith(`claim:${id}#`))).length,
  { timeout: 20_000 }).toBe(0);
  expect(errors).toEqual([]);
});

/** Off, every claims element: scene, strip, card, table, and the highlight. */
async function expectAllOff(page: any): Promise<void> {
  await expect(page.locator('#btnToggleClaims')).toHaveText('Claims: Off');
  await expect(page.locator('#claimsStrip')).toBeHidden();
  await expect(page.locator('#claimCard')).toBeHidden();
  await expect(page.locator('#claimsOverlay')).toBeHidden();
  const shown = (await sceneEdges(page)).filter((e: any) => e.show);
  expect(shown).toEqual([]);
  const width = await page.evaluate((id: string) => (window as any).__viewer.entities
    .getById(`${id}-extent`)?.box?.outlineWidth?.getValue(), FOUNTAIN);
  expect(width).toBe(2);
}

test('the switch: off hides every claims element, on restores them', async ({ page }) => {
  const errors = watchConsole(page);
  await readyPage(page);
  await expect(page.locator('#btnToggleClaims')).toHaveText('Claims: On');
  await expect(page.locator('#claimsStrip')).toBeVisible();
  await expect.poll(async () => (await sceneEdges(page)).length, { timeout: 20_000 })
    .toBeGreaterThan(0);
  await page.locator('.claimBar').first().click();
  await page.locator('#btnClaimsTable').click();
  await page.locator('#btnToggleClaims').click();
  await expectAllOff(page);
  await page.locator('#btnToggleClaims').click();
  await expect(page.locator('#btnToggleClaims')).toHaveText('Claims: On');
  await expect(page.locator('#claimsStrip')).toBeVisible();
  const all = await sceneEdges(page);
  expect(all.length).toBeGreaterThan(0);
  expect(all.every((e: any) => e.show)).toBe(true);
  expect(errors).toEqual([]);
});

/**
 * Off, the venue renders as it did before the overlay existed: a screenshot
 * of this build with the switch off against one of the build before it, the
 * same camera, the robot outside the fountain so no containment is lit in
 * either. Chrome that is not the venue is masked in both (the toolbar's
 * button is restyled, and the clock and timeline tick).
 *
 * Needs that build served: PRE_BATCH_URL, for instance a worktree of the
 * parent commit with `vite build && vite preview --port 4174`.
 */
test('the switch, off, matches the venue as the pre-overlay build drew it', async ({ browser, page }) => {
  test.skip(!PRE_BATCH_URL, 'set PRE_BATCH_URL to a served build of the commit '
            + 'before the overlay');
  test.setTimeout(300_000);
  const errors = watchConsole(page);
  const mask = (p: any) => ['#toolbar', '#hud', '.cesium-viewer-animationContainer',
    '.cesium-viewer-timelineContainer', '.cesium-viewer-toolbar',
    '.cesium-viewer-bottom'].map((s) => p.locator(s));

  const before = await browser.newPage({ viewport: page.viewportSize() });
  await readyPage(before, `${PRE_BATCH_URL}/?debug=1`);
  await readyPage(page);
  await page.locator('#btnToggleClaims').click();
  await expectAllOff(page);
  // Both cameras set and settled, then both shot as close together as the
  // venue allows: the ducks keep moving.
  await Promise.all([look(before), look(page)]);
  const [a, b] = await Promise.all([
    before.screenshot({ mask: mask(before) }),
    page.screenshot({ mask: mask(page) })
  ]);
  await before.close();
  writeFileSync(test.info().outputPath('pre-overlay-build.png'), a);
  writeFileSync(test.info().outputPath('this-build-switch-off.png'), b);
  const diff = await page.evaluate(async ([x, y]: string[]) => {
    const load = (src: string) => new Promise<HTMLImageElement>((res) => {
      const i = new Image();
      i.onload = () => res(i);
      i.src = `data:image/png;base64,${src}`;
    });
    const [ia, ib] = await Promise.all([load(x), load(y)]);
    const px = (img: HTMLImageElement) => {
      const c = document.createElement('canvas');
      c.width = img.width;
      c.height = img.height;
      const g = c.getContext('2d')!;
      g.drawImage(img, 0, 0);
      return g.getImageData(0, 0, c.width, c.height).data;
    };
    const da = px(ia);
    const db = px(ib);
    let differ = 0;
    for (let i = 0; i < da.length; i += 4) {
      if (Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1])
          + Math.abs(da[i + 2] - db[i + 2]) > 48) {
        differ += 1;
      }
    }
    return { differ, total: da.length / 4 };
  }, [a.toString('base64'), b.toString('base64')]);
  test.info().annotations.push({ type: 'pixels differing',
    description: `${diff.differ} of ${diff.total} (${(100 * diff.differ / diff.total).toFixed(2)}%)` });
  expect(diff.differ / diff.total).toBeLessThan(0.01);
  expect(errors).toEqual([]);
});
