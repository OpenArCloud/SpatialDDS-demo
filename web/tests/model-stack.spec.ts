import { expect, test } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  BRIDGE_URL, container, inContainer, readyPage, restoreVenue, startMover,
  stopMover, look
} from './model-stack.helpers';

/**
 * The world model layer against the running stack.
 *
 * Three scenarios that each need a live bus, a live bridge and a browser:
 * an entity moves, a view is filtered, an entity retires. They share one
 * venue, so they share one file and run in order -- as separate files
 * Playwright ran them in parallel and they fought over the same duck: one
 * retired what another was moving, and a third counted three ducks while
 * there were two. The venue is restored before each.
 *
 * All of it skips unless the stack is up:
 *
 *     SPATIALDDS_MODEL_LAYER=1 ./run_bridge_server_docker.sh
 */

test.describe.configure({ mode: 'serial' });

const OUT = process.env.P23_OUT || join(tmpdir(), 'spatialdds-basis');

let stack: string | null = null;

test.beforeAll(async ({ request }) => {
  stack = container();
  if (!stack) {
    return;
  }
  try {
    const model = await (await request.get(`${BRIDGE_URL}/v1/model`,
                                           { timeout: 4000 })).json();
    if (!(model.entities || []).length) {
      stack = null;
    }
  } catch {
    stack = null;
  }
});

test.beforeEach(async ({ request }) => {
  test.skip(stack === null,
            'no model stack — run with SPATIALDDS_MODEL_LAYER=1');
  await restoreVenue(request, stack as string);
});

test('a fresh tab shows the venue with no clicks at all', async ({ page }) => {
  /**
   * The repair P3.0 exists for.
   *
   * The model bootstrap used to live inside the Discover handler, so a person
   * opening the page saw an empty sky and two buttons, with no way to know
   * which one stood between them and the venue. The tests pressed the same
   * button, so nothing ever reported the gap -- the demo was equally
   * unfriendly to humans and harnesses, which is why it looked fine.
   *
   * Nothing below touches a control. The assertion that matters is the last
   * one: Discover must not have run. Without it this test would still pass if
   * something quietly clicked for us, which is the failure it exists to catch.
   */
  test.setTimeout(180_000);
  await page.goto('/?debug=1');
  await page.waitForFunction(() => (window as any).__viewer, null, { timeout: 60_000 });
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id).startsWith('ent:duck'));
  }, null, { timeout: 60_000 });

  const drawn: string[] = await page.evaluate(() => [...new Set(
    (window as any).__viewer.entities.values
      .map((e: any) => String(e.id))
      .filter((x: string) => x.startsWith('ent:'))
      .map((x: string) => x.replace(/-(model|extent)$/, '')))] as string[]);
  const logs: string[] = await page.evaluate(() => (window as any).__appLogs || []);

  expect(drawn.filter((i) => i.startsWith('ent:duck')).length).toBe(3);
  expect(drawn).toContain('ent:fountain:littlefield');
  expect(drawn).toContain('ent:gnome:visitor');

  expect(logs.some((l) => l.startsWith('discover:items')),
         'Discover must not have run').toBe(false);
  expect(logs.some((l) => l.startsWith('model:loaded')),
         'the bootstrap should report what it loaded').toBe(true);
  // And it localized against the demo's own VPS, not a real one.
  expect(logs.find((l) => l.startsWith('autostart:')))
    .toContain("is the demo's mock");
});

async function capture(page: any, url: string, name: string) {
  await readyPage(page, url);
  await look(page);

  const drawn = await page.evaluate(() => {
    const v = (window as any).__viewer;
    const now = v.clock.currentTime;
    return v.entities.values
      .filter((e: any) => String(e.id).startsWith('ent:'))
      .map((e: any) => ({
        id: String(e.id).replace(/-(model|extent)$/, ''),
        label: e.label?.text?.getValue?.(now) ?? null,
        // 0 is HeightReference.NONE; see the assertion below.
        labelHeightReference: e.label
          ? Number(e.label.heightReference?.getValue?.(now) ?? 0) : null,
        hasModel: !!e.model, hasBox: !!e.box, hasBillboard: !!e.billboard
      }))
      .sort((a: any, b: any) => a.id.localeCompare(b.id));
  });
  const readout = (await page.locator('#readout').textContent()) || '';
  await page.screenshot({ path: join(OUT, `${name}.png`) });
  console.log(`  [${name}] ${readout.replace(/\n/g, ' | ')}`);
  return { drawn, readout, ids: ([...new Set(drawn.map((d: any) => d.id))] as string[]).sort() };
}

