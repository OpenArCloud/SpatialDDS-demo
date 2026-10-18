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

/**
 * The binding, which is where the model now is.
 *
 * `server.py`'s `GET /v1/model` retired on 2026-10-18 with the pre-1.8
 * readers behind it. These specs read the venue over Appendix N.3 instead,
 * from `bridges/web_bridge/binding.py` on its own port --- two processes, so
 * two base URLs, the same split `web/src/spatialdds_binding.ts` makes for
 * the browser.
 */
export const BINDING_URL =
  process.env.VITE_SPATIALDDS_BINDING_URL || 'http://localhost:8089';
export const SCENE = 'ut-austin-littlefield';
/** Needed only to command. Reading these lanes needs no credential. */
export const BINDING_TOKEN =
  process.env.VITE_SPATIALDDS_BINDING_TOKEN || 'bridge-session';
export const SEEDED_ENTITIES = 7;      // + the observed pond

/**
 * The venue's entities, as N.3 serves them.
 *
 * **A collection, not a snapshot document.** The old `/v1/model` answered
 * `{entities, relationships}`; `GET /{scene}/world/entity/v1` answers an
 * array. The `relationships` half has no 1.8 counterpart at all --- the
 * provisional module carries no relationship type, so the four the venue used
 * to seed are gone rather than moved, and every assertion that counted them
 * went with them. `docs/relationship-candidate-1.9.md` is where that loss is
 * argued; this helper is not the place to paper over it.
 */
export async function venueEntities(request: any, timeout = 4000): Promise<any[]> {
  const r = await request.get(
    `${BINDING_URL}/${SCENE}/world/entity/v1`, { timeout });
  const doc = await r.json();
  return Array.isArray(doc) ? doc : [];
}

/**
 * Can a *browser* send an N.6 command to this binding at all?
 *
 * **Probed rather than assumed, so this unblocks itself.** A command needs a
 * credential, `Authorization` is not CORS-safelisted, so a cross-origin POST
 * carrying one preflights --- and the gateway routes no `OPTIONS`, so the
 * preflight 405s and the POST is never attempted. Nothing on the page can
 * work around it: it is the browser enforcing CORS correctly against a
 * gateway that does not answer.
 *
 * Measured 2026-10-18 against binding 0.5.2:
 *
 * ```
 * OPTIONS /{scene}/world/model_command/v1
 *   Access-Control-Request-Method: POST
 *   Access-Control-Request-Headers: authorization,content-type
 * -> 405 Allow: GET,HEAD,POST
 * ```
 *
 * This is blocker B in `docs/browser-credential-finding.md`, and it is the
 * `spatialdds-web` gateway's to fix (answer the preflight, allow the header,
 * echo the Origin) --- not a specification question and not something to
 * paper over here. So the tap-to-send spec asks this first and skips with the
 * reason when the answer is no. When the gateway starts answering `OPTIONS`,
 * this returns true and the spec runs again with no edit.
 */
export async function browserCanCommand(request: any): Promise<boolean> {
  try {
    const r = await request.fetch(
      `${BINDING_URL}/${SCENE}/world/model_command/v1`,
      { method: 'OPTIONS', timeout: 4000,
        headers: { Origin: 'http://127.0.0.1:4173',
                   'Access-Control-Request-Method': 'POST',
                   'Access-Control-Request-Headers': 'authorization,content-type' } });
    return r.status() >= 200 && r.status() < 300
      && Boolean(r.headers()['access-control-allow-origin']);
  } catch {
    return false;
  }
}

/**
 * Send one command over N.6, the way the page does.
 *
 * A POST to the resource, with a complete `spatial.owm.ModelCommand` --- the
 * binding validates against the published schema before any backend sees it,
 * so the guarded members have to be present and `stamp.sec` has to be a
 * decimal *string* per N.1. The old bridge filled a partial command in
 * server-side. 202 is accepted, 409 is a decline *with a reason*, and both
 * are answers.
 */
export async function sendCommand(
    request: any, verb: string, subjectId: string,
    pose?: { t: number[]; q: number[] }, reason = ''): Promise<any> {
  const now = Date.now() / 1000;
  return request.post(`${BINDING_URL}/${SCENE}/world/model_command/v1`, {
    headers: { Authorization: `Bearer ${BINDING_TOKEN}` },
    data: {
      command_id: `cmd:spec:${verb}-${Math.floor(now * 1000)}`,
      verb,
      subject_id: subjectId,
      reason,
      requester_id: 'playwright',
      has_pose: Boolean(pose),
      pose: pose || { t: [0, 0, 0], q: [0, 0, 0, 1] },
      has_extent: false,
      extent: { min_xyz: [0, 0, 0], max_xyz: [0, 0, 0] },
      stamp: { sec: String(Math.floor(now)), nanosec: 0 }
    }
  });
}

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
        const entities = await venueEntities(request);
        const ducks = entities
          .filter((e: any) => e.entity_id.startsWith('ent:duck:'));
        const placed = ducks.every((d: any) =>
          d.state === 'ACTIVE'
          && Math.abs(d.pose.t[0] - SEED_POSES[d.entity_id][0]) < 0.01
          && Math.abs(d.pose.t[1] - SEED_POSES[d.entity_id][1]) < 0.01);
        // What the venue's own publisher seeds must all be present and back
        // where it put them. Deliberately not an exact total: optional
        // services add entities (pondwatch a pond, the robot bridge a robot),
        // and asserting a count made the suite depend on which flags the
        // stack happened to be launched with. An eighth entity is not a
        // reason for a duck test to fail.
        const ids = new Set(entities.map((e: any) => e.entity_id));
        const seeded = ['ent:fountain:littlefield', 'ent:pond:littlefield',
                        'ent:duck:catalog-pose', 'ent:duck:west', 'ent:duck:east'];
        // No relationship clause any more: 1.8 has no relationship type, so
        // the four this used to wait for cannot come back and waiting on
        // them would never return.
        return seeded.every((id) => ids.has(id)) && placed;
      } catch {
        return false;
      }
    }, { timeout: 30_000 })
    .toBe(true);
}

