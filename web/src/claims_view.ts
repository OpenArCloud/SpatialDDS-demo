/**
 * What the claims overlay draws, as pure functions over the lane's events.
 *
 * `claims.ts` says which edges are in force. This says how they read: the
 * colour of a basis, how an edge's bar ended, which edges are one
 * proposition, what a provenance card says and which records it cites. The
 * scene, the strip and the card all read it, so they cannot disagree, and
 * `tests/claims-view.spec.ts` holds it to the binding's own fixture with no
 * stack and no WebGL.
 *
 * Still claims made visible, not a graph view (the prototype's restraint
 * ruling): no layout of its own, every edge drawn where its parties are.
 */
import {
  describe, predicateLabel, seconds, PART_OF, SF_WITHIN, NEAR_BASE,
  type Claim, type LaneEvent
} from './claims';

/**
 * One colour per basis, shared with the extents (`extentColour` in app.ts
 * reads this), so an edge and a box that are the same kind of claim are the
 * same colour.
 */
export const BASIS_CSS: Record<string, string> = {
  OBSERVED: 'rgba(95, 196, 205, 0.9)',
  DECLARED: 'rgba(240, 196, 78, 0.9)',
  DERIVED: 'rgba(197, 128, 240, 0.9)',
  AUTHORED: 'rgba(200, 200, 200, 0.75)'
};

export function basisCss(basis: string | undefined): string {
  return BASIS_CSS[basis ?? ''] ?? BASIS_CSS.AUTHORED;
}

/** The order the legend lists them in. */
export const BASIS_ORDER = ['OBSERVED', 'DECLARED', 'AUTHORED', 'DERIVED'];

/**
 * How an edge stopped being drawn, and the two must never look alike.
 *
 * - `relationship`: the world changed. "no longer holds", including a party
 *   withdrawn, which the asserters spell the same way.
 * - `assertion`: somebody stopped saying it, and the world was not consulted.
 *   "asserter stopped", "subject unobserved", a dispose with no terminal
 *   sample, or an asserter whose liveliness ended (that one is still open:
 *   it stands, and nobody asserts it).
 *
 * That split is the bitemporal design's whole argument, so it is decided
 * here, once, from the reason the asserter gave.
 */
export type EndKind = 'relationship' | 'assertion';

export const NO_LONGER_HOLDS = 'no longer holds';

export function endKind(reason: string): EndKind {
  return reason.startsWith(NO_LONGER_HOLDS) ? 'relationship' : 'assertion';
}

/** How a predicate is drawn in the scene. */
export type Treatment = 'containment' | 'part-of' | 'line';

export function treatment(predicate: string): Treatment {
  if (predicate === SF_WITHIN) {
    return 'containment';
  }
  if (predicate === PART_OF) {
    return 'part-of';
  }
  return 'line';
}

export function isNear(predicate: string): boolean {
  return predicate.startsWith(`${NEAR_BASE}#`);
}

/** The proposition an edge is about: one per (subject, predicate, object). */
export function propositionOf(c: Pick<Claim, 'subject_ref' | 'predicate' | 'object_ref'>): string {
  return `${c.subject_ref}\u001f${c.predicate}\u001f${c.object_ref}`;
}

/** An asserter's id as a badge reads it: the part after the `/`. */
export function asserterBadge(asserter: string): string {
  return asserter.split('/').pop() || asserter;
}

/** One bar on the strip: one edge, from when it held to when it ended. */
export type Bar = {
  id: string;
  claim: Claim;
  /** Seconds since the epoch. `valid_from`, or the first sample's stamp. */
  start: number;
  /** Whether `start` is the claim's own valid_from or only when it was seen. */
  startStated: boolean;
  /** Null while it is in force. */
  end: number | null;
  endKind: EndKind | null;
  reason: string;
  /** In force, and its asserter's liveliness has ended. */
  writerGone: boolean;
};

/**
 * Every edge seen recently, in force or ended, for the strip.
 *
 * `ClaimStore` keeps the edges in force and a few retirements; the strip
 * needs a window of history, and needs to know *how* each one ended, so it
 * keeps its own. Fed the same events.
 */