test('the basis pair partitions the venue, and a stranger is placed either way',
  async ({ page }) => {
    test.setTimeout(300_000);
    const all = await capture(page, '/?debug=1', 'default-all');
    const observed = await capture(page, '/?debug=1&basis=observed', 'basis-observed');
    const declared = await capture(page, '/?debug=1&basis=declared', 'basis-declared');
    const authored = await capture(page, '/?debug=1&basis=authored', 'basis-authored');
    const derived = await capture(page, '/?debug=1&basis=derived', 'basis-derived');

    // The stranger, in the default view: placed, named from its type URI
    // because it published no label, and drawn as nothing more than it
    // claimed to be.
    const gnome = all.drawn.find((d: any) => d.id === 'ent:gnome:visitor');
    expect(gnome, 'the gnome should render').toBeTruthy();
    expect(gnome.label).toBe('garden-gnome');
    expect(gnome.hasModel, 'no asset published, so none may be drawn').toBe(false);
    expect(gnome.hasBox, 'no extent published, so no volume may be drawn').toBe(false);
    expect(all.ids.filter((i: string) => i.startsWith('ent:duck')).length,
           'the ducks are unaffected by a stranger arriving').toBe(3);

    /**
     * Markers are not clamped, and this is the guard for it.
     *
     * There is no terrain provider here -- world terrain was removed because
     * it drew a false surface over the photorealistic tiles' water -- so
     * CLAMP_TO_GROUND had nothing to clamp to but the ellipsoid, ~143 m under
     * Austin. The names looked right until the tiles finished loading and the
     * clamp resolved, then dropped underground: present in every hurried
     * screenshot and missing from every patient one.
     */
    for (const entity of all.drawn) {
      if (entity.label !== null) {
        expect(entity.labelHeightReference,
               `${entity.id} label must not be clamped`).toBe(0);
      }
    }

    // The quartet. One venue, four kinds of claim about it -- including two
    // about the same water, which is the pair the model refuses to settle.
    expect(observed.ids).toEqual(['ent:fountain:littlefield']);
    expect(declared.ids).toEqual(['ent:pond:littlefield']);
    expect(derived.ids).toEqual(['ent:pond:observed']);
    expect(authored.ids).toEqual(
      ['ent:duck:catalog-pose', 'ent:duck:east', 'ent:duck:west', 'ent:gnome:visitor']);
    // Disjoint and complete: every entity is in exactly one view, and between
    // them they account for the whole model.
    expect([...observed.ids, ...declared.ids, ...derived.ids, ...authored.ids].sort())
      .toEqual(all.ids);

    // A filtered scene says so, rather than presenting a partial world as
    // the whole one.
    expect(observed.readout).toContain('basis=OBSERVED — 6 hidden');
    expect(declared.readout).toContain('basis=DECLARED — 6 hidden');
    expect(derived.readout).toContain('basis=DERIVED — 6 hidden');
    expect(authored.readout).toContain('basis=AUTHORED — 3 hidden');
    expect(all.readout).not.toContain('hidden');

    // Both ponds are drawn, and the map says which claim is which rather
    // than showing two things called "Pond".
    const names = all.drawn
      .filter((d: any) => d.id.startsWith('ent:pond'))
      .map((d: any) => d.label)
      .filter(Boolean)
      .sort();
    // "derived", not "observed": the qualifier is the entity's own basis,
    // and pondwatch computes its bounds rather than having seen them. A
    // client that relabelled it would be editing somebody else's claim.
    expect(names).toEqual(['Pond (declared)', 'Pond (derived)']);
  });

