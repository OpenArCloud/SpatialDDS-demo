import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { parseLocalizeResponse, catalogEntryToItem } from '../src/spatialdds_bridge';
import type { GeoPose } from '../src/types';

/**
 * Pins the browser-side parsers to a real bridge response.
 *
 * The fixture is captured from the demo's own VPS responder and serialised
 * through the IDL, so it is the exact JSON `/v1/localize` returns — not a
 * hand-written approximation of it. That distinction is the point: the
 * previous parser read `quality.success` / `quality.confidence` /
 * `quality.rmse_m` and `request_id`, none of which exist on
 * `spatial::argeo::VpsResponse`. Because every read had a `?? default`, the
 * UI showed "success", 0.00 confidence and 0.00 rmse for every localization,
 * including failures, and nothing failed.
 *
 * Refresh with the snippet in web/tests/fixtures/README.md.
 */

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(resolve(here, 'fixtures/localize_response.json'), 'utf-8')
);

const PRIOR: GeoPose = {
  lat_deg: 1.0,
  lon_deg: 2.0,
  alt_m: 3.0,
  q: [0, 0, 0, 1],
  stamp: { sec: 0, nanosec: 0 },
  cov: 'COV_NONE'
};

test.describe('localize response parser', () => {
  test('reads the fields VpsResponse actually has', () => {
    const parsed = parseLocalizeResponse(fixture, PRIOR);
    expect(parsed.request_id).toBe('fixture-query-0001');   // query_id
    expect(parsed.service_id).toBe('svc:vps:demo/sf-downtown');
    expect(parsed.quality.success).toBe(true);
    expect(parsed.quality.confidence).toBeCloseTo(0.87, 6);
    expect(parsed.quality.rmse_m).toBeCloseTo(0.42, 6);
  });

  test('takes the pose from node_geo, not the prior', () => {
    const parsed = parseLocalizeResponse(fixture, PRIOR);
    expect(parsed.geopose.lat_deg).not.toBe(PRIOR.lat_deg);
  });

  test('a non-success status is not reported as success', () => {
    // The old parser defaulted to `true`, so a failed localization drew a
    // confident marker on the globe.
    const failed = { ...fixture, status: 'VPS_NO_MATCH' };
    expect(parseLocalizeResponse(failed, PRIOR).quality.success).toBe(false);
  });

  test('an unknown status is not reported as success either', () => {
    const odd = { ...fixture, status: 'VPS_SOMETHING_NEW' };
    expect(parseLocalizeResponse(odd, PRIOR).quality.success).toBe(false);
  });

  test('absent rmse is null, not a perfect zero', () => {
    const noRmse = { ...fixture, has_rmse_m: false, rmse_m: 0.0 };
    expect(parseLocalizeResponse(noRmse, PRIOR).quality.rmse_m).toBeNull();
  });

  test('an unlocated response falls back to the prior pose', () => {
    const unlocated = { ...fixture, has_node_geo: false };
    expect(parseLocalizeResponse(unlocated, PRIOR).geopose).toEqual(PRIOR);
  });
});

test.describe('catalog entry parser', () => {
  test('honours has_bbox rather than the bbox key being present', () => {
    // Every CoverageElement carries a bbox member whatever the flag says, so
    // testing the array alone matched [0, 0, 0, 0] and placed the item at
    // null island instead of falling back.
    const item = catalogEntryToItem({
      content_id: 'c1',
      name: 'Unplaced',
      kind: 'model',
      coverage: [{ has_bbox: false, bbox: [0, 0, 0, 0] }]
    });
    expect(item.geopose.lat_deg).toBeCloseTo(37.7749, 4);
    expect(item.geopose.lon_deg).toBeCloseTo(-122.4194, 4);
  });

  test('uses a real bbox when the flag says so', () => {
    const item = catalogEntryToItem({
      content_id: 'c2',
      name: 'Placed',
      kind: 'model',
      coverage: [{ has_bbox: true, bbox: [-122.43, 37.77, -122.42, 37.78] }]
    });
    expect(item.geopose.lon_deg).toBeCloseTo(-122.425, 3);
    expect(item.geopose.lat_deg).toBeCloseTo(37.775, 3);
  });
});
