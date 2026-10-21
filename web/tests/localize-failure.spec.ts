import { expect, test } from '@playwright/test';

/**
 * A failed localization must not read as a successful one.
 *
 * `parseLocalizeResponse` decides this correctly and carries it in
 * `quality.success`; `localizeWith` used to ignore the field, log
 * `localize:success` unconditionally and fly the camera to
 * `response.geopose` -- which, on a failure, is the *prior* the parser
 * substitutes on purpose. So a `VPS_FAILED` presented as a confident fix at
 * the position we had just sent in, with the camera snapped to the prior's
 * own orientation. Against the real deployed service this fired on every
 * press of "Localize (DDS)", because a Cesium render cannot match a map built
 * from photographs.
 *
 * The parser already had a test. Its consumer did not, and that is exactly
 * where the defect lived -- so this drives the page and asserts on what the
 * page says and does, not on what the parser returns.
 *
 * Hermetic by interception: no bridge, no bus, no VPS.
 */
const BRIDGE = 'http://localhost:8088';

const HEALTH = { status: 'ok', dds_domain: 0 };

/** A refusal: no node_geo at all, which is how the service says "no fix". */
const FAILED = {
  query_id: 'q:spec:failed',
  service_id: 'svc:vps:spec/none',
  status: 'VPS_FAILED',
  has_node_geo: false,
  // Deliberately not 0.0. An unserved stub answers `{}`, which parses to
  // confidence 0.0 -- so a 0.0 here would let this spec pass on a response it
  // never actually sent. The logged value is checked below.
  confidence: 0.37,
  has_rmse_m: false
};

/** A fix, somewhere unmistakably not the prior. */
const FIX_LAT = 30.2845;
const FIX_LON = -97.7402;
const SUCCESS = {
  query_id: 'q:spec:ok',
  service_id: 'svc:vps:spec/yes',
  status: 'VPS_SUCCESS',
  has_node_geo: true,
  node_geo: {
    has_geopose: true,
    geopose: {
      lat_deg: FIX_LAT, lon_deg: FIX_LON, alt_m: 150.0,
      q: [0, 0, 0, 1], stamp: { sec: 0, nanosec: 0 },
      cov: { discriminator: 'COV_NONE', none: 0 }
    }
  },
  confidence: 0.91,
  has_rmse_m: true,
  rmse_m: 0.08
};

async function pageOnAStubbedBridge(page: any, localizeBody: unknown) {
  // **Order matters and is not the obvious one.** Playwright matches handlers
  // in reverse registration order, so the catch-all has to be registered
  // *first* or it shadows the specific routes below it. Registered last, it
  // served `{}` to the localize call -- which parses as "no status, so not a
  // success" and made the failure case pass without ever sending its own
  // fixture, while the success case failed for the same reason.
  await page.route(`${BRIDGE}/**`, (route: any) => {
    const url = route.request().url();
    if (url.includes('/search')) return route.fulfill({ json: { results: [] } });
    return route.fulfill({ json: {} });
  });
  await page.route(`${BRIDGE}/health`, (route: any) =>
    route.fulfill({ json: HEALTH }));
  await page.route(`${BRIDGE}/v1/localize`, (route: any) =>
    route.fulfill({ json: localizeBody }));

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/?debug=1');
  await page.waitForFunction(() => (window as any).__viewer, null,
                             { timeout: 90_000 });
  await page.waitForFunction(
    () => (window as any).__appLogs?.some((l: string) =>
      l.startsWith('bridge:online') || l.startsWith('bridge:offline')),
    null, { timeout: 90_000 });
  const badge = await page.evaluate(
    () => (document.querySelector('#modeBadge')?.textContent || '').trim());
  expect(badge, 'the stub must put the page on the bridge path').toContain('DDS');
}

/**
 * The button that actually reaches `/v1/localize`.
 *
 * The plain Localize button is an explicit mock now and never calls the
 * bridge, so a spec about how a bridge *response* is handled has to press the
 * image one. Asserted rather than assumed: if the bundle were missing this
 * would be disabled and the spec would be testing nothing.
 */