test('a model entity moved on the bus moves in an open browser', async ({ page }) => {
  test.setTimeout(240_000);
  const name = stack as string;
  const target = 'ent:duck:east';

  const moved: string[] = [];
  page.on('console', (m) => {
    if (m.text().startsWith('model:')) {
      moved.push(m.text());
    }
  });

  await readyPage(page);
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id).startsWith('ent:duck'));
  }, null, { timeout: 40_000 });

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

  // A fixed destination, safe because the venue was restored above. Deriving
  // it from "current" instead made each run move the duck another few metres
  // from its last position: eight runs had walked it from (16.5, -10.5) to
  // (40.5, -34.5), out of the fountain and onto the plaza.
  const seeded = [16.5, -10.5];
  // Inside the pond the venue declares (x 9.5..20, y -10..-18). The old
  // destination (6.0, -17.0) predates the pond and would now park a duck on
  // the sculpture -- harmless to the assertion, and a bad picture.
  const destination = [11.0, -16.0];
  const [x, y] = seeded;

  // The republish is not politeness. About one run in three, an update
  // published shortly after a browser subscribes never reaches it: the socket
  // is open, the client has logged that it is watching, the writer reports
  // success, and nothing arrives. A second publish of the same pose always
  // lands, and republishing is idempotent -- same key, same value -- so this
  // proves the path without pretending the first attempt worked. The gap is
  // recorded in SPEC_COMPLIANCE.
  const publish = () => inContainer(
    name, `python3 scripts/move_duck.py ${target} ${destination[0]} ${destination[1]}`);
  // Wait on the thing itself, not on a log line about it. The log was a
  // proxy and stopped being a true one in P3.3: a FAST entity's move arrives
  // on the tempo lane, which is applied silently because six lines a second
  // would drown the panel. The position is what the test is actually about.
  const applied = () => page.waitForFunction(([id, lat, lon]) => {
    const v = (window as any).__viewer;
    const e = v.entities.values.find((x: any) => String(x.id) === `${id}-model`);
    if (!e) {
      return false;
    }
    const c = v.scene.globe.ellipsoid.cartesianToCartographic(
      e.position.getValue(v.clock.currentTime));
    return Math.abs(c.latitude * 180 / Math.PI - (lat as number)) > 1e-7
      || Math.abs(c.longitude * 180 / Math.PI - (lon as number)) > 1e-7;
  }, [target, before![0], before![1]] as const, { timeout: 15_000 });

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
  // commanded delta is known, so the observed ground distance should match
  // it -- a bare "moved more than N metres" invites the threshold and the
  // delta to drift apart, which is how the first version of this line
  // failed: a 4.24 m move against a 5 m floor.
  const metres = Math.hypot(
    (after![0] - before![0]) * 111_320,
    (after![1] - before![1]) * 111_320 * Math.cos(before![0] * Math.PI / 180));
  const commanded = Math.hypot(destination[0] - x, destination[1] - y);
  expect(commanded, 'the destination must differ from the start').toBeGreaterThan(1);
  expect(metres, 'the duck should move the distance it was told to')
    .toBeGreaterThan(commanded - 0.5);
  expect(metres).toBeLessThan(commanded + 0.5);
  // The client said, once, which lane it is following.
  const logs: string[] = await page.evaluate(() => (window as any).__appLogs || []);
  expect(logs.some((l) => l.startsWith(`model:tempo ${target}`))
         || moved.some((l) => l.includes(`model:moved ${target}`)),
         'the client should report how the move reached it').toBe(true);
});

