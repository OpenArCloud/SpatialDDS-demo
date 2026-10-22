import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

/**
 * The gateway's fusion dashboard draws the conflicts the fusion service sends.
 *
 * Measured on 2026-10-21 against the live local stack: the service published
 * PREDICTED_CONFLICT, the dashboard received it (3 frames in 40 s), and kept
 * none of it -- its handler accepted only PROXIMITY_ALERT, on a comment
 * claiming 1.7 had no predicted-conflict type. 1.7 had one (value 13; see
 * tests/cross_era/). Nothing covered the page, so the drop went unseen.
 *
 * The page is served as-is from bridges/web_bridge/static under a stub
 * origin, with no gateway behind it; each frame is the captured wire frame
 * (fixtures/conflict_frame.json) handed to the page's own handler.
 */
const here = dirname(fileURLToPath(import.meta.url));
const STATIC = join(here, '..', '..', 'bridges', 'web_bridge', 'static');
const frame = JSON.parse(readFileSync(join(here, 'fixtures', 'conflict_frame.json'), 'utf8'));

test.beforeEach(async ({ page }) => {
  await page.route('http://dashboard.test/**', (route) => {
    // The gateway's own mapping (server.py): `/` is index.html and the
    // directory is mounted at /static. Anything else is not served.
    const path = new URL(route.request().url()).pathname;
    const file = path === '/' ? 'index.html'
      : path.startsWith('/static/') ? path.slice('/static/'.length) : null;
    if (!file) return route.fulfill({ status: 404, body: '' });
    const type = file.endsWith('.js') ? 'text/javascript' : 'text/html';
    route.fulfill({ status: 200, contentType: type, body: readFileSync(join(STATIC, file), 'utf8') });
  });
  await page.goto('http://dashboard.test/');
});

// `state` is a top-level const in a classic script: in the global scope by
// name, but not a property of window, so it is read by name.
const held = (page: Page): Promise<{ kind: string; agents: string[] }[]> =>
  page.evaluate('state.conflicts.map((c) => ({ kind: c.kind, agents: c.agents }))');

const deliver = (page: Page, f: unknown) =>
  page.evaluate((m: unknown) => (window as unknown as { onConflict: (m: unknown) => void }).onConflict(m), f);

test('a predicted conflict from the fusion service is drawn', async ({ page }) => {
  expect(frame.payload.type).toBe('PREDICTED_CONFLICT');
  await deliver(page, frame);
  expect(await held(page)).toEqual([
    { kind: 'predicted', agents: ['operator_a_ego', 'operator_b_ego'] },
  ]);
});

test('a proximity alert is drawn, and kept apart from a predicted one', async ({ page }) => {
  await deliver(page, frame);
  await deliver(page, { ...frame, payload: { ...frame.payload, type: 'PROXIMITY_ALERT' } });
  expect((await held(page)).map((c) => c.kind)).toEqual(['predicted', 'proximity']);
});

test('other event types are not drawn as conflicts', async ({ page }) => {
  await deliver(page, { ...frame, payload: { ...frame.payload, type: 'ANOMALY' } });
  expect(await held(page)).toEqual([]);
});

test('the page names no era it is not', async ({ page }) => {
  await expect(page).toHaveTitle('SpatialDDS — Multi-Operator Intersection');
});
