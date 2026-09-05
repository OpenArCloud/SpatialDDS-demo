import { expect, test } from '@playwright/test';

test('the keyboard card can be put away and brought back', async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto('/?debug=1&autostart=0');
  const hud = page.locator('#hud');
  await expect(hud).toBeVisible();

  await hud.click();
  await expect(hud).toBeHidden();

  await page.keyboard.press('?');
  await expect(hud).toBeVisible();

  // Driving the camera dismisses it too.
  await page.keyboard.press('w');
  await expect(hud).toBeHidden();

  // And the choice survives a reload.
  await page.reload();
  await expect(page.locator('#hud')).toBeHidden();
  console.log('  dismiss, restore, auto-dismiss on first key, and it stays gone');
});
