import { expect, test } from '@playwright/test';

/**
 * The aligner has to do three things before it is any use: load Cesium, parse
 * the map's point cloud, and emit a transform in the shape OpenVPS stores.
 * A cloud is installed locally and git-ignored, so skip when absent.
 */
test('map aligner loads a cloud and emits a transform', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 900 });

  const ply = await request.get('/aligner/fountain2.ply').catch(() => null);
  test.skip(!ply || !ply.ok(), 'no aligner point cloud installed');

  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));

  await page.goto('/aligner.html');

  // Status reports the point count once the PLY is parsed.
  await expect(page.locator('#status')).toContainText(/points · anchor/, { timeout: 60_000 });

  // The emitted transform is what gets POSTed to OpenVPS, so pin its shape.
  const out = JSON.parse(await page.locator('#out').innerText());
  expect(Object.keys(out).sort()).toEqual(['height', 'latitude', 'longitude', 'matrix']);
  expect(out.matrix).toHaveLength(4);
  expect(out.matrix[3]).toEqual([0, 0, 0, 1]);

  // Yaw must actually change the matrix, or the control is decorative.
  const before = JSON.stringify(out.matrix);
  await page.locator('#yaw').fill('90');
  await page.locator('#yaw').dispatchEvent('input');
  const after = await page.locator('#out').innerText();
  expect(JSON.parse(after).matrix).not.toEqual(JSON.parse(before));

  expect(errors.filter((e) => !/Ion|token|401|403/i.test(e))).toEqual([]);
});