async function pressRealLocalize(page: any) {
  const disabled = await page.evaluate(
    () => (document.getElementById('btnLocalizeImage') as any)?.disabled);
  expect(disabled, 'the query-frame bundle must be installed for this spec to '
                 + 'reach the bridge at all').toBe(false);
  await page.click('#btnLocalizeImage');
}

async function cameraState(page: any) {
  return page.evaluate(() => {
    const v = (window as any).__viewer;
    const c = v.camera.positionCartographic;
    return {
      lat: (c.latitude * 180) / Math.PI,
      lon: (c.longitude * 180) / Math.PI,
      heading: (v.camera.heading * 180) / Math.PI
    };
  });
}

test('a VPS_FAILED is reported as a failure and does not move the camera',
     async ({ page }) => {
  test.setTimeout(180_000);
  await pageOnAStubbedBridge(page, FAILED);

  const beforeCamera = await cameraState(page);
  const before: number = await page.evaluate(
    () => ((window as any).__appLogs || []).length);

  await pressRealLocalize(page);
  await page.waitForFunction(
    (n) => ((window as any).__appLogs || []).slice(n).some(
      (l: string) => l.startsWith('localize:')),
    before, { timeout: 60_000 });
  // Long enough that a flyTo of duration 1.0 would have landed.
  await page.waitForTimeout(4000);

  const fresh: string[] = await page.evaluate(
    (n) => ((window as any).__appLogs || []).slice(n), before);
  const success = fresh.filter((l) => l.startsWith('localize:success'));
  const failed = fresh.filter((l) => l.startsWith('localize:failed'));

  expect(success, `a VPS_FAILED must not log a success: ${fresh.join(' | ')}`)
    .toEqual([]);
  expect(failed.length, `the failure must be stated: ${fresh.join(' | ')}`)
    .toBeGreaterThan(0);
  // Proof the stub under test is the one that answered, not a stand-in.
  expect(failed[0], 'the reported confidence must be the fixture\'s, which is '
                  + 'how this spec knows it tested its own response')
    .toContain('0.37');
  console.log(`  ${failed[0]}`);

  // The visible half of the defect: the camera snapping to an orientation no
  // localizer returned.
  const afterCamera = await cameraState(page);
  const turned = Math.abs(((afterCamera.heading - beforeCamera.heading + 540)
                           % 360) - 180);
  console.log(`  heading ${beforeCamera.heading.toFixed(1)} -> `
            + `${afterCamera.heading.toFixed(1)} deg (moved ${turned.toFixed(1)})`);
  expect(turned,
         'a failed localization must not reorient the camera')
    .toBeLessThan(1.0);
});

test('a VPS_SUCCESS still localizes and still moves the camera',
     async ({ page }) => {
  test.setTimeout(180_000);
  await pageOnAStubbedBridge(page, SUCCESS);

  const before: number = await page.evaluate(
    () => ((window as any).__appLogs || []).length);
  await pressRealLocalize(page);
  await page.waitForFunction(
    (n) => ((window as any).__appLogs || []).slice(n).some(
      (l: string) => l.startsWith('localize:')),
    before, { timeout: 60_000 });
  await page.waitForTimeout(4000);

  const fresh: string[] = await page.evaluate(
    (n) => ((window as any).__appLogs || []).slice(n), before);
  const success = fresh.find((l) => l.startsWith('localize:success')) || '';
  expect(success, `the fix must be reported: ${fresh.join(' | ')}`)
    .toContain('localize:success');
  console.log(`  ${success}`);

  // And the camera is really there, so the guard above cannot pass by
  // disabling the flight altogether.
  const after = await cameraState(page);
  const metres = Math.hypot((after.lat - FIX_LAT) * 111320,
                            (after.lon - FIX_LON) * 111320
                            * Math.cos((FIX_LAT * Math.PI) / 180));
  console.log(`  camera landed ${metres.toFixed(1)} m from the reported fix`);
  expect(metres, 'a successful localization must still fly the camera to it')
    .toBeLessThan(50);
});
