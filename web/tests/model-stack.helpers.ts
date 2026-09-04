import { execSync } from 'node:child_process';
import { expect } from '@playwright/test';

/**
 * Shared ground for the specs that drive the running stack.
 *
 * There is one venue, one bus and one bridge, and these specs move ducks
 * around and retire them. Playwright runs files in parallel by default, so
 * as separate files they raced: one spec retired the duck another was about
 * to move, and a third asserted three ducks while a fourth was one short.
 * They live in a single serial file for that reason -- the isolation has to
 * come from the harness, because the world underneath cannot provide it.
 */

export const BRIDGE_URL =
  process.env.VITE_SPATIALDDS_BRIDGE_URL || 'http://localhost:8088';
export const SEEDED_ENTITIES = 5;      // fountain, three ducks, the gnome
export const SEEDED_RELATIONSHIPS = 3;

export function container(): string | null {
  try {
    const name = execSync("docker ps --format '{{.Names}}' | grep dds_bridge | head -1",
                          { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim();
    return name || null;
  } catch {
    return null;
  }
}

export function inContainer(name: string, command: string): string {
  return execSync(`docker exec -w /app ${name} ${command}`).toString().trim();
}

/**
 * Put the venue back the way it was seeded, and wait until it is.
 *
 * Every stack-driving test starts here rather than assuming what the last one
 * left behind. The wait is the important half: `--restore` returns as soon as
 * the request is sent, three hops before the bridge's cache agrees.
 */
export async function restoreVenue(request: any, name: string): Promise<void> {
  inContainer(name, 'python3 scripts/retire_entity.py --restore');
  inContainer(name, 'python3 scripts/move_duck.py --reset');
  await expect
    .poll(async () => {
      try {
        const model = await (await request.get(`${BRIDGE_URL}/v1/model`,
                                               { timeout: 4000 })).json();
        const ducks = (model.entities || [])
          .filter((e: any) => e.entity_id.startsWith('ent:duck:'));
        const placed = ducks.every((d: any) =>
          d.state === 'ACTIVE'
          && Math.abs(d.pose.t[0] - SEED_POSES[d.entity_id][0]) < 0.01
          && Math.abs(d.pose.t[1] - SEED_POSES[d.entity_id][1]) < 0.01);
        return (model.entities || []).length === SEEDED_ENTITIES
          && (model.relationships || []).length === SEEDED_RELATIONSHIPS
          && placed;
      } catch {
        return false;
      }
    }, { timeout: 30_000 })
    .toBe(true);
}

// As seeded in spatialdds_demo/model_service.py.
export const SEED_POSES: Record<string, [number, number]> = {
  'ent:duck:catalog-pose': [11.708, -14.273],
  'ent:duck:west': [6.5, -8.0],
  'ent:duck:east': [16.5, -10.5]
};

/** Bring the page to a localized, discovered state. */
export async function readyPage(page: any, url = '/?debug=1'): Promise<void> {
  await page.goto(url);
  await page.waitForFunction(() => (window as any).__viewer, null, { timeout: 60_000 });
  const clear = async () => page.evaluate(() =>
    document.querySelectorAll('.cesium-widget-errorPanel').forEach((e: any) => e.remove()));
  await clear();
  await page.evaluate(() => document.getElementById('btnLocalize')?.click());
  await expect
    .poll(async () => Number(/alt=([\d.]+)m/.exec(
      (await page.locator('#readout').getAttribute('data-geopose')) || '')?.[1] ?? NaN),
    { timeout: 40_000 })
    .toBeLessThan(1000);
  await clear();
  await page.evaluate(() => document.getElementById('btnDiscover')?.click());
  await page.waitForFunction(
    () => (window as any).__appLogs?.some((l: string) => l.startsWith('discover:items')),
    null, { timeout: 40_000 });
  await clear();
}