test('a retired entity states its reason, then leaves the map', async ({ page }) => {
  test.setTimeout(240_000);
  const name = stack as string;
  const target = 'ent:duck:east';
  const reason = 'taken in for the winter';

  await readyPage(page);
  await page.waitForFunction((id) => {
    const v = (window as any).__viewer;
    return v && v.entities.values.some((e: any) => String(e.id) === id);
  }, target, { timeout: 40_000 });

  const drawn = () => page.evaluate((id) => {
    const v = (window as any).__viewer;
    return v.entities.values
      .map((e: any) => String(e.id))
      .filter((x: string) => x === id || x.startsWith(`${id}-`));
  }, target);

  expect((await drawn()).length,
         'the duck should be on the map before it is retired').toBeGreaterThan(0);

  inContainer(name,
    `python3 scripts/retire_entity.py ${target} ${JSON.stringify(reason)}`);

  // Gone from the map, in every form it was drawn in.
  await expect.poll(async () => (await drawn()).length, { timeout: 40_000 }).toBe(0);

  const logs: string[] = await page.evaluate(() => (window as any).__appLogs || []);
  const retired = logs.filter((l) => l.startsWith(`model:retired ${target}`));
  const disposed = logs.filter((l) => l.startsWith(`model:disposed ${target}`));

  expect(retired.length, 'the tombstone should have been reported').toBeGreaterThan(0);
  expect(retired[0]).toContain(reason);
  expect(disposed.length, 'the removal should have been reported').toBeGreaterThan(0);
  // In that order. After the dispose there is nothing left to explain itself,
  // so a client that only handles removal shows things vanishing for no
  // stated cause.
  expect(logs.indexOf(retired[0])).toBeLessThan(logs.indexOf(disposed[0]));
});

test('every catalog reference is resolved by id, with no coverage query first',
  async ({ page }) => {
    /**
     * The coincidence, gone -- and no longer needing a switch to prove it.
     *
     * `?noassetcache=1` existed because the duck's catalogue row and the
     * duck's entity were in the same plaza: the cached lookup always hit, so
     * reference-by-id was never exercised and the demo could not tell the two
     * apart. Since P3.0 the model bootstrap runs *before* any coverage query,
     * so there is no cache to hit and every reference resolves by id on every
     * page load. The flag was retired rather than left as a switch with
     * nothing left to switch.
     *
     * This asserts the ordinary path, which is stronger evidence than the
     * flag ever was: the ducks cannot render at all without `content_id_in`.
     */
    test.setTimeout(240_000);
    await page.goto('/?debug=1');
    await page.waitForFunction(() => (window as any).__viewer, null, { timeout: 60_000 });
    await page.waitForFunction(
      () => (window as any).__appLogs?.some((l: string) =>
        l.startsWith('catalog:by-id')), null, { timeout: 60_000 });

    const logs: string[] = await page.evaluate(() => (window as any).__appLogs || []);
    const line = logs.find((l) => l.startsWith('catalog:by-id'))!;
    // Three ducks, one row: every distinct id resolved, and the line says so
    // without implying two lookups failed.
    expect(line, 'every id should have resolved')
      .toMatch(/3 reference\(s\) over 1 id\(s\); resolved 1$/);
    // And it happened before Discover, not because of it.
    expect(logs.indexOf(line)).toBeLessThan(
      logs.findIndex((l) => l.startsWith('discover:items')) === -1
        ? Number.MAX_SAFE_INTEGER
        : logs.findIndex((l) => l.startsWith('discover:items')));

    const ducks = await page.evaluate(() => {
      const v = (window as any).__viewer;
      return v.entities.values
        .filter((e: any) => String(e.id).startsWith('ent:duck') && !!e.model)
        .map((e: any) => String(e.id));
    });
    expect(ducks.length, 'the ducks render from a by-id resolution').toBe(3);
  });

