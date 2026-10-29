import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { type Claim, type LaneEvent } from '../src/claims';
import {
  ClaimTimeline, basisCss, cardRows, citedRecords, endKind, groupBars, slots,
  treatment
} from '../src/claims_view';

/**
 * What the claims overlay draws, held to the relationship lane as the
 * binding sends it (fixtures/relationship_events.json, written by
 * tools/make_relationship_fixture.py through the binding's own projection).
 *
 * The scene, the strip and the card all read these, so this is where the
 * overlay's decisions are pinned: how a bar ended, which edges are one
 * proposition, what a card says and which records it lights. The rendered
 * result is claims-overlay-stack.spec.ts, against the running venue.
 */

const here = dirname(fileURLToPath(import.meta.url));
const { events } = JSON.parse(readFileSync(
  resolve(here, 'fixtures/relationship_events.json'), 'utf-8')) as
  { events: LaneEvent[] };
const cut = events.findIndex((e) => e.event === 'end_of_replay');
const replay = events.slice(0, cut);
const after = events.slice(cut + 1);

const ROBOT = 'ent:robot:tb3';
const FOUNTAIN = 'ent:fountain:littlefield';
const NOW = 1792960100;

const claimOf = (pred: (c: Claim) => boolean): Claim =>
  replay.map((e) => e.data as unknown as Claim).find(pred)!;
const observed = claimOf((c) => c.basis === 'OBSERVED');
const declared = claimOf((c) => c.basis === 'DECLARED' && c.predicate.endsWith('P361'));
const derived = claimOf((c) => c.basis === 'DERIVED');

function fed(upto: LaneEvent[]): ClaimTimeline {
  const t = new ClaimTimeline(180);
  for (const ev of upto) {
    t.apply(ev, NOW);
  }
  return t;
}

test('the two ways an edge ends are told apart, from the reason alone', () => {
  expect(endKind('no longer holds: ent:robot:tb3 left the extent')).toBe('relationship');
  expect(endKind('asserter stopped: the participant stopped')).toBe('assertion');
  expect(endKind('subject unobserved: ent:robot:tb3 went UNOBSERVED')).toBe('assertion');
  expect(endKind('')).toBe('assertion');
});

test('the replay opens a bar per edge, from its valid_from', () => {
  const t = fed(replay);
  const bars = t.visible(NOW);
  expect(bars).toHaveLength(5);
  expect(bars.every((b) => b.end === null)).toBe(true);
  const bar = t.bars.get(observed.id)!;
  expect(bar.startStated).toBe(true);
  expect(bar.start).toBeCloseTo(Number(observed.valid_from.sec)
    + observed.valid_from.nanosec / 1e9, 6);
});

test('a retirement ends its bar as the relationship ending, with the reason', () => {
  const t = fed([...replay, ...after]);
  const retired = after.find((e) => e.event === 'sample')!.data as unknown as Claim;
  const bar = t.bars.get(retired.id)!;
  expect(bar.end).not.toBeNull();
  expect(bar.endKind).toBe('relationship');
  expect(bar.reason).toBe(retired.state_reason);
  // The dispose after the terminal sample does not end it a second time.
  expect(bar.end).toBe(retired.has_valid_until
    ? Number(retired.valid_until.sec) + retired.valid_until.nanosec / 1e9
    : Number(retired.stamp.sec) + retired.stamp.nanosec / 1e9);
});

test('a killed asserter: its bar stays open and is marked, nothing ends', () => {
  const t = fed([...replay, ...after]);
  const killed = after.find((e) => e.event === 'liveliness')!;
  const bar = t.bars.get(killed.key!)!;
  expect(bar.end).toBeNull();
  expect(bar.writerGone).toBe(true);
});

test('a dispose with no terminal sample is an assertion ending', () => {
  const t = fed(replay);
  t.apply({ event: 'dispose', key: declared.id, reason: 'asserter stopped: shut down' }, NOW);
  expect(t.bars.get(declared.id)!.endKind).toBe('assertion');
  expect(t.bars.get(declared.id)!.end).toBe(NOW);
});

