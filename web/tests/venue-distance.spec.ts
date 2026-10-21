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
 *
 * **KNOWN BLIND: the orbit assertion has never been shown able to fail.**
 * Measured 2026-10-21: with the page's scoping removed outright
 * (`VENUE_VISIBLE_RANGE_M = 1e12`, confirmed on the live page as the pond
 * billboard's `distanceDisplayCondition.far`), every billboard is drawn from
 * orbit and a drill-pick at all 17 venue entities' projected positions still
 * returns no hits. So "no venue entity may be drawn from orbit" currently
 * passes whether or not the scoping works. The commit that introduced it
 * reported "from 20,000 km the renderer reports nothing" without checking that
 * the renderer *could* report something there -- the injection that would
 * have shown this was never run. The near assertion is unaffected: it does
 * report hits, so the venue being drawn at walking distance is genuinely
 * checked. Filed in docs/final-migration-census.md; why picking finds nothing
 * at orbital range (depth against the globe is the first suspect) is the open
 * question, and until it is answered this test proves only the near half.
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

  /**
   * What the renderer reports at **every** venue entity's projected position.
   *
   * This picked one subject until 2026-10-21: "whatever the model layer placed
   * first". That is aim-by-position, and which entity is first depends on
   * which publisher's sample lands first -- it varies per stack cycle, and the
   * gnome's start-up timing is a documented race. When the gnome came first
   * the test failed on a venue that was drawn: the gnome is a billboard-only
   * marker (AUTHORED, no content ref), and a drill-pick at its anchor pixel
   * reported nothing. Every entity is probed now, so the subject is not a
   * matter of arrival order, and the orbit assertion covers all of them
   * instead of one.
   */
  // The bundle does not put Cesium on `window`, so nothing here may import
  // it: reach types through live objects, the way `look()` already does.
  const pickAtVenue = async () => page.evaluate(() => {
    const v = (window as any).__viewer;
    const now = v.clock.currentTime;
    const subjects = v.entities.values.filter(
      (e: any) => String(e.id).startsWith('ent:') && e.position);
    if (!subjects.length) return { error: 'no venue entity in the collection' };
    v.scene.render();
    const probes = subjects.map((subject: any) => {
      const world = subject.position.getValue(now);
      if (!world) return { subject: String(subject.id), unplaced: true, hits: [] };
      const screen = v.scene.cartesianToCanvasCoordinates(world);
      if (!screen) return { subject: String(subject.id), offscreen: true, hits: [] };
      const hits = (v.scene.drillPick(screen, 12) || [])
        .map((h: any) => String(h?.id?.id ?? h?.id ?? ''))
        .filter((s: string) => s.startsWith('ent:'));
      return { subject: String(subject.id), hits };
    });
    return {
      probed: probes.length,
      venueHits: [...new Set(probes.flatMap((p: any) => p.hits))],
      probes,
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
  console.log(`  from 20,000 km: probed ${far.probed}, hits ${JSON.stringify(far.venueHits)}`);
  expect(far.error, `probe failed: ${far.error}`).toBeUndefined();
  expect(far.venueHits || [],
         'no venue entity may be drawn from orbit').toEqual([]);

  // Then close up, so the guard above cannot pass by hiding everything.
  await look(page, VIEW);
  await page.waitForTimeout(2500);
  const near = await pickAtVenue();
  console.log(`  from ${VIEW.height} m: probed ${near.probed}, hits ${JSON.stringify(near.venueHits)}`);
  expect(near.error, `probe failed: ${near.error}`).toBeUndefined();
  expect((near.venueHits || []).length,
         'the venue must still be drawn at walking distance -- otherwise the '
         + 'orbit assertion above is satisfied by drawing nothing at all')
    .toBeGreaterThan(0);
});