test('the ducks wander in an open tab, and stop dead when the mover does',
  async ({ page, request }) => {
    /**
     * The mover is a consumer that produces: it reads the model, decides what
     * it would like to be different, and asks the authority. Two things
     * follow, and both are asserted here rather than argued.
     *
     * Motion reaches an open tab with no polling and no client-side
     * animation -- the browser is drawing what the bus says.
     *
     * And when the mover stops, everything stops, for everyone. Nothing of
     * its is latched anywhere, so there is no ghost writer whose last word
     * outlives it and no second answer to where a duck is. That is the Part 2
     * lesson from the other side: a process that only asks leaves nothing
     * behind when it goes.
     */
    test.setTimeout(300_000);
    const name = stack as string;

    const positions = () => page.evaluate(() => {
      const v = (window as any).__viewer;
      const out: Record<string, [number, number]> = {};
      for (const e of v.entities.values) {
        const id = String(e.id);
        if (!id.startsWith('ent:duck') || id.endsWith('-model')) continue;
        const c = v.scene.globe.ellipsoid.cartesianToCartographic(
          e.position.getValue(v.clock.currentTime));
        out[id] = [c.latitude * 180 / Math.PI, c.longitude * 180 / Math.PI];
      }
      return out;
    });
    const metres = (a: [number, number], b: [number, number]) =>
      Math.hypot((b[0] - a[0]) * 111_320, (b[1] - a[1]) * 96_000);

    await readyPage(page);
    await page.waitForFunction(() => {
      const v = (window as any).__viewer;
      return v && v.entities.values.some((e: any) => String(e.id).startsWith('ent:duck'));
    }, null, { timeout: 40_000 });

    try {
      startMover(name);
      const before = await positions();
      await page.waitForTimeout(8000);
      const during = await positions();

      const moved = Object.keys(before)
        .filter((id) => metres(before[id], during[id]) > 0.2);
      console.log(`  wandering: ${moved.length}/3 ducks moved in the open tab`);
      // Not all three: the walk is random, so a duck can spend a window
      // stepping back and forth over the same metre. Two is motion; asserting
      // three would be asserting the random number generator.
      expect(moved.length, 'the ducks should be wandering in the open tab')
        .toBeGreaterThanOrEqual(2);
    } finally {
      stopMover(name);
    }

    // Let anything already in flight land, then nothing more may happen.
    await page.waitForTimeout(3000);
    const settled = await positions();
    await page.waitForTimeout(4000);
    const still = await positions();
    for (const id of Object.keys(settled)) {
      expect(metres(settled[id], still[id]),
             `${id} must be still once the mover has gone`).toBeLessThan(0.05);
    }

    // And the bus agrees with the tab: one truth, not a screen that kept its
    // own last known positions.
    const model = await (await request.get(`${BRIDGE_URL}/v1/model`)).json();
    const onBus = Object.fromEntries((model.entities || [])
      .filter((e: any) => e.entity_id.startsWith('ent:duck'))
      .map((e: any) => [e.entity_id, e.pose.t]));
    expect(Object.keys(onBus).sort()).toEqual(Object.keys(still).sort());
    console.log('  frozen: tab and bus agree on all three ducks');
  });

