import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import {
  ClaimStore, predicateLabel, validity, type LaneEvent
} from '../src/claims';

/**
 * The claims panel's reader, held to the relationship lane as the binding
 * sends it (fixtures/relationship_events.json, written by
 * tools/make_relationship_fixture.py through the binding's own projection).
 *
 * What it pins is what the panel and the highlight show: which edges are in
 * force, that two asserters about one proposition stay two rows, the asserter
 * filter, a retirement keeping its reason, and which zone is lit.
 */

const here = dirname(fileURLToPath(import.meta.url));
const { events } = JSON.parse(readFileSync(
  resolve(here, 'fixtures/relationship_events.json'), 'utf-8')) as
  { events: LaneEvent[] };
const replay = events.slice(0, events.findIndex((e) => e.event === 'end_of_replay'));
const after = events.slice(replay.length + 1);

const ROBOT = 'ent:robot:tb3';
const FOUNTAIN = 'ent:fountain:littlefield';

function replayed(): ClaimStore {
  const store = new ClaimStore();
  for (const ev of replay) {
    store.apply(ev);
  }
  return store;
}

test('the replay puts every edge in force, two asserters as two rows', () => {
  const store = replayed();
  expect(store.rows()).toHaveLength(5);
  const within = store.rows().filter((c) =>
    c.subject_ref === ROBOT && c.object_ref === FOUNTAIN);
  expect(within.map((c) => c.asserter).sort()).toEqual(
    ['svc:claims:demo/zone-fast', 'svc:claims:demo/zone-latched']);
  expect(new Set(within.map((c) => c.id)).size).toBe(2);
});

test('the filter shows one asserter, and lists every asserter', () => {
  const store = replayed();
  expect(store.asserters()).toEqual([
    'svc:claims:demo/near', 'svc:claims:demo/zone-fast',
    'svc:claims:demo/zone-latched', 'svc:model:demo/venue',
    'svc:mover:demo/ducks']);
  const fast = store.rows('svc:claims:demo/zone-fast');
  expect(fast).toHaveLength(1);
  expect(fast[0].basis).toBe('OBSERVED');
});

test('only observed containment lights a zone', () => {
  const store = replayed();
  const lit = store.containment();
  expect([...lit.keys()]).toEqual([FOUNTAIN]);
  expect([...lit.get(FOUNTAIN)!]).toEqual([ROBOT]);
});

test('a retirement keeps its reason and valid_until, and the dispose removes it', () => {
  const store = replayed();
  for (const ev of after) {
    store.apply(ev);
  }
  expect(store.rows()).toHaveLength(4);
  const [gone] = store.retired;
  expect(gone.asserter).toBe('svc:claims:demo/zone-fast');
  expect(gone.state).toBe('RETIRED');
  expect(gone.state_reason).toMatch(/^no longer holds: /);
  expect(gone.has_valid_until).toBe(true);
  // The other asserter's claim about the same proposition still stands, so
  // the fountain stays lit: one retiring is not the proposition retiring.
  expect([...store.containment().keys()]).toEqual([FOUNTAIN]);
});

test('predicates and validity read as a person would', () => {
  const store = replayed();
  const labels = store.rows().map((c) => predicateLabel(c.predicate)).sort();
  expect(labels).toEqual(['near (15m)', 'part of', 'within', 'within', 'within']);
  const part = store.rows('svc:model:demo/venue')[0];
  expect(validity(part)).toBe('while asserted');
  const near = store.rows('svc:claims:demo/near')[0];
  expect(near.derived_from.map((r) => r.key).sort())
    .toEqual(['ent:duck:east', ROBOT]);
  expect(validity(near, 1792960000)).toBe('from 10.0 s ago, while asserted');
});
