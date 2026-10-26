/**
 * Claims about the venue, from the OWM 0.2 relationship prototype's lane.
 *
 * `oarc.relationship` is a demo-owned extension in the 0.2 design's shape
 * (`idl/demo18/oarc_owm02.idl`; census and ruling in
 * `docs/owm-02-census.md`). The binding serves it public and latched, like
 * the entity lane, so reading it needs no credential and a late tab is
 * handed every edge in force.
 *
 * Deliberately modest, as ruled: a claims panel and a containment highlight.
 * Not a graph view. What this module holds is what both need, as pure
 * functions over the lane's events, so the panel and the highlight cannot
 * disagree about which edges are in force, and a test can hold them to a
 * fixture the binding itself produced (`tests/fixtures/relationship_events.json`).
 *
 * **Two asserters about one proposition are two rows.** The zone watches
 * both claim the robot is within the fountain, from different evidence, and
 * neither wins. Nothing here merges them, ranks them or picks one; the panel
 * filters by asserter instead.
 */
import { BINDING_URL, SCENE } from './spatialdds_binding';

export const RELATIONSHIP_PATH = `/${SCENE}/world/oarc.relationship/v1`;

export const SF_WITHIN = 'http://www.opengis.net/ont/geosparql#sfWithin';
export const PART_OF = 'http://www.wikidata.org/prop/direct/P361';
export const NEAR_BASE = 'https://demo.spatialdds.org/owm/0.2/predicate/near';

type WireTime = { sec: string | number; nanosec: number };

/** One edge, as N.1 serves it. */
export type Claim = {
  id: string;
  subject_ref: string;
  predicate: string;
  object_ref: string;
  basis: string;
  asserter: string;
  has_confidence: boolean;
  confidence: number;
  derived_from: Array<{ topic: string; key: string; stamp: WireTime }>;
  stamp: WireTime;
  has_valid_from: boolean;
  valid_from: WireTime;
  has_valid_until: boolean;
  valid_until: WireTime;
  meta: Array<{ key: string; value: string }>;
  state: 'CREATED' | 'REVISED' | 'RETIRED' | string;
  state_reason: string;
};

/** An N.5 event, the three kinds this lane carries. */
export type LaneEvent = {
  event?: string;
  key?: string;
  data?: Record<string, unknown>;
  reason?: string;
  replayed?: number;
  alive?: boolean;
};

/** The edge in force for each id, and the most recent retirements. */
export class ClaimStore {
  readonly live = new Map<string, Claim>();
  /** Newest first, with the reason the asserter gave. */
  readonly retired: Claim[] = [];
  /**
   * Edges in force whose asserter's liveliness has ended. A killed asserter
   * withdraws nothing, so its edges stand; what a reader can know is that
   * nobody is asserting them any more, and the panel says so.
   */
  readonly writerGone = new Set<string>();
  constructor(private readonly keepRetired = 8) {}

  /** Apply one event. Returns what changed, for the log. */
  apply(ev: LaneEvent): string | null {
    if (ev.event === 'sample' && ev.data) {
      const claim = ev.data as unknown as Claim;
      if (claim.state === 'RETIRED') {
        // The terminal sample: the last thing the edge says, and the only
        // place valid_until is. The dispose that follows removes it.
        this.live.delete(claim.id);
        if (this.retired[0]?.id !== claim.id) {
          this.retired.unshift(claim);
          this.retired.length = Math.min(this.retired.length, this.keepRetired);
        } else {
          this.retired[0] = claim;
        }
        return `claim retired: ${describe(claim)} (${claim.state_reason})`;
      }
      const was = this.live.get(claim.id);
      this.live.set(claim.id, claim);
      this.writerGone.delete(claim.id);
      return was ? null : `claim: ${describe(claim)}`;
    }
    if (ev.event === 'liveliness' && ev.key) {
      const claim = this.live.get(ev.key);
      if (ev.alive) {
        this.writerGone.delete(ev.key);
        return null;
      }
      this.writerGone.add(ev.key);
      return claim ? `claim's asserter is gone, the edge stands: ${describe(claim)}`
        : null;
    }
    if (ev.event === 'dispose' && ev.key) {
      const gone = this.live.get(ev.key);
      this.live.delete(ev.key);
      this.writerGone.delete(ev.key);
      return gone ? `claim withdrawn: ${describe(gone)} (${ev.reason ?? ''})`
        : null;
    }
    return null;
  }

