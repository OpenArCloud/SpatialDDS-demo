import { expect, test } from '@playwright/test';

const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

test('REST overlay records the exchanges the app makes', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let health: { status?: string } | null = null;
  try {
    health = await (await request.get(`${BRIDGE_URL}/health`, { timeout: 4000 })).json();
  } catch { health = null; }
  test.skip(health === null, 'bridge not reachable');

  await page.goto('/');
  await expect(page.locator('#modeBadge')).toContainText('DDS Bridge');

  // Both panels start hidden.
  await expect(page.locator('#restOverlay')).toHaveClass(/hidden/);
  await page.getByRole('button', { name: /REST Messages: Off/ }).click();
  await expect(page.locator('#restOverlay')).not.toHaveClass(/hidden/);

  // /health was recorded during boot, before the panel was opened.
  await expect(page.locator('#restOverlayBody')).toContainText('GET /health -> 200');

  await page.evaluate(() => document.getElementById('btnLocalize')?.click());
  await expect(page.locator('#restOverlayBody')).toContainText(
    '/.well-known/spatialdds/search?geohash=', { timeout: 20_000 });
  await expect(page.locator('#restOverlayBody')).toContainText(
    'POST /v1/localize -> 200', { timeout: 20_000 });
  // The discovered service is named in the request body.
  await expect(page.locator('#restOverlayBody')).toContainText('svc:vps:demo/austin-downtown');

  // The query frame: a real JPEG captured from the Cesium canvas, shown in the
  // panel as its size because the base64 itself is tens of thousands of chars.
  // Its presence here is the browser-side proof that the request carries real
  // imagery rather than the placeholder the bridge used to invent.
  const restText = (await page.locator('#restOverlayBody').textContent()) || '';
  const sizeMatch = /"query_image":\s*"<(\d+) KB base64/.exec(restText);
  expect(sizeMatch, 'localize request carries a captured query image').not.toBeNull();
  expect(Number(sizeMatch![1])).toBeGreaterThan(0);

  await page.evaluate(() => document.getElementById('btnDiscover')?.click());
  await expect(page.locator('#restOverlayBody')).toContainText(
    'POST /v1/catalog/query -> 200', { timeout: 20_000 });

  // Both windows open together — the point of having two.
  await page.getByRole('button', { name: /DDS Messages: Off/ }).click();
  await expect(page.locator('#ddsOverlay')).not.toHaveClass(/hidden/);
  await expect(page.locator('#restOverlay')).not.toHaveClass(/hidden/);

  console.log((await page.locator('#restOverlayBody').textContent())?.slice(0, 900));
});