export class ClaimTimeline {
  readonly bars = new Map<string, Bar>();
  constructor(private readonly windowS = 180) {}

  apply(ev: LaneEvent, now = Date.now() / 1000): void {
    if (ev.event === 'sample' && ev.data) {
      const c = ev.data as unknown as Claim;
      const was = this.bars.get(c.id);
      const live = was && was.end === null ? was : null;
      if (c.state === 'RETIRED') {
        // The terminal sample. valid_until when the asserter could say when
        // it stopped being true; otherwise the moment it stopped saying so.
        const bar = live ?? this.fresh(c, now);
        bar.claim = c;
        bar.end = c.has_valid_until ? seconds(c.valid_until) : seconds(c.stamp);
        bar.endKind = endKind(c.state_reason);
        bar.reason = c.state_reason;
        bar.writerGone = false;
        this.bars.set(c.id, bar);
        return;
      }
      if (live) {
        live.claim = c;
        live.writerGone = false;
        if (c.has_valid_from && !live.startStated) {
          live.start = seconds(c.valid_from);
          live.startStated = true;
        }
        return;
      }
      this.bars.set(c.id, this.fresh(c, now));
      return;
    }
    if (ev.event === 'dispose' && ev.key) {
      // After a terminal sample the bar has already ended; a bare dispose is
      // a withdrawal nobody explained with a sample, so it ends here.
      const bar = this.bars.get(ev.key);
      if (bar && bar.end === null) {
        bar.end = now;
        bar.reason = ev.reason ?? '';
        bar.endKind = endKind(bar.reason);
        bar.writerGone = false;
      }
      return;
    }
    if (ev.event === 'liveliness' && ev.key) {
      const bar = this.bars.get(ev.key);
      if (bar && bar.end === null) {
        bar.writerGone = !ev.alive;
      }
    }
  }

  private fresh(c: Claim, now: number): Bar {
    return {
      id: c.id, claim: c,
      start: c.has_valid_from ? seconds(c.valid_from)
        : (Number(c.stamp.sec) ? seconds(c.stamp) : now),
      startStated: c.has_valid_from,
      end: null, endKind: null, reason: '', writerGone: false
    };
  }

  /** Bars that touch the window ending now, oldest ended ones forgotten. */
  visible(now = Date.now() / 1000): Bar[] {
    const from = now - this.windowS;
    for (const [id, bar] of this.bars) {
      if (bar.end !== null && bar.end < from - this.windowS) {
        this.bars.delete(id);
      }
    }
    return [...this.bars.values()].filter((b) => b.end === null || b.end >= from);
  }

  get window(): number {
    return this.windowS;
  }
}

/** Edges grouped by proposition: two asserters, one claim, neither winning. */
export type Group = {
  key: string;
  subject: string;
  predicate: string;
  object: string;
  bars: Bar[];
  asserters: string[];
  /** The latest moment anything in the group happened, for the order. */
  latest: number;
  /** Whether any of its edges is in force. */
  open: boolean;
};

/**
 * Newest activity first, whether that was an edge starting or ending, so
 * what just changed is at the top: a retirement is the most telling thing
 * the strip shows, and ordering edges in force first sank every fresh one
 * below the fold. Edges held since long before the window (the declared
 * ones) settle at the bottom by themselves. Inside a group
 * the asserters are in name order: a stable order, not a ranking.
 */
export function groupBars(bars: Bar[]): Group[] {
  const groups = new Map<string, Group>();
  for (const bar of bars) {
    const key = propositionOf(bar.claim);
    const g = groups.get(key) ?? {
      key, subject: bar.claim.subject_ref, predicate: bar.claim.predicate,
      object: bar.claim.object_ref, bars: [], asserters: [], latest: 0,
      open: false
    };
    g.bars.push(bar);
    g.latest = Math.max(g.latest, bar.end ?? bar.start);
    g.open = g.open || bar.end === null;
    groups.set(key, g);
  }
  for (const g of groups.values()) {
    g.bars.sort((a, b) => a.claim.asserter.localeCompare(b.claim.asserter)
      || a.start - b.start);
    g.asserters = [...new Set(g.bars.map((b) => b.claim.asserter))];
  }
  return [...groups.values()].sort((a, b) => (b.latest - a.latest)
    || a.key.localeCompare(b.key));
}

