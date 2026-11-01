// The crossing replay, driven by a real browser: two loops with no console
// errors, then the review shots. Run from web/ (it uses web's Playwright):
//
//   node crossing/check.mjs http://127.0.0.1:8765/ OUT_DIR [--loops-only]
//
// Prints one JSON line: {errors: [...], loops: n, shots: [...]}; exits 1 on
// any console error or page error.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const [base, out = 'crossing-shots', flag] = process.argv.slice(2);
if (flag !== '--loops-only') fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const errors = [];
const watch = (page, label) => {
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`${label}: ${m.text()}`); });
  page.on('pageerror', (e) => errors.push(`${label}: ${e.message}`));
};

// Two loops, at REPLAY_SPEED (default 16x: the same code paths as real
// time, every event passed twice; REPLAY_SPEED=1 for real time).
const ctx = await browser.newContext({ viewport: { width: 1100, height: 1000 } });
const page = await ctx.newPage();
watch(page, 'loops');
const speed = Number(process.env.REPLAY_SPEED || 16);
await page.goto(`${base}?speed=${speed}`);
await page.waitForFunction(() => window.__replay !== undefined);
const loop = await page.evaluate(() => window.__replay.loops());
let loops = 0, last = 0;
const until = Date.now() + (2 * loop / speed + 4) * 1000;
while (Date.now() < until) {
  const t = await page.evaluate(() => window.__replay.t);
  if (t < last) loops++;
  last = t;
  await page.waitForTimeout(200);
}
await ctx.close();

const shots = [];
if (flag !== '--loops-only') {
  const views = [
    ['beat1', 't=33.5&paused'],
    ['beat2', 't=50.5&paused'],
    ['card', 't=35.2&paused&card=C1'],
    ['claims-off', 't=33.5&paused&claims=off'],
  ];
  for (const scheme of ['light', 'dark']) {
    for (const [w, h, tag] of [[1100, 1000, 'desktop'], [400, 900, 'phone']]) {
      const c = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, deviceScaleFactor: 2 });
      const p = await c.newPage();
      watch(p, `${scheme}-${tag}`);
      for (const [name, q] of views) {
        if (tag === 'phone' && name !== 'beat1' && name !== 'card') continue;
        await p.goto(`${base}?${q}`);
        await p.waitForFunction(() => window.__replay !== undefined);
        await p.waitForTimeout(1200);
        const file = path.join(out, `${name}-${scheme}-${tag}.png`);
        await p.screenshot({ path: file, fullPage: true });
        shots.push(file);
      }
      await c.close();
    }
  }
}
await browser.close();
console.log(JSON.stringify({ errors, loops, shots }));
process.exit(errors.length ? 1 : 0);
