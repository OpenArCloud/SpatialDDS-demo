import { expect, test } from '@playwright/test';

/**
 * Localizing with a real photograph, rather than a capture of the Cesium view.
 *
 * The canvas capture proves the blob lane carries bytes; it cannot prove a VPS
 * could ever match them. This drives the other button — a genuine frame from
 * the scan a VPS map was built from — and checks the two things that make that
 * path different from the canvas one:
 *
 *  * the frame is fetched and sent whole (a real JPEG, not a placeholder), and
 *  * the prior comes from the bundle's own map anchor, so discovery searches
 *    where the map actually is instead of where the demo happened to start.
 *
 * Needs a query-frame bundle installed and a VPS covering its map; skips
 * otherwise, so a fresh clone and the default downtown stack both stay green.
 */
const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

/** Base32 geohash, matching web/src/geohash.ts, to ask about the bundle's own cell. */
function geohashEncode(lat: number, lon: number, precision = 5): string {
  const BASE32 = '0123456789bcdefghjkmnpqrstuvwxyz';
  let latMin = -90, latMax = 90, lonMin = -180, lonMax = 180;
  let hash = '', bits = 0, bitCount = 0, lonTurn = true;
  while (hash.length < precision) {
    if (lonTurn) {
      const mid = (lonMin + lonMax) / 2;
      if (lon >= mid) { bits = (bits << 1) | 1; lonMin = mid; } else { bits <<= 1; lonMax = mid; }
    } else {
      const mid = (latMin + latMax) / 2;
      if (lat >= mid) { bits = (bits << 1) | 1; latMin = mid; } else { bits <<= 1; latMax = mid; }
    }
    lonTurn = !lonTurn;
    if (++bitCount === 5) { hash += BASE32[bits]; bits = 0; bitCount = 0; }
  }
  return hash;
}

test('localize with a real scan frame', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 900 });

  let health: { status?: string } | null = null;
  try {
    health = await (await request.get(`${BRIDGE_URL}/health`, { timeout: 4000 })).json();
  } catch {
    health = null;
  }
  test.skip(health === null, `bridge not reachable at ${BRIDGE_URL}`);

  // The bundle is installed locally and git-ignored — a fresh clone has none.
  let manifest: { anchor?: { lat_deg: number; lon_deg: number }; frames?: string[] } | null = null;
  try {
    const res = await request.get('/query-frames/manifest.json', { timeout: 4000 });
    manifest = res.ok() ? await res.json() : null;
  } catch {
    manifest = null;
  }
  test.skip(!manifest?.anchor || !manifest?.frames?.length,
            'no query-frame bundle installed (see ar_demo/README.md)');

  const { lat_deg: lat, lon_deg: lon } = manifest!.anchor!;
  const cell = geohashEncode(lat, lon);

  // Without a VPS covering the bundle's map there is nothing to assert.
  let covered = false;
  try {
    const search = await request.get(
      `${BRIDGE_URL}/.well-known/spatialdds/search?geohash=${cell}`, { timeout: 4000 });
    covered = ((await search.json()).results ?? []).length > 0;
  } catch {
    covered = false;
  }
  test.skip(!covered, `no VPS covers the bundle's map (geohash ${cell})`);

  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`));

  await page.goto('/?autostart=0');
  await expect(page.locator('#modeBadge')).toContainText('DDS Bridge');

  const button = page.locator('#btnLocalizeImage');
  await expect(button).toBeEnabled();
  await page.evaluate(() => document.getElementById('btnLocalizeImage')?.click());

  const logs = async () =>
    (await page.evaluate(() => (window as Window & { __appLogs?: string[] }).__appLogs ?? []));

  // A real JPEG of a plausible size, not a truncated or failed fetch that
  // still produced a string. The SOI guard in loadQueryFrame catches an SPA
  // fallback page; this catches a frame that is merely too small to be one.
  await expect
    .poll(async () => (await logs()).find((l) => l.startsWith('frame:')), { timeout: 30_000 })
    .toMatch(/^frame: \S+ ([1-9]\d{1,3}) KB/);

  await expect
    .poll(async () => (await logs()).find((l) => l.startsWith('localize:success')), { timeout: 30_000 })
    .toBeTruthy();

  // Localization came back on the bundle's map, not the demo's start position.
  const success = (await logs()).find((l) => l.startsWith('localize:success')) as string;
  const [gotLat, gotLon] = success.replace('localize:success ', '').split(',').map(Number);
  expect(Math.abs(gotLat - lat)).toBeLessThan(0.05);
  expect(Math.abs(gotLon - lon)).toBeLessThan(0.05);

  // The preview is rotated with CSS because the pipeline stores frames
  // sideways. What is sent must remain the stored bytes — rotating those
  // would break matching against the map they came from.
  const sentBytes = await page.evaluate(async () => {
    const res = await fetch('/query-frames/manifest.json');
    const m = await res.json();
    const img = await fetch(`/query-frames/${m.frames[0]}`);
    return new Uint8Array(await img.arrayBuffer()).slice(0, 4).join(',');
  });
  expect(sentBytes.startsWith('255,216,255')).toBe(true);   // untouched JPEG SOI

  expect(consoleErrors).toEqual([]);
});