/**
 * The edges in force grouped by proposition, for the scene: each gets its
 * place among its proposition's asserters, so two arcs about one claim are
 * drawn side by side rather than on top of each other.
 */
export function slots(live: Claim[]): Map<string, { index: number; of: number; asserters: string[] }> {
  const by = new Map<string, Claim[]>();
  for (const c of live) {
    const k = propositionOf(c);
    by.set(k, [...(by.get(k) ?? []), c]);
  }
  const out = new Map<string, { index: number; of: number; asserters: string[] }>();
  for (const cs of by.values()) {
    cs.sort((a, b) => a.asserter.localeCompare(b.asserter));
    const asserters = cs.map((c) => c.asserter);
    cs.forEach((c, index) => out.set(c.id, { index, of: cs.length, asserters }));
  }
  return out;
}

/** One line of a provenance card. */
export type CardRow = { label: string; value: string; title?: string };

/**
 * A provenance card's contents: everything the edge says about itself.
 * Values are bus data and are put on the page as text, never markup.
 */
export function cardRows(c: Claim, opts: { writerGone?: boolean; now?: number } = {}): CardRow[] {
  const now = opts.now ?? Date.now() / 1000;
  const when = (t: { sec: string | number; nanosec: number }) => {
    const s = seconds(t);
    return `${new Date(s * 1000).toISOString().replace('T', ' ').slice(0, 19)}Z`
      + ` (${Math.max(0, now - s).toFixed(1)} s ago)`;
  };
  const rows: CardRow[] = [
    { label: 'subject', value: c.subject_ref },
    { label: 'predicate', value: predicateLabel(c.predicate), title: c.predicate },
    { label: 'predicate URI', value: c.predicate },
    { label: 'object', value: c.object_ref },
    { label: 'asserter', value: c.asserter
        + (opts.writerGone ? ' — asserter not alive' : '') },
    { label: 'basis', value: c.basis }
  ];
  if (c.has_confidence) {
    rows.push({ label: 'confidence', value: c.confidence.toFixed(2) });
  }
  rows.push({ label: 'stamp', value: when(c.stamp) });
  rows.push({ label: 'valid from', value: c.has_valid_from ? when(c.valid_from) : '—' });
  rows.push({ label: 'valid until', value: c.has_valid_until ? when(c.valid_until)
    : (c.state === 'RETIRED' ? 'not stated' : 'while asserted') });
  rows.push({ label: 'state', value: c.state + (c.state_reason ? `: ${c.state_reason}` : '') });
  if (c.derived_from.length) {
    rows.push({ label: 'derived from', value: c.derived_from
      .map((r) => `${r.key} @ ${r.topic.split('/').slice(-2).join('/')}`).join('\n') });
  }
  return rows;
}

/**
 * Which records a derived edge cites, as things on screen: entities (by the
 * evidence sample's key, which on the entity and pose lanes is the entity
 * id) and other edges (on the relationship lane, the edge id).
 */
export function citedRecords(c: Claim): { entities: string[]; claims: string[] } {
  const entities = new Set<string>();
  const claims = new Set<string>();
  for (const ref of c.derived_from) {
    if (/\/oarc\.relationship\//.test(ref.topic)) {
      claims.add(ref.key);
    } else {
      entities.add(ref.key);
    }
  }
  return { entities: [...entities], claims: [...claims] };
}

/** The strip's hover text for one bar. */
export function barTitle(bar: Bar): string {
  const head = describe(bar.claim);
  if (bar.end === null) {
    return `${head}\nin force${bar.writerGone ? ' — asserter not alive, nobody asserts it now' : ''}`;
  }
  return `${head}\n${bar.endKind === 'relationship'
    ? 'the relationship ended' : 'the assertion ended'}: ${bar.reason || '(no reason given)'}`;
}
