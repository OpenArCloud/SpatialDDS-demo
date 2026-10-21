import { expect, test } from '@playwright/test';
import { BINDING_URL, SCENE, VIEW, look } from './model-stack.helpers';

/**
 * The venue draws with the AR stack down.
 *
 * **The point of this spec is what is NOT running.** The Open World Model's
 * data plane has been 1.8 for a while, but until the scene published its own
 * georeference the view still could not draw a single entity without the 1.7
 * AR stack: the frame transform came from the catalogue server's `Announce`,
 * read through the bridge's `/v1/frames`, and the model load was gated behind
 * a VPS localization. Both are gone. `model_service` publishes where
 * `map/ut-littlefield-fountain` sits on the earth, the binding serves it over
 * Appendix N.3, and the page loads the model once its boot has settled —
 * whether or not a localization happened — rather than only behind a Localize
 * that may never come.
 *
 * So this runs only when the binding is up and the 1.7 bridge is **not**,
 * which is the configuration the claim is about. A passing run with the AR
 * stack up would prove nothing new — every other spec here already covers
 * that — so the skip condition is the test, as much as the assertions are.
 * It arms itself when that configuration appears and skips with its reason
 * otherwise, rather than needing a flag somebody has to remember.
 *
 * The camera is set directly with `look()`. That is not a workaround for a
 * missing capability: localizing a camera is a different question from
 * placing a venue, it is still the VPS's, and it is still gated on OpenVPS's
 * own 1.8 migration. What this asserts is placement given a viewpoint.
 */
const BRIDGE_URL =
  process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

test('the venue places every entity with the AR stack down', async ({
  page, request
}) => {
  test.setTimeout(180_000);

  // Is this the standalone configuration? Both halves matter.
  let entities: any[] | null = null;
  try {
    const r = await request.get(`${BINDING_URL}/${SCENE}/world/entity/v1`,
                                { timeout: 4000 });
    entities = r.ok() ? await r.json() : null;
  } catch {
    entities = null;
  }
  test.skip(!entities || !entities.length,
            `no venue on the binding at ${BINDING_URL}`);

  let bridgeUp = false;
  try {
    const r = await request.get(`${BRIDGE_URL}/health`, { timeout: 3000 });
    bridgeUp = r.ok();
  } catch {
    bridgeUp = false;
  }
  test.skip(bridgeUp,
            `the 1.7 bridge is answering at ${BRIDGE_URL}; this spec is about `
            + 'the configuration where it is not');

  // The georeference has to be the scene's, because there is no other source.
  const frames = await (await request.get(
    `${BINDING_URL}/${SCENE}/frames/frame_transform/v1`)).json();
  expect(frames.length, 'the scene must publish its own georeference')
    .toBeGreaterThan(0);

  const placed = (entities as any[]).filter((e) => e.has_pose);
  expect(placed.length, 'the venue must have placed entities to draw')
    .toBeGreaterThan(0);

  await page.goto('/?debug=1');
  await page.waitForFunction(() => (window as any).__viewer, null,
                             { timeout: 60_000 });
  await page.evaluate(() => document.querySelectorAll('.cesium-widget-errorPanel')
    .forEach((e: any) => e.remove()));

  // No Localize, and none available. The model must arrive on its own.
  await page.waitForFunction(
    () => (window as any).__appLogs?.some(
      (l: string) => l.startsWith('model:loaded')),
    null, { timeout: 60_000 });

  const logs: string[] = await page.evaluate(
    () => (window as any).__appLogs || []);

  // Nothing failed to resolve a frame. This is the assertion the whole batch
  // exists for: before it, every one of these lines was present.
  const unresolved = logs.filter((l) => l.startsWith('model:unresolved'));
  expect(unresolved, `entities could not resolve their frame: `
                   + `${unresolved.join(' | ')}`).toEqual([]);

  const loaded = logs.find((l) => l.startsWith('model:loaded')) || '';
  const count = Number(/model:loaded (\d+)/.exec(loaded)?.[1] ?? -1);
  expect(count, `"${loaded}" should account for all ${placed.length} placed `
              + 'entities').toBe(placed.length);

  // And they are really in the scene, not merely counted.
  await look(page, VIEW);
  const drawn: string[] = await page.evaluate(() => {
    const v = (window as any).__viewer;
    return v.entities.values.map((e: any) => String(e.id));
  });
  for (const entity of placed) {
    expect(drawn.some((id) => id === entity.entity_id
                           || id.startsWith(`${entity.entity_id}-`)),
           `${entity.entity_id} is published and placed but not in the scene`)
      .toBe(true);
  }

  // **And it is live, not a still.** Placement was the stated acceptance, and
  // checking only that would have missed a venue that draws once and then
  // freezes: `connectModelStream` consulted the 1.7 bridge's health probe
  // before opening the binding's own sockets, so with the AR stack down the
  // N.5 lanes never connected at all. Found by asking this question.
  await page.waitForFunction(
    () => (window as any).__appLogs?.some(
      (l: string) => l.startsWith('model:entity subscribed')),
    null, { timeout: 30_000 });
  await page.waitForFunction(
    () => (window as any).__appLogs?.some(
      (l: string) => l.startsWith('model:pose subscribed')),
    null, { timeout: 30_000 });

  // Something actually moves, which only the fast lane can deliver. The
  // mover is running in this configuration; if it were not, this would be the
  // wrong assertion to make.
  await page.waitForFunction(
    () => (window as any).__appLogs?.some(
      (l: string) => l.startsWith('model:moved')),
    null, { timeout: 60_000 });

  // The asset half degrades and says so: a catalogue reference cannot resolve
  // with no catalogue, and an entity's appearance is not its position.
  const skipped = logs.find((l) => l.startsWith('catalog:by-id skipped'));
  console.log(`  ${count} entities placed from the scene's own georeference, `
            + `no bridge`);
  if (skipped) {
    console.log(`  ${skipped}`);
  }
});
