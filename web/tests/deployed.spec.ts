import { test, expect } from '@playwright/test';

/**
 * Drives a *deployed* AR demo, not the dev server.
 *
 * Set DEPLOYED_BASE to run it:
 *
 *     DEPLOYED_BASE=http://my-alb.example.com npx playwright test tests/deployed.spec.ts
 *
 * Skipped without it, so `npm test` stays local and hermetic.
 *
 * This exists because four separate faults reached a deployment while every
 * HTTP check passed, and each rendered as a blank or flat page:
 *
 *   1. Vite `base` left at `/` while the bundle is mounted at `/ar/` — the
 *      HTML serves, every asset 404s.
 *   2. `web/.env.local` swept into the Docker build context — the API URL
 *      baked as `localhost:8088`, so the app silently runs in Mock Mode.
 *   3. No Ion token in a clean build — Cesium's *base imagery* is an Ion
 *      service, so the globe renders as nothing.
 *   4. `define: { CESIUM_BASE_URL }` in vite.config overriding the base —
 *      Cesium fetches its own workers and assets from the site root.
 *
 * None of them produced a non-200 on the page itself. So this asserts what a
 * viewer would actually see: pixels on the globe, live mode rather than mock,
 * and a localization that comes back.
 */

const BASE = process.env.DEPLOYED_BASE;

test.skip(!BASE, 'set DEPLOYED_BASE to test a deployment');

test('a deployed AR demo renders, connects and localizes', async ({ page }) => {
  // Same-origin only. Every fault this test exists for was in *our* asset
  // paths or *our* API; a third party refusing a request is not this test's
  // business. Cesium probes api.cesium.com for its default Ion imagery and
  // gets a 401 when no token is configured — expected, and the OpenStreetMap
  // fallback renders anyway.
  const origin = new URL(BASE!).origin;
  const failures: string[] = [];
  const sameOrigin = (url: string) => url.startsWith(origin);
  page.on('response', r => {
    if (r.status() >= 400 && sameOrigin(r.url())) {
      failures.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`);
    }
  });
  page.on('requestfailed', r => {
    if (sameOrigin(r.url())) {
      failures.push(`${r.failure()?.errorText} ${new URL(r.url()).pathname}`);
    }
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`${BASE}/ar/`);

  // Live, not mock: proves the bundle found its API on its own origin.
  await expect(page.locator('#modeBadge')).toContainText('DDS Bridge', { timeout: 40_000 });
  await expect(page.locator('#cesiumContainer')).toBeVisible();
  await page.waitForTimeout(8000);

  // Pixels, not a status code. A flat canvas is what every asset-path fault
  // above looked like, and all of them served HTTP 200 for the page.
  const distinctColours = await page.evaluate(() => {
    const canvas = document.querySelector('canvas') as HTMLCanvasElement;
    const gl = (canvas.getContext('webgl2') ||
                canvas.getContext('webgl')) as WebGLRenderingContext;
    const size = 200;
    const pixels = new Uint8Array(4 * size * size);
    gl.readPixels(Math.floor(canvas.width / 2) - size / 2,
                  Math.floor(canvas.height / 2) - size / 2,
                  size, size, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    const seen = new Set<number>();
    for (let i = 0; i < pixels.length; i += 4) {
      seen.add((pixels[i] << 16) | (pixels[i + 1] << 8) | pixels[i + 2]);
    }
    return seen.size;
  });
  expect(distinctColours,
         'globe is a flat colour — imagery or Cesium assets did not load')
    .toBeGreaterThan(50);

  // A localization that actually returns: the altitude collapsing from the
  // ~2e7 m default to ground level is what proves the VPS answered.
  await page.evaluate(() => document.getElementById('btnLocalize')?.click());
  await expect
    .poll(async () => {
      const text = (await page.locator('#readout').textContent()) || '';
      return Number(/alt=([\d.]+)m/.exec(text)?.[1] ?? NaN);
    }, { timeout: 40_000 })
    .toBeLessThan(1000);

  // Nothing this deployment serves 404s. Cesium fetches its own assets
  // lazily, so a wrong base surfaces here rather than in the page's status —
  // which is exactly how it went unnoticed the first time.
  expect([...new Set(failures)].slice(0, 10),
         'this deployment failed to serve something it references').toEqual([]);
});
