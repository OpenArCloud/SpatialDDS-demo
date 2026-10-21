import { expect, test } from '@playwright/test';

/**
 * The fresh-clone localize: no real bundle, so the synthetic card stands in.
 *
 * A fresh clone has no `web/public/query-frames/` (photographs of a real
 * place, git-ignored), so until the synthetic bundle existed it could not
 * reach the localize round trip from the UI at all. This routes the real
 * bundle's directory to 404 -- what a fresh clone serves, whatever happens to
 * be installed on this machine -- and walks the README's path:
 *
 *  * the card is labelled SYNTHETIC where a reader meets it (picker, caption,
 *    button title), not only in its pixels;
 *  * pressing "Localize with Image" makes exactly the two REST calls and puts
 *    the five bus messages on the wire;
 *  * the service answers VPS_SUCCESS at the venue's declared anchor -- read
 *    from the synthetic manifest, which is generated from the anchor config;
 *  * the pose is applied: the readout leaves the start position for the fix.
 *
 * Needs the demo stack; skips when the bridge is not reachable.
 */
const BRIDGE_URL = process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';

test('fresh clone: synthetic frame localizes at the venue anchor', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 900 });

  let health: { status?: string } | null = null;
  try {
    health = await (await request.get(`${BRIDGE_URL}/health`, { timeout: 4000 })).json();
  } catch {
    health = null;
  }
  test.skip(health === null, `bridge not reachable at ${BRIDGE_URL}`);

  const manifest = await (await request.get('/query-frames-synthetic/manifest.json')).json();
  expect(manifest.synthetic).toBe(true);
  const anchor = manifest.anchor as { lat_deg: number; lon_deg: number };

  // A fresh clone's view of the real bundle: absent.
  await page.route('**/query-frames/**', (r) => r.fulfill({ status: 404, body: '' }));

  const errors: string[] = [];
  page.on('pageerror', (err) => errors.push(err.message));
  const calls: string[] = [];
  page.on('request', (r) => {
    if (r.url().startsWith(BRIDGE_URL)) calls.push(`${r.method()} ${new URL(r.url()).pathname}`);
  });

  await page.goto('/?autostart=0');
  await expect(page.locator('#modeBadge')).toContainText('DDS Bridge');

  const button = page.getByRole('button', { name: 'Localize with Image' });
  await expect(button).toBeEnabled();
  await expect(button).toHaveAttribute('title', /SYNTHETIC/);
  await expect(page.locator('#frameSynthetic')).toBeVisible();
  await expect(page.locator('#frameSynthetic')).toHaveText(/SYNTHETIC/);
  await expect(page.locator('#frameSelect option')).toHaveText([/synthetic.*\(SYNTHETIC\)/]);

  await page.getByRole('button', { name: /DDS Messages/ }).click();
  const ddsBefore = (await page.locator('#ddsOverlayBody').innerText()).length;
  const mark = calls.length;
  await button.click();

  const logs = async () =>
    (await page.evaluate(() => (window as Window & { __appLogs?: string[] }).__appLogs ?? []));
  await expect
    .poll(async () => (await logs()).filter((l) => l.startsWith('localize:')).at(-1), { timeout: 60_000 })
    .toMatch(/^localize:success /);

  const frameLine = (await logs()).find((l) => l.startsWith('frame:')) ?? '';
  expect(frameLine).toMatch(/^frame: synthetic-query-frame-\d+\.jpg \d+ KB \(SYNTHETIC/);

  // The fix is the service's, near the anchor the request carried. The mock
  // VPS jitters by up to 5e-5 degrees, so 1e-4 is generous and still metres.
  const fix = (await logs()).filter((l) => l.startsWith('localize:success')).at(-1) as string;
  const [lat, lon] = fix.replace('localize:success ', '').split(',').map(Number);
  expect(Math.abs(lat - anchor.lat_deg)).toBeLessThan(1e-4);
  expect(Math.abs(lon - anchor.lon_deg)).toBeLessThan(1e-4);

  // Applied, not only logged: the readout is at the fix, not the start position.
  await expect
    .poll(async () => {
      const text = (await page.locator('#readout').getAttribute('data-geopose')) || '';
      const m = /lat=([\d.-]+) lon=([\d.-]+)/.exec(text);
      return m ? Math.abs(Number(m[1]) - anchor.lat_deg) < 1e-4 && Math.abs(Number(m[2]) - anchor.lon_deg) < 1e-4 : false;
    }, { timeout: 15_000 })
    .toBe(true);

  expect(calls.slice(mark)).toEqual([
    'GET /.well-known/spatialdds/search',
    'POST /v1/localize',
  ]);

  const dds = (await page.locator('#ddsOverlayBody').innerText()).slice(ddsBefore);
  const kinds = dds.split('\n')
    .map((l) => /^\[[^\]]+\] (\w+) spatialdds\//.exec(l)?.[1])
    .filter(Boolean);
  // In order, as a subsequence: a successful localize also starts the model
  // bootstrap (localizeWith), whose own discovery lands on the same panel.
  const want = ['coverage_query', 'coverage_response', 'coverage_response', 'vps_query', 'vps_response'];
  let at = 0;
  for (const k of kinds) if (at < want.length && k === want[at]) at++;
  expect(at, `bus messages seen: ${kinds.join(', ')}`).toBe(want.length);
  expect(kinds.filter((k) => k === 'vps_query')).toHaveLength(1);
  expect(kinds.filter((k) => k === 'vps_response')).toHaveLength(1);

  expect(errors).toEqual([]);
});
