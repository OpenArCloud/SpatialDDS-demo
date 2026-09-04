import { expect, test } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/**
 * `?basis=` against the running stack — the two views, headless.
 *
 * Skips unless the bridge is up with a model publishing behind it, so it
 * costs nothing in the default host run. Bring it up with:
 *
 *     SPATIALDDS_MODEL_LAYER=1 ./run_bridge_server_docker.sh
 *
 * The unit tests next door pin the filter's logic. This pins the thing the
 * logic is for: that the two URLs partition the venue, that a filtered scene
 * says it is filtered, and that a stranger's entity of an unknown type is
 * placed and named rather than dropped. It also writes the screenshot pair.
 */

const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';
const OUT = process.env.P23_OUT || join(tmpdir(), 'spatialdds-basis');

// South-east of the basin, high enough to hold the whole model in frame.
const VIEW = { lon: -97.73920, lat: 30.28345, height: 205, heading: 327, pitch: -36 };

async function capture(page: any, url: string, name: string) {
  await page.goto(url);
  await page.waitForFunction(() => (window as any).__viewer, null, { timeout: 60_000 });
  const clear = async () => page.evaluate(() =>
    document.querySelectorAll('.cesium-widget-errorPanel').forEach((e: any) => e.remove()));
  await clear();

  await page.evaluate(() => document.getElementById('btnLocalize')?.click());
  await expect
    .poll(async () => Number(/alt=([\d.]+)m/.exec(
      (await page.locator('#readout').getAttribute('data-geopose')) || '')?.[1] ?? NaN),
    { timeout: 40_000 })
    .toBeLessThan(1000);
  await clear();
  await page.evaluate(() => document.getElementById('btnDiscover')?.click());
  await page.waitForFunction(
    () => (window as any).__appLogs?.some((l: string) => l.startsWith('discover:items')),
    null, { timeout: 40_000 });

  // The demo starts the camera at the localized prior, which is eye level in
  // the basin: right for the demo, useless for a capture.
  await page.evaluate((v: any) => {
    const viewer = (window as any).__viewer;
    // Cesium is bundled, not global; borrow Cartesian3 off a live instance.
    const Cartesian3: any = viewer.camera.position.constructor;
    const rad = (deg: number) => deg * Math.PI / 180;
    viewer.camera.setView({
      destination: Cartesian3.fromDegrees(v.lon, v.lat, v.height),
      orientation: { heading: rad(v.heading), pitch: rad(v.pitch), roll: 0 }
    });
  }, VIEW);
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    const prims = v.scene.primitives;
    for (let i = 0; i < prims.length; i += 1) {
      const p: any = prims.get(i);
      if (p && 'tilesLoaded' in p && !p.tilesLoaded) {
        return false;
      }
    }
    return true;
  }, null, { timeout: 90_000 });
  await page.waitForTimeout(2500);
  await clear();

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
  return { drawn, readout, ids: [...new Set(drawn.map((d: any) => d.id))].sort() };
}

test('the basis pair partitions the venue, and a stranger is placed either way',
  async ({ page, request }) => {
    test.setTimeout(300_000);
    let model: any = { entities: [] };
    try {
      model = await (await request.get(`${BRIDGE_URL}/v1/model`, { timeout: 4000 })).json();
    } catch {
      model = { entities: [] };
    }
    test.skip((model.entities || []).length === 0,
              'no model publishing — run with SPATIALDDS_MODEL_LAYER=1');
    test.skip(!model.entities.some((e: any) => e.entity_id === 'ent:gnome:visitor'),
              'no second publisher — run scripts/gnome_publisher.py');

    const all = await capture(page, '/?debug=1', 'default-all');
    const observed = await capture(page, '/?debug=1&basis=observed', 'basis-observed');
    const authored = await capture(page, '/?debug=1&basis=authored', 'basis-authored');

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

    // The pair.
    expect(observed.ids).toEqual(['ent:fountain:littlefield']);
    expect(authored.ids).toEqual(
      ['ent:duck:catalog-pose', 'ent:duck:east', 'ent:duck:west', 'ent:gnome:visitor']);
    // Disjoint and complete: every entity is in exactly one of the two views,
    // and between them they account for the whole model.
    expect([...observed.ids, ...authored.ids].sort()).toEqual(all.ids);

    // A filtered scene says so, rather than presenting a partial world as
    // the whole one.
    expect(observed.readout).toContain('basis=OBSERVED — 4 hidden');
    expect(authored.readout).toContain('basis=AUTHORED — 1 hidden');
    expect(all.readout).not.toContain('hidden');
  });
