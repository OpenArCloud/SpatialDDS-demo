import { expect, test } from '@playwright/test';
import { BINDING_URL, SCENE, VIEW, look } from './model-stack.helpers';

/**
 * The venue's contents are not drawn from orbit.
 *
 * The page opens at 20,000 km. Billboards are fixed-pixel-size, so without a
 * distance condition three ducks, a TurtleBot, a gnome and the pond extents
 * rendered at full size over Texas on the globe view -- legible from a
 * continental scale, which is not meaningful for 0.3 m objects in a 30 m
 * venue.
 *
 * **Picked rather than inspected.** Reading back
 * `entity.billboard.distanceDisplayCondition` would only confirm the property
 * was assigned; it would pass just as happily if Cesium ignored it, or if the
 * range were set to something absurd. `scene.pick` at the venue's own
 * projected screen position asks the renderer what is actually there.
 *
 * Skips with its reason when the binding is absent, so it arms itself once
 * the stack is up rather than waiting to be remembered.
 */
test('the venue draws close up and not from orbit', async ({ page, request }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1280, height: 900 });

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

  await page.goto('/?debug=1');
  await page.waitForFunction(() => (window as any).__viewer, null,
                             { timeout: 90_000 });
  await page.waitForFunction(
    () => (window as any).__appLogs?.some(
      (l: string) => l.startsWith('model:loaded')),
    null, { timeout: 90_000 });

  /** What the renderer reports at the venue's projected position. */
  // The bundle does not put Cesium on `window`, so nothing here may import
  // it: reach types through live objects, the way `look()` already does.
  const pickAtVenue = async () => page.evaluate(() => {
    const v = (window as any).__viewer;
    // A real venue entity, not a hand-picked coordinate: whatever the model
    // layer placed first.
    const subject = v.entities.values.find(
      (e: any) => String(e.id).startsWith('ent:') && e.position);
    if (!subject) return { error: 'no venue entity in the collection' };
    const now = v.clock.currentTime;
    const world = subject.position.getValue(now);
    if (!world) return { error: `${subject.id} has no position at this time` };
    // `Scene` can do this itself, which avoids needing the SceneTransforms
    // module at all.
    const screen = v.scene.cartesianToCanvasCoordinates(world);
    if (!screen) return { subject: String(subject.id), offscreen: true };
    v.scene.render();
    const hits = v.scene.drillPick(screen, 12) || [];
    return {
      subject: String(subject.id),
      screen: { x: Math.round(screen.x), y: Math.round(screen.y) },
      venueHits: hits
        .map((h: any) => String(h?.id?.id ?? h?.id ?? ''))
        .filter((s: string) => s.startsWith('ent:'))
    };
  });

  // Orbit first: this is where the page actually opens.
  await page.evaluate(() => {
    const v = (window as any).__viewer;
    const Cartesian3: any = v.camera.position.constructor;
    v.camera.setView({
      destination: Cartesian3.fromDegrees(-97.73974, 30.28396, 20_000_000)
    });
  });
  await page.waitForTimeout(2500);
  const far = await pickAtVenue();
  console.log(`  from 20,000 km: ${JSON.stringify(far)}`);
  expect(far.error, `probe failed: ${far.error}`).toBeUndefined();
  expect(far.venueHits || [],
         'no venue entity may be drawn from orbit').toEqual([]);

  // Then close up, so the guard above cannot pass by hiding everything.
  await look(page, VIEW);
  await page.waitForTimeout(2500);
  const near = await pickAtVenue();
  console.log(`  from ${VIEW.height} m: ${JSON.stringify(near)}`);
  expect(near.error, `probe failed: ${near.error}`).toBeUndefined();
  expect((near.venueHits || []).length,
         'the venue must still be drawn at walking distance -- otherwise the '
         + 'orbit assertion above is satisfied by drawing nothing at all')
    .toBeGreaterThan(0);
});
