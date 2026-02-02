import { expect, test } from '@playwright/test';

test('smoke: cesium app loads and responds to mock endpoints', async ({ page }) => {
  await page.goto('/');

  await expect(page.locator('#cesiumContainer')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Localize (Mock VPS)' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Discover Content (Mock Catalog)' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Clear' })).toBeVisible();

  await expect(page.locator('#readout')).toContainText('status: ready');

  await page.evaluate(() => {
    document.getElementById('btnLocalize')?.click();
  });
  await expect(page.locator('#readout')).toContainText('pose:');

  await page.evaluate(() => {
    document.getElementById('btnDiscover')?.click();
  });
  await expect(page.locator('#readout')).toContainText(/items:\s*[1-9]/);
});
