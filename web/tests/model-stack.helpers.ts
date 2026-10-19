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
  const origin = 'http://127.0.0.1:4173';
  try {
    const pre = await request.fetch(
      `${BINDING_URL}/${SCENE}/world/model_command/v1`,
      { method: 'OPTIONS', timeout: 4000,
        headers: { Origin: origin,
                   'Access-Control-Request-Method': 'POST',
                   'Access-Control-Request-Headers': 'authorization,content-type' } });
    if (!(pre.status() >= 200 && pre.status() < 300
          && Boolean(pre.headers()['access-control-allow-origin']))) {
      return false;
    }
    // **Half the contract is not the contract.** This checked only the
    // preflight until 2026-10-18, so it armed on binding 0.5.3 — which
    // answered the preflight and then returned a `202` with no allowed
    // origin, so the page's `fetch` rejected on a command that had in fact
    // published. The spec went from skipping to failing, and the system and
    // the client disagreed about whether the robot had been told to move.
    //
    // So probe the reply as well. A POST with no credential is the safe way:
    // it is refused, publishes nothing, and its `401` is built by the same
    // hand-written path the `202` is — so if that names the asking origin,
    // the reply a real command gets will too. Fixed in 0.5.4.
    const refused = await request.fetch(
      `${BINDING_URL}/${SCENE}/world/model_command/v1`,
      { method: 'POST', timeout: 4000, data: {},
        headers: { Origin: origin, 'Content-Type': 'application/json' } });
    return Boolean(refused.headers()['access-control-allow-origin']);
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

/**
 * How many `docker exec` calls this run had to retry, and what they were.
 *
 * Exported so a spec or a reader can see the tally rather than having to scrape
 * the log, and deliberately a running total: the point of the retry is to
 * *measure* an intermittent, not to make it disappear.
 */
export const execRetries: string[] = [];

/**
 * Run a command in the stack container. One retry, loudly.
 *
 * **The retry is an instrument, not a mask.** This used `execSync` with no
 * retry, and `restoreVenue` calls it from every `beforeEach`, so a single
 * transient `docker exec` failure aborted the whole serial file. Measured on
 * 2026-10-18: `move_duck.py --reset` failed in **two of four** full stack
 * runs, always late in the file, and exited 0 every time it was run by hand
 * immediately afterwards.
 *
 * A silent retry would hide an unexplained intermittent — the suite would go
 * green and the cause would stop being visible. So every absorbed failure
 * prints the command, the exit code and the stderr, and appends to
 * `execRetries`, which means each firing adds evidence instead of vanishing.
 * If the tally is routinely non-empty that is a finding with data attached,
 * and the next step is the cause rather than a second retry.
 *
 * One retry, not three. Two attempts distinguish "transient" from "broken";
 * more would start absorbing the second class as well.
 */
export function inContainer(name: string, command: string): string {
  const line = `docker exec -w /app ${name} ${command}`;
  try {
    return execSync(line).toString().trim();
  } catch (first: any) {
    const code = first?.status ?? '?';
    const err = String(first?.stderr || first?.message || '').trim().slice(0, 400);
    const note = `${command} — exit ${code}`;
    execRetries.push(note);
    console.log(`  exec:retry ${note}\n`
              + `    cmd: ${line}\n`
              + `    stderr: ${err || '(none captured)'}\n`
              + `    retries so far this run: ${execRetries.length}`);
    return execSync(line).toString().trim();
  }
}

/**
 * Put the venue back the way it was seeded — including its dynamics — and wait
 * until it is.
 *
 * Every stack-driving test starts here rather than assuming what the last one
 * left behind. The wait is the important half: `--restore` returns as soon as
 * the request is sent, three hops before the bridge's cache agrees.
 *
 * **Quiesce, reset, assert, then restore the motion.** This asserted every
 * duck within 1 cm of its seed pose while the stack's own duck mover was
 * still running — `SPATIALDDS_DUCK_MOVER` defaults to 1 in
 * run_bridge_server_docker.sh — so it could only succeed by sampling between
 * two of the mover's steps. Measured mid-failure on 2026-10-18: a mover alive
 * and the ducks 0.58, 2.10 and 4.12 m off seed. **Earlier green runs were won
 * races**, usually won because some preceding spec had left the mover stopped.
 * That is the honest epitaph for every pass this function used to report.
 *
 * A fixture that asserts positions while a writer it knows about keeps
 * writing is not testing state, it is racing. So the mover is stopped first
 * and started again afterwards **only if it was running when we arrived** —
 * which keeps the mover's meaning intact for both spec families: the ones
 * that assert where something *is* get stillness, and the ones that assert
 * that something *moves* get their motion back. Restarting unconditionally
 * would hand a mover to a stack launched without one.
 *
 * The alternative — the launcher not starting a mover for test runs — was
 * considered and rejected: it changes what the stack *is*, and the motion
 * assertions the retirement batch added exist precisely because the shipped
 * stack moves.
 *
 * `startMover`'s default bounds and `duck_mover`'s own default are both
 * `shallows`, so the restart reproduces what the launcher started rather than
 * quietly substituting a different box.
 */
export async function restoreVenue(request: any, name: string): Promise<void> {
  const moverWasRunning = moverRunning(name);
  if (moverWasRunning) {
    stopMover(name);
  }
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

  // The venue is quiet and correct; give it its motion back if it had any.
  if (moverWasRunning) {
    startMover(name);
  }
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
  stopMoverNow(name);
  // **Wait for it to actually be gone.** `pkill` returns as soon as the
  // signal is sent, so `moverRunning` a few milliseconds later can still see
  // the dying process — and a caller that reads "running" there draws the
  // opposite conclusion from the truth. That bit `restoreVenue`: called from
  // a `beforeEach` just after a `beforeAll` had stopped the mover, it saw one
  // alive, recorded "was running", and **restarted a mover the suite had
  // deliberately stopped**, defeating the file-level quiesce and failing a
  // distance assertion two tests later.
  //
  // Bounded rather than unbounded: if the thing will not die in two seconds
  // that is a finding, and the caller should see the state rather than hang.
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline && moverRunning(name)) {
    execSync('sleep 0.1');
  }
}

function stopMoverNow(name: string): void {
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
