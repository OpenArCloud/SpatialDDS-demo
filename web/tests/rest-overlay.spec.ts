import { expect, test } from '@playwright/test';

const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

test('REST overlay records the exchanges the app makes', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let health: { status?: string } | null = null;
  try {
    health = await (await request.get(`${BRIDGE_URL}/health`, { timeout: 4000 })).json();
  } catch { health = null; }
  test.skip(health === null, 'bridge not reachable');

  await page.goto('/?autostart=0');
  await expect(page.locator('#modeBadge')).toContainText('DDS Bridge');

  // Both panels start hidden.
  await expect(page.locator('#restOverlay')).toHaveClass(/hidden/);
  await page.getByRole('button', { name: /REST Messages: Off/ }).click();
  await expect(page.locator('#restOverlay')).not.toHaveClass(/hidden/);

  // /health was recorded during boot, before the panel was opened.
  await expect(page.locator('#restOverlayBody')).toContainText('GET /health -> 200');

  // The image button, because the plain one is an explicit mock and makes no
  // HTTP request at all -- there would be no exchange for this panel to record.
  const canSendReal = await page.evaluate(
    () => !(document.getElementById('btnLocalizeImage') as any)?.disabled);
  test.skip(!canSendReal,
            'no query-frame bundle, so no real localize exchange to record');
  await page.evaluate(
    () => document.getElementById('btnLocalizeImage')?.click());
  await expect(page.locator('#restOverlayBody')).toContainText(
    '/.well-known/spatialdds/search?geohash=', { timeout: 20_000 });
  await expect(page.locator('#restOverlayBody')).toContainText(
    'POST /v1/localize -> 200', { timeout: 20_000 });
  // The discovered service is named in the request body.
  //
  // Read from the page rather than written in here. This was the literal
  // `svc:vps:demo/austin-downtown`, which is the *demo's own mock* -- so the
  // assertion could only ever pass against the mock, and failed the moment the
  // stack pointed at a real VPS even though the behaviour under test (the
  // overlay naming the service in the body) was perfectly correct. The subject
  // is "whichever service this page discovered", and that is knowable.
  const discovered: string | null = await page.evaluate(
    () => (((window as any).__appLogs || []).find(
      (l: string) => l.startsWith('discover:vps ')) || '')
      .replace('discover:vps ', '').trim() || null);
  if (discovered && discovered !== 'none — bridge will choose') {
    console.log(`  overlay must name ${discovered}`);
    // 20 s, matching the assertions above rather than the 5 s default. The
    // bridge serialises localize requests behind one lock, so in a full-suite
    // run this exchange can still be queued when the shorter default expires
    // -- which showed up as a spec that passed alone and failed in the suite.
    await expect(page.locator('#restOverlayBody'))
      .toContainText(discovered, { timeout: 20_000 });
  } else {
    // No service discovered: the bridge picks, and the body carries no
    // service_id to assert on. Say so rather than passing quietly.
    console.log('  no service discovered; the bridge chose, so the request '
              + 'body names none');
  }

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
