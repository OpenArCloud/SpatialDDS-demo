// The crossing replay, driven by a real browser: two loops with no console
// errors, then the review shots. Run from web/ (it uses web's Playwright):
//
//   node crossing/check.mjs http://127.0.0.1:8765/ OUT_DIR [--loops-only]
//
// Prints one JSON line: {errors: [...], loops: n, gate: [...], shots: [...]};
// exits 1 on any console error or page error, or any gate failure.
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

// The gate's arms, read from the drawn geometry against the asset's own
// road: closed, each lies across the roadway from its pivot with the tips
// meeting over the lanes (an arm swung outward lies beyond the shoulder);
// open, each stands vertical.
const gate = [];
{
  const c = await browser.newContext({ viewport: { width: 1100, height: 1000 } });
  const p = await c.newPage();
  watch(p, 'gate');
  const at = async (t) => {
    await p.goto(`${base}?t=${t}&paused`);
    await p.waitForFunction(() => window.__replay !== undefined);
    await p.waitForTimeout(300);
    return p.evaluate(() => window.__replay.arms());
  };
  const asset = await (await fetch(new URL('crossing-replay.json', base))).json();
  const t0 = asset.meta.window.session_start;
  const ys = asset.map.lanes.flatMap((L) => [L.box[1], L.box[3]]);
  const y0 = Math.min(...ys), y1 = Math.max(...ys), slack = 0.3;
  const closed = asset.gate.find((g) => g.state === 'closed');
  const open = asset.gate.findLast((g) => g.state === 'open');
  const down = await at(t0 + closed.t + 2);
  down.forEach((a, i) => {
    if (a.y[0] < y0 - slack || a.y[1] > y1 + slack) gate.push(`closed: arm ${i} spans y ${a.y.map((v) => v.toFixed(2))}, beyond the roadway ${y0}..${y1}`);
    if (a.y[1] - a.y[0] < (y1 - y0) / 2 - 1.5) gate.push(`closed: arm ${i} reaches only ${(a.y[1] - a.y[0]).toFixed(2)} m across`);
    if (a.up[1] > 1.6) gate.push(`closed: arm ${i} top at ${a.up[1].toFixed(2)} m, not lowered`);
  });
  if (down.length === 2) {
    const [s, n] = down[0].y[0] < down[1].y[0] ? down : [down[1], down[0]];
    if (n.y[0] - s.y[1] > 1.5) gate.push(`closed: tips ${(n.y[0] - s.y[1]).toFixed(2)} m apart, not meeting over the lanes`);
  } else gate.push(`expected two arms, found ${down.length}`);
  const up = await at(t0 + open.t + 2);
  up.forEach((a, i) => {
    if (a.up[1] < 5) gate.push(`open: arm ${i} top at ${a.up[1].toFixed(2)} m, not raised`);
  });
  await c.close();
}

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
console.log(JSON.stringify({ errors, loops, gate, shots }));
process.exit(errors.length || gate.length ? 1 : 0);
