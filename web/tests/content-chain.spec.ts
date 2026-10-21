import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import {
  catalogEntryToItem,
  contentToCatalogEntry,
  framesFromContent
} from '../src/spatialdds_bridge';

/**
 * The page's half of the catalogue's 1.8 chain, pinned to the 1.7 capture.
 *
 * The catalogue moved from the demo's own CatalogQuery/CatalogResponse onto
 * a latched ContentAnnounce per item naming a content manifest. The page now
 * rebuilds each row from those two documents; this asserts it rebuilds every
 * field the old `/v1/catalog/query` returned, for every row of every seed.
 *
 * Both inputs are generated, not written here: the oracle is the frozen
 * capture of the pre-migration code (tests/fixtures/catalogue-1.7-baseline.json),
 * and the documents are what the builders produce, kept honest by the drift
 * gate in tests/test_catalogue_chain.py. The Python half of the chain is
 * asserted there against the same capture, so the two cannot disagree about
 * what a row is without one of them failing.
 */

const here = dirname(fileURLToPath(import.meta.url));
const baseline = JSON.parse(readFileSync(
  resolve(here, '../../tests/fixtures/catalogue-1.7-baseline.json'), 'utf-8')).seeds as
  Record<string, Array<Record<string, any>>>;
// What `GET /v1/frames` served before it was retired, captured live; see
// tests/test_frames_route_retired.py.
const routeFrames = JSON.parse(readFileSync(
  resolve(here, '../../tests/fixtures/frames-route-baseline.json'), 'utf-8')).route.frames as
  Record<string, Record<string, any>>;
const chain = JSON.parse(readFileSync(
  resolve(here, 'fixtures/content_chain.json'), 'utf-8')) as
  Record<string, Array<{ announce: Record<string, any>; manifest: Record<string, any> }>>;

/** Every key and value of `old` present and equal in `got`; extras allowed. */
function contained(old: unknown, got: unknown, path = ''): string[] {
  if (old !== null && typeof old === 'object' && !Array.isArray(old)) {
    if (got === null || typeof got !== 'object' || Array.isArray(got)) {
      return [`${path}: expected an object`];
    }
    return Object.entries(old as Record<string, unknown>).flatMap(([k, v]) =>
      k in (got as Record<string, unknown>)
        ? contained(v, (got as Record<string, unknown>)[k], `${path}.${k}`)
        : [`${path}.${k}: missing`]);
  }
  if (Array.isArray(old)) {
    if (!Array.isArray(got) || got.length !== old.length) {
      return [`${path}: ${JSON.stringify(old)} != ${JSON.stringify(got)}`];
    }
    return old.flatMap((v, i) => contained(v, got[i], `${path}[${i}]`));
  }
  return old === got ? [] : [`${path}: ${JSON.stringify(old)} != ${JSON.stringify(got)}`];
}

test.describe('catalogue chain, page side', () => {
  test('the fixture covers every captured row', () => {
    for (const seed of Object.keys(baseline)) {
      expect(chain[seed]?.length, seed).toBe(baseline[seed].length);
    }
    expect(Object.values(baseline).flat().length).toBe(34);
  });

  test('every field of every row is reachable', () => {
    const problems: string[] = [];
    for (const [seed, rows] of Object.entries(baseline)) {
      rows.forEach((old, i) => {
        const { announce, manifest } = chain[seed][i];
        const got = contentToCatalogEntry(announce, manifest);
        // The presence pairs: a payload behind a false flag was meaningless
        // in 1.7, and is absent rather than zero now.
        const expected = { ...old };
        if (!old.has_pose) delete expected.pose;
        if (!old.has_asset) delete expected.asset;
        problems.push(...contained(expected, got).map((p) => `${seed} ${old.content_id}${p}`));
      });
    }
    expect(problems).toEqual([]);
  });

  test('a placed row lands where the 1.7 row and /v1/frames put it', () => {
    // The old page resolved a row's pose through `/v1/frames`, which served
    // the catalogue service's announced frame transforms. The announce for a
    // placed item now carries that same anchor; placing the 1.7 row through
    // the route's captured answer and the 1.8 documents through the announce
    // alone must give the same position and orientation.
    let checked = 0;
    for (const [seed, docs] of Object.entries(chain)) {
      const frames = framesFromContent(docs.map((d) => d.announce));
      docs.forEach(({ announce, manifest }, i) => {
        const old = baseline[seed][i];
        if (!old.has_pose) return;
        expect(routeFrames[old.frame_ref.fqn],
               `the route never served ${old.frame_ref.fqn}`).toBeTruthy();
        const was = catalogEntryToItem(old, { [old.frame_ref.fqn]: routeFrames[old.frame_ref.fqn] });
        const now = catalogEntryToItem(contentToCatalogEntry(announce, manifest), frames);
        expect(now.geopose.lat_deg).toBeCloseTo(was.geopose.lat_deg, 9);
        expect(now.geopose.lon_deg).toBeCloseTo(was.geopose.lon_deg, 9);
        expect(now.geopose.alt_m).toBeCloseTo(was.geopose.alt_m, 6);
        expect(now.orientation).toEqual(was.orientation);
        expect(now.model_url).toBe(was.model_url);
        checked += 1;
      });
    }
    expect(checked, 'no placed row was exercised').toBeGreaterThan(0);
  });

  test("a row's own placement is not mistaken for a frame anchor", () => {
    for (const docs of Object.values(chain)) {
      for (const fqn of Object.keys(framesFromContent(docs.map((d) => d.announce)))) {
        expect(fqn).not.toContain('/content/');
      }
    }
  });
});