  asserters(): string[] {
    return [...new Set([...this.live.values()].map((c) => c.asserter))].sort();
  }

  /** The edges in force, optionally one asserter's, in a stable order. */
  rows(asserter = ''): Claim[] {
    return [...this.live.values()]
      .filter((c) => !asserter || c.asserter === asserter)
      .sort((a, b) => (a.subject_ref + a.predicate + a.object_ref + a.asserter)
        .localeCompare(b.subject_ref + b.predicate + b.object_ref + b.asserter));
  }

  /**
   * What to highlight: the object of every OBSERVED `sfWithin` in force, and
   * the subjects inside it.
   *
   * OBSERVED only. The declared containments (each duck within the shallows)
   * are true for as long as the venue runs, so lighting their objects would
   * light the shallows permanently and say nothing. An observed containment
   * comes and goes with the world, which is what a highlight can show.
   */
  containment(): Map<string, Set<string>> {
    const out = new Map<string, Set<string>>();
    for (const c of this.live.values()) {
      if (c.predicate !== SF_WITHIN || c.basis !== 'OBSERVED') {
        continue;
      }
      const inside = out.get(c.object_ref) ?? new Set<string>();
      inside.add(c.subject_ref);
      out.set(c.object_ref, inside);
    }
    return out;
  }
}

/** A predicate URI as a person reads it. */
export function predicateLabel(uri: string): string {
  if (uri === SF_WITHIN) {
    return 'within';
  }
  if (uri === PART_OF) {
    return 'part of';
  }
  if (uri.startsWith(`${NEAR_BASE}#`)) {
    return `near (${uri.slice(NEAR_BASE.length + 1)})`;
  }
  return uri.split(/[#/]/).pop() || uri;
}

/** Seconds since the epoch, from N.1's int64-as-string. */
export function seconds(t: WireTime): number {
  return Number(t.sec) + t.nanosec / 1e9;
}

/** When the claim is about, honestly: from, until, or "while asserted". */
export function validity(c: Claim, now = Date.now() / 1000): string {
  const ago = (t: WireTime) => `${Math.max(0, now - seconds(t)).toFixed(1)} s ago`;
  const from = c.has_valid_from ? `from ${ago(c.valid_from)}` : '';
  const until = c.has_valid_until ? `until ${ago(c.valid_until)}`
    : 'while asserted';
  return [from, until].filter(Boolean).join(', ');
}

export function describe(c: Claim): string {
  return `${c.subject_ref} ${predicateLabel(c.predicate)} ${c.object_ref}` +
    ` — ${c.basis}, by ${c.asserter}`;
}

export type ClaimHandlers = {
  onEvent(ev: LaneEvent): void;
  onReplayComplete(replayed: number): void;
  onStatus(line: string): void;
};

/**
 * The lane over N.5: replay, one `end_of_replay`, then live, as the model
 * lanes are subscribed. Its own socket, because N.5 subscribes one resource
 * per connection. Returns a function that closes it and stops reconnecting.
 */
export function subscribeClaims(handlers: ClaimHandlers): () => void {
  let ws: WebSocket | null = null;
  let stopped = false;
  const open = () => {
    if (stopped) {
      return;
    }
    try {
      ws = new WebSocket(`${BINDING_URL.replace(/^http/, 'ws')}${RELATIONSHIP_PATH}`);
    } catch (e) {
      handlers.onStatus(`claims: socket failed (${String(e)})`);
      return;
    }
    ws.onopen = () => handlers.onStatus('claims: subscribed over N.5');
    ws.onmessage = (event) => {
      let msg: LaneEvent;
      try {
        msg = JSON.parse(event.data as string);
      } catch {
        return;
      }
      if (msg.event === 'end_of_replay') {
        handlers.onReplayComplete(Number(msg.replayed || 0));
        return;
      }
      handlers.onEvent(msg);
    };
    ws.onclose = () => {
      if (!stopped) {
        handlers.onStatus('claims: closed; reconnecting');
        window.setTimeout(open, 2000);
      }
    };
  };
  open();
  return () => {
    stopped = true;
    ws?.close();
  };
}