test('two asserters about one proposition are one group, neither first by rank', () => {
  const groups = groupBars(fed(replay).visible(NOW));
  const within = groups.find((g) => g.subject === ROBOT && g.object === FOUNTAIN)!;
  expect(within.asserters).toEqual([
    'svc:claims:demo/zone-fast', 'svc:claims:demo/zone-latched']);
  expect(within.bars).toHaveLength(2);
  // And in the scene, side by side: two slots, each knowing the other.
  const place = slots(replay.map((e) => e.data as unknown as Claim));
  const theirs = within.bars.map((b) => place.get(b.id)!);
  expect(theirs.map((s) => s.index).sort()).toEqual([0, 1]);
  expect(theirs.every((s) => s.of === 2)).toBe(true);
});

test('a group stays in force while any asserter holds it; newest activity on top', () => {
  const t = fed([...replay, ...after]);
  // One zone watch retired its edge and the other still holds it: one
  // proposition, still in force, with one bar ended and one open.
  const groups = groupBars(t.visible(NOW));
  const within = groups.find((g) => g.subject === ROBOT && g.object === FOUNTAIN)!;
  expect(within.open).toBe(true);
  expect(within.bars.map((b) => b.end === null).sort()).toEqual([false, true]);
  // The retirement is the newest thing that happened, so its group leads.
  expect(groups[0]).toBe(within);
  // And a later ending leads in its turn, open or not.
  t.apply({ event: 'dispose', key: declared.id, reason: 'asserter stopped' }, NOW + 5);
  const after2 = groupBars(t.visible(NOW + 5));
  expect(after2[0].bars[0].id).toBe(declared.id);
  expect(after2[0].open).toBe(false);
  const latest = after2.map((g) => g.latest);
  expect(latest).toEqual([...latest].sort((x, y) => y - x));
});

test('each predicate has its treatment', () => {
  expect(treatment(observed.predicate)).toBe('containment');
  expect(treatment(declared.predicate)).toBe('part-of');
  expect(treatment(derived.predicate)).toBe('line');
});

test('the provenance card: a declared, an observed and a derived edge', () => {
  const row = (c: Claim, label: string) =>
    cardRows(c, { now: NOW }).find((r) => r.label === label)?.value;
  for (const c of [declared, observed, derived]) {
    expect(row(c, 'subject')).toBe(c.subject_ref);
    expect(row(c, 'predicate URI')).toBe(c.predicate);
    expect(row(c, 'object')).toBe(c.object_ref);
    expect(row(c, 'asserter')).toBe(c.asserter);
    expect(row(c, 'basis')).toBe(c.basis);
    expect(row(c, 'stamp')).toMatch(/Z \(\d+\.\d s ago\)$/);
  }
  expect(row(declared, 'predicate')).toBe('part of');
  expect(row(observed, 'predicate')).toBe('within');
  expect(row(observed, 'valid until')).toBe('while asserted');
  expect(row(derived, 'predicate')).toBe('near (15m)');
  for (const c of [declared, observed, derived]) {
    expect(row(c, 'confidence')).toBe(c.has_confidence ? c.confidence.toFixed(2) : undefined);
  }
  expect(row(derived, 'derived from')).toContain(ROBOT);
  expect(row(declared, 'derived from')).toBeUndefined();
  expect(row(observed, 'asserter')).toBe(observed.asserter);
  expect(cardRows(observed, { writerGone: true }).find((r) => r.label === 'asserter')!
    .value).toContain('asserter not alive');
});

test('a derived card cites the records it read, as entities on screen', () => {
  const cited = citedRecords(derived);
  expect(cited.entities.sort()).toEqual(
    [...new Set(derived.derived_from.map((r) => r.key))].sort());
  expect(cited.entities).toContain(ROBOT);
  expect(cited.claims).toEqual([]);
  expect(citedRecords(declared)).toEqual({ entities: [], claims: [] });
});

test('one palette for edges and extents, by basis', () => {
  expect(basisCss('OBSERVED')).toBe('rgba(95, 196, 205, 0.9)');
  expect(basisCss('DECLARED')).toBe('rgba(240, 196, 78, 0.9)');
  expect(basisCss('DERIVED')).toBe('rgba(197, 128, 240, 0.9)');
  expect(basisCss(undefined)).toBe(basisCss('AUTHORED'));
});