// As seeded in spatialdds_demo/model_service.py.
export const SEED_POSES: Record<string, [number, number]> = {
  'ent:duck:catalog-pose': [11.708, -14.273],
  'ent:duck:west': [10.5, -13.0],
  'ent:duck:east': [16.5, -10.5]
};

/** Bring the page to a localized, discovered state. */
export async function readyPage(page: any, url = '/?debug=1'): Promise<void> {
  await page.goto(url);
  await page.waitForFunction(() => (window as any).__viewer, null, { timeout: 60_000 });
  const clear = async () => page.evaluate(() =>
    document.querySelectorAll('.cesium-widget-errorPanel').forEach((e: any) => e.remove()));
  await clear();
  // Since P3.0 the page localizes itself against the demo's own VPS, so
  // clicking Localize is what a person would do only if it had not already
  // happened. Clicking anyway started a second localization on top of the
  // first, which is how this harness found the missing in-flight guard.
  const localized = async () => Number(/alt=([\d.]+)m/.exec(
    (await page.locator('#readout').getAttribute('data-geopose')) || '')?.[1] ?? NaN);
  if (!((await localized()) < 1000)) {
    await page.evaluate(() => document.getElementById('btnLocalize')?.click());
  }
  await expect.poll(localized, { timeout: 40_000 }).toBeLessThan(1000);
  await clear();
  await page.evaluate(() => document.getElementById('btnDiscover')?.click());
  await page.waitForFunction(
    () => (window as any).__appLogs?.some((l: string) => l.startsWith('discover:items')),
    null, { timeout: 40_000 });
  await clear();
}

/**
 * The mover, started and stopped inside the test that wants it.
 *
 * It is off in the stack by default and must stay that way: every other test
 * here asserts where a duck *is*, and a service quietly moving them would
 * turn the whole file into a coin toss. Anything that needs motion starts it
 * and is responsible for stopping it, including when it fails.
 */
export function startMover(
    name: string,
    bounds: 'shallows' | 'declared' | 'derived' = 'shallows'): void {
  execSync(`docker exec -d -w /app ${name} python3 -m spatialdds_demo.duck_mover `
           + `--bounds ${bounds}`, { stdio: 'ignore' });
}

/** Whether a mover is running right now. */
export function moverRunning(name: string): boolean {
  try {
    const out = execSync(
      `docker exec ${name} pgrep -c -f '^python3 -m spatialdds_demo.duck_mover'`,
      { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim();
    return Number(out) > 0;
  } catch {
    return false;                 // pgrep exits non-zero when nothing matches
  }
}

export function stopMover(name: string): void {
  try {
    // Anchored, and this matters more than it looks. The container's PID 1 is
    // the startup shell, whose command line *contains* the string
    // `spatialdds_demo.duck_mover` because it is the script that conditionally
    // launches it. An unanchored `pkill -f` therefore sends SIGTERM to init on
    // every cleanup: it has not killed the stack yet, which is luck rather
    // than design. `^python3 -m ...` matches the mover and nothing else.
    execSync(`docker exec ${name} pkill -f '^python3 -m spatialdds_demo.duck_mover'`,
             { stdio: 'ignore' });
  } catch {
    // pkill exits non-zero when there was nothing to kill, which is fine:
    // this runs in cleanup paths where the mover may already be gone.
  }
}

/** The capture viewpoint, shared so screenshots across tests are comparable. */
export const VIEW = { lon: -97.73920, lat: 30.28345, height: 205,
                      heading: 327, pitch: -36 };

export async function look(page: any, view = VIEW): Promise<void> {
  await page.evaluate((v: any) => {
    const viewer = (window as any).__viewer;
    const Cartesian3: any = viewer.camera.position.constructor;
    const rad = (deg: number) => deg * Math.PI / 180;
    viewer.camera.setView({
      destination: Cartesian3.fromDegrees(v.lon, v.lat, v.height),
      orientation: { heading: rad(v.heading), pitch: rad(v.pitch), roll: 0 }
    });
    if (viewer.navigationHelpButton) {
      viewer.navigationHelpButton.viewModel.showInstructions = false;
    }
    // The keyboard card is furniture in a capture: it sat over the venue in
    // every acceptance screenshot for three parts because nothing could put
    // it away. A capture never presses a key, so it has to be told.
    document.getElementById('hud')?.classList.add('hidden');
  }, view);
  await page.waitForFunction(() => {
    const v = (window as any).__viewer;
    const prims = v.scene.primitives;
    for (let i = 0; i < prims.length; i += 1) {
      const p: any = prims.get(i);
      if (p && 'tilesLoaded' in p && !p.tilesLoaded) {
        return false;
      }
    }
    return true;
  }, null, { timeout: 90_000 });
  await page.waitForTimeout(2000);
}