test('shrinking the pond crowds the ducks on every open tab', async ({ browser }) => {
  /**
   * The demonstration the whole part exists for.
   *
   * Two tabs, one command, and the ducks end up somewhere else on both --
   * because a service that has never heard of ducks-and-water read a box off
   * the model and clamped into it. Nothing in `reshape_pond.py` mentions a
   * duck; nothing in `duck_mover.py` mentions a pond beyond the id whose
   * extent it follows. An application that moved the water *and* the ducks
   * would prove nothing; this proves that the model is the interface.
   *
   * Two tabs rather than one because "every open tab" is the claim. A single
   * client could be running its own animation and nobody would know.
   */
  test.setTimeout(420_000);
  const name = stack as string;
  const first = await browser.newPage();
  const second = await browser.newPage();

  const bounds = async () => {
    const model = await (await first.request.get(`${BRIDGE_URL}/v1/model`)).json();
    const pond = (model.entities || []).find(
      (e: any) => e.entity_id === 'ent:pond:littlefield');
    return { min: pond.extent.min_xyz, max: pond.extent.max_xyz };
  };
  const ducksOn = (page: any) => page.evaluate(() => {
    const v = (window as any).__viewer;
    const out: Record<string, [number, number]> = {};
    for (const e of v.entities.values) {
      const id = String(e.id);
      if (!id.startsWith('ent:duck') || id.endsWith('-model')) continue;
      const c = v.scene.globe.ellipsoid.cartesianToCartographic(
        e.position.getValue(v.clock.currentTime));
      out[id] = [c.latitude * 180 / Math.PI, c.longitude * 180 / Math.PI];
    }
    return out;
  });

  try {
    for (const page of [first, second]) {
      await readyPage(page);
      await page.waitForFunction(() => {
        const v = (window as any).__viewer;
        return v && v.entities.values.some((e: any) => String(e.id).startsWith('ent:duck'));
      }, null, { timeout: 40_000 });
      await look(page);
    }
    startMover(name);
    await first.waitForTimeout(4000);

    const wide = await bounds();
    const beforeA = await ducksOn(first);
    const beforeB = await ducksOn(second);
    expect(Object.keys(beforeA).length).toBe(3);
    expect(Object.keys(beforeB).length).toBe(3);
    await first.screenshot({ path: join(OUT, 'shrink-1-before.png') });

    // 0.4 rather than 0.5: at half scale the crowding is real but reads as
    // a small nudge in a still frame, and this capture is the part's whole
    // argument. 10.5 x 8.0 m becomes 4.2 x 3.2 m -- still comfortably above
    // the 2 m the mover's inset needs, so the ducks crowd inside the water
    // rather than sitting on its rim.
    inContainer(name, 'python3 scripts/reshape_pond.py --shrink 0.4');
    const small = await bounds();
    expect(small.max[0] - small.min[0]).toBeLessThan(wide.max[0] - wide.min[0]);

    // The bus decides when this is true, not a timer.
    await expect.poll(async () => {
      const model = await (await first.request.get(`${BRIDGE_URL}/v1/model`)).json();
      return (model.entities || [])
        .filter((e: any) => e.entity_id.startsWith('ent:duck'))
        .every((e: any) => e.pose.t[0] >= small.min[0] && e.pose.t[0] <= small.max[0]
                        && e.pose.t[1] >= small.min[1] && e.pose.t[1] <= small.max[1]);
    }, { timeout: 60_000 }).toBe(true);

    await first.waitForTimeout(2500);
    const afterA = await ducksOn(first);
    const afterB = await ducksOn(second);
    await first.screenshot({ path: join(OUT, 'shrink-2-after.png') });
    await second.screenshot({ path: join(OUT, 'shrink-2-after-second-tab.png') });

    // Both tabs moved, and they agree with each other.
    for (const [tab, before, after] of
         [['first', beforeA, afterA], ['second', beforeB, afterB]] as const) {
      const moved = Object.keys(before).filter((id) =>
        Math.hypot((after[id][0] - before[id][0]) * 111_320,
                   (after[id][1] - before[id][1]) * 96_000) > 0.3);
      console.log(`  ${tab} tab: ${moved.length}/3 ducks ended up somewhere else`);
      expect(moved.length, `${tab} tab should show the crowding`).toBeGreaterThanOrEqual(2);
    }
    // Do the two tabs agree? Only once the world stops. Sampled while the
    // mover is running they legitimately differ by one in-flight update --
    // the first attempt at this compared them mid-motion and called a 0.19 m
    // gap a disagreement, which is asserting that two windows can read the
    // same clock at the same instant rather than that they converge.
    stopMover(name);
    await first.waitForTimeout(3000);
    const restA = await ducksOn(first);
    const restB = await ducksOn(second);
    for (const id of Object.keys(restA)) {
      expect(Math.abs(restA[id][0] - restB[id][0])).toBeLessThan(1e-9);
      expect(Math.abs(restA[id][1] - restB[id][1])).toBeLessThan(1e-9);
    }
    console.log('  once still, both tabs agree on every duck to 1e-9 degrees '
                + '— one model, two windows');
  } finally {
    stopMover(name);
    inContainer(name, 'python3 scripts/reshape_pond.py --restore');
    await first.close();
    await second.close();
  }
});
