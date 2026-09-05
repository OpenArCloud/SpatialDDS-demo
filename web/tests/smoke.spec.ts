import { expect, test } from '@playwright/test';

/**
 * Mock-mode smoke: the app with no DDS bridge behind it.
 *
 * The app probes the bridge on boot and only falls back to the in-page mocks
 * when nothing answers, so this test skips itself when a bridge IS running —
 * the button labels and readout differ in that case. The live counterpart is
 * tests/live-bridge.spec.ts.
 */
const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

test('smoke: cesium app loads and responds to mock endpoints', async ({ page, request }) => {
  // Probe first, then skip. test.skip() signals by throwing, so calling it
  // inside the try block would have the catch swallow it and run on anyway.
  let bridgeUp = false;
  try {
    bridgeUp = (await request.get(`${BRIDGE_URL}/health`, { timeout: 2000 })).ok();
  } catch {
    bridgeUp = false; // no bridge — mock mode, which is what this test wants
  }
  test.skip(bridgeUp, `bridge is up at ${BRIDGE_URL}; this test covers mock mode`);

  // Headless Cesium needs a real canvas size, else WebGL raises
  // "Expected width to be greater than 0".
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.goto('/?autostart=0');

  await expect(page.locator('#cesiumContainer')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Localize (Mock VPS)' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Discover Content (Mock Catalog)' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Clear' })).toBeVisible();

  // Bridge probe resolved and the app settled into mock mode. (The previous
  // assertion here looked for 'status: ready', a string the app never emits —
  // the readout starts at 'status: idle' and is then replaced wholesale by
  // renderReadout's GeoPose block.)
  await expect(page.locator('#modeBadge')).toContainText('Mock Mode');
  await expect(page.locator('#readout')).toContainText('message: mock mode');

  // Mock localize returns a fixed pose ~18 m up. Poll for the altitude to drop
  // from the ~2e7 m default camera height: asserting toContainText('pose:')
  // would be satisfied by the initial 'pose: none' placeholder and so would
  // pass whether or not localize ran at all.
  await page.evaluate(() => {
    document.getElementById('btnLocalize')?.click();
  });
  await expect
    .poll(async () => {
      const text = (await page.locator('#readout').getAttribute('data-geopose')) || '';
      return Number(/alt=([\d.]+)m/.exec(text)?.[1] ?? NaN);
    }, { timeout: 20_000 })
    .toBeLessThan(1000);
  await expect(page.locator('#readout'))
    .toHaveAttribute('data-geopose', /GeoPose: lat=30\.28\d+ lon=-97\.73\d+/);

  await page.evaluate(() => {
    document.getElementById('btnDiscover')?.click();
  });
  await expect(page.locator('#readout')).toContainText(/items:\s*[1-9]/);
});
