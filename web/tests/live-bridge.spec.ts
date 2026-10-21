import { expect, test } from '@playwright/test';

/**
 * SpatialDDS live smoke: drives the Cesium app against a real
 * HTTP-to-DDS bridge rather than the in-page mocks.
 *
 * Requires the demo stack to be up (VPS + catalog + web bridge on a DDS
 * domain, bridge published on BRIDGE_URL). Skips itself when the bridge is
 * not reachable so the default `npm test` run stays hermetic.
 */
const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

test('live bridge: localize + discover against real DDS', async ({ page, request }) => {
  // Headless Cesium needs a real canvas size, else WebGL raises
  // "Expected width to be greater than 0".
  await page.setViewportSize({ width: 1280, height: 900 });

  // Probe first, then skip: test.skip() signals by throwing, so it must not be
  // called from inside a try whose catch would swallow it.
  let health: { status?: string } | null = null;
  try {
    health = await (await request.get(`${BRIDGE_URL}/health`, { timeout: 4000 })).json();
  } catch {
    health = null;
  }
  test.skip(health === null, `bridge not reachable at ${BRIDGE_URL}`);
  expect(health?.status).toBe('ok');

  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const url = msg.location()?.url || '';
      consoleErrors.push(url ? `${msg.text()} [${url}]` : msg.text());
    }
  });
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`));

  await page.goto('/?autostart=0');
  await expect(page.locator('#cesiumContainer')).toBeVisible();

  // The app probes /health on boot and flips to bridge mode when it answers.
  await expect(page.locator('#modeBadge')).toContainText('DDS Bridge');
  // In bridge mode the catalogue button relabels to DDS. The plain Localize
  // button does *not*: it is an explicit mock in both modes now, because the
  // request it used to make -- a Cesium render sent to a photogrammetric VPS
  // -- could only ever be refused. The real path is "Localize with Image".
  await expect(page.getByRole('button', { name: 'Localize (Mock)' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Localize with Image' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Discover Content (DDS)' })).toBeVisible();
  await expect(page.locator('#readout')).toContainText('dds bridge online');

  // Localize round trip: browser -> bridge -> DDS -> mock VPS -> back.
  // A 1.7 GeoPose carries no frame_kind/frame_ref; the client must parse it
  // anyway and render a real pose. Note `toContainText('pose:')` would be
  // satisfied by the initial "pose: none" placeholder, so assert on the
  // rendered coordinates instead.
  // **Which button, and why it is not always the same one.**
  //
  // This spec used to press `btnLocalize` unconditionally and pass. It was
  // passing on a defect: that button sends a *Cesium render* as the query
  // image, a real photogrammetric VPS answers `VPS_FAILED`, and the page then
  // logged `localize:success` and flew the camera to the prior anyway -- whose
  // altitude is ~145 m, which satisfied the altitude assertion below. So the
  // round trip this spec exists to prove was never actually proven against a
  // real service; only against the demo's own mock, which ignores imagery and
  // succeeds regardless.
  //
  // With a query-frame bundle installed there is a real photograph to send, so
  // prefer it: that is the request a VPS can answer. Without one, fall back --
  // against the mock stack the plain button is still a true round trip.
  const imageButtonUsable = await page.evaluate(
    () => !(document.getElementById('btnLocalizeImage') as HTMLButtonElement
            | null)?.disabled);
  // The plain button is an explicit mock now, so it is not a fallback for
  // this spec: pressing it would localize without touching DDS and this would
  // pass having proved nothing. With no bundle there is no real localize path
  // from the UI, which is a skip and not a pass.
  test.skip(!imageButtonUsable,
            'no query-frame bundle installed, so there is no photograph to '
            + 'send and the only real localize path is unavailable');
  console.log('  localizing via btnLocalizeImage (real photograph)');
  await page.evaluate(
    () => document.getElementById('btnLocalizeImage')?.click());
  // Poll rather than assert once: the readout already shows an Austin lat/lon
  // for the initial camera pose, so only the altitude collapsing from the
  // ~2e7 m default to a ground-level value proves the VPS answered.
  await expect
    .poll(async () => {
      const text = (await page.locator('#readout').getAttribute('data-geopose')) || '';
      return Number(/alt=([\d.]+)m/.exec(text)?.[1] ?? NaN);
      // 90s, not 45s, and not because the request got slower: the queue
      // got more expensive. The bridge serialises localize requests behind
      // one lock and several specs need a real one -- this, the overlay
      // tests, and every model-stack page that autostarts. Against a local
      // stack each is ~0.1 s and the depth never showed; against a bridge
      // reached over an SSM tunnel to EC2 each is ~3 s, so the same queue
      // costs about thirty times as much and this spec -- which happens to go
      // first -- crossed 45 s at 46.4 s. Measured, not guessed: it passes in
      // 11.8 s run alone.
      //
      // `?autostart=0` already spares the pages that do not need a
      // localization; the ones that do have to wait their turn.
    }, { timeout: 90_000 })
    .toBeLessThan(1000);
  await expect(page.locator('#readout'))
    .toHaveAttribute('data-geopose', /GeoPose: lat=30\.28\d+ lon=-97\.73\d+/);

  // The service has to have said it localized. Without this the altitude
  // assertion above can be met by a camera flown to a prior, which is how this
  // spec passed against a `VPS_FAILED` for as long as the page logged every
  // response as a success.
  const localizeLines: string[] = await page.evaluate(
    () => ((window as any).__appLogs || []).filter(
      (l: string) => l.startsWith('localize:')));
  expect(localizeLines.filter((l) => l.startsWith('localize:failed')),
         `the service reported no fix: ${localizeLines.join(' | ')}`)
    .toEqual([]);
  expect(localizeLines.some((l) => l.startsWith('localize:success')),
         `no successful localization was reported: ${localizeLines.join(' | ')}`)
    .toBe(true);

  // Catalog discovery through the bridge's structured kind_in filter.
  await page.evaluate(() => document.getElementById('btnDiscover')?.click());
  await expect(page.locator('#readout')).toContainText(/items:\s*[1-9]/);

  expect(consoleErrors, `console errors: ${consoleErrors.join(' | ')}`).toEqual([]);
});
