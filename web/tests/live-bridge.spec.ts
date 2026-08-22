import { expect, test } from '@playwright/test';

/**
 * SpatialDDS 1.7 live smoke: drives the Cesium app against a real
 * HTTP-to-DDS bridge rather than the in-page mocks.
 *
 * Requires the demo stack to be up (VPS + catalog + web bridge on a DDS
 * domain, bridge published on BRIDGE_URL). Skips itself when the bridge is
 * not reachable so the default `npm test` run stays hermetic.
 */
const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

test('live 1.7 bridge: localize + discover against real DDS', async ({ page, request }) => {
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
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`));

  await page.goto('/');
  await expect(page.locator('#cesiumContainer')).toBeVisible();

  // The app probes /health on boot and flips to bridge mode when it answers.
  await expect(page.locator('#modeBadge')).toContainText('DDS Bridge');
  // In bridge mode the app relabels its buttons away from the mock names.
  await expect(page.getByRole('button', { name: 'Localize (DDS)' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Discover Content (DDS)' })).toBeVisible();
  await expect(page.locator('#readout')).toContainText('dds bridge online');

  // Localize round trip: browser -> bridge -> DDS -> mock VPS -> back.
  // A 1.7 GeoPose carries no frame_kind/frame_ref; the client must parse it
  // anyway and render a real pose. Note `toContainText('pose:')` would be
  // satisfied by the initial "pose: none" placeholder, so assert on the
  // rendered coordinates instead.
  await page.evaluate(() => document.getElementById('btnLocalize')?.click());
  // Poll rather than assert once: the readout already shows an Austin lat/lon
  // for the initial camera pose, so only the altitude collapsing from the
  // ~2e7 m default to a ground-level value proves the VPS answered.
  await expect
    .poll(async () => {
      const text = (await page.locator('#readout').textContent()) || '';
      return Number(/alt=([\d.]+)m/.exec(text)?.[1] ?? NaN);
    }, { timeout: 20_000 })
    .toBeLessThan(1000);
  await expect(page.locator('#readout')).toContainText(/GeoPose: lat=30\.28\d+ lon=-97\.73\d+/);

  // Catalog discovery through the bridge's structured kind_in filter.
  await page.evaluate(() => document.getElementById('btnDiscover')?.click());
  await expect(page.locator('#readout')).toContainText(/items:\s*[1-9]/);

  expect(consoleErrors, `console errors: ${consoleErrors.join(' | ')}`).toEqual([]);
});
