/**
 * The claims overlay: the relationship lane drawn in the scene, on a strip,
 * and on a card. One switch turns all of it on and off.
 *
 * Three surfaces over one store, so they cannot disagree:
 *
 * - **The scene.** Each edge in force, coloured by basis, where its parties
 *   are, following them as they move. Treatment by predicate: containment
 *   is a soft ring under the thing contained, part-of a faint outline nested
 *   around the part's own box, near a plain line, anything else an arc.
 *   Two asserters about one proposition are drawn side by side, neither on
 *   top, with their names at the apex. An asserter whose liveliness ended
 *   is drawn dashed: its edges stand and nobody asserts them.
 * - **The strip.** Each edge as a bar over the last few minutes, grouped by
 *   proposition. A bar that ended because the relationship ended ("no
 *   longer holds") stops with a hard cap; one that ended because somebody
 *   stopped asserting it fades out. That difference is the bitemporal
 *   design's argument, so it is never left to the hover text alone.
 * - **The card.** Click an edge, in the scene or on the strip, for what it
 *   says about itself. A derived edge's card lights the records it cites.
 *
 * Everything that is bus data reaches the page as text, never markup.
 * What to draw is decided in `claims_view.ts`, which is tested on its own.
 */
import * as Cesium from 'cesium';

import { predicateLabel, type Claim, type ClaimStore, type LaneEvent } from './claims';
import {
  BASIS_ORDER, ClaimTimeline, asserterBadge, barTitle, basisCss, cardRows,
  citedRecords, groupBars, isNear, slots, treatment, type Bar
} from './claims_view';
import type { CatalogItem } from './types';

/** What the overlay needs from the page. */
export type OverlayHost = {
  viewer: Cesium.Viewer;
  /** The model entity on screen with this id, as last placed or moved. */
  item(entityId: string): CatalogItem | undefined;
  /** Whether the observed-containment zone highlight should be lit. */
  onVisibilityChange(on: boolean): void;
};

/** The id prefix every Cesium entity of the overlay carries. */
export const EDGE_PREFIX = 'claim:';

const CITED = Cesium.Color.fromCssColorString('rgba(255, 255, 255, 0.95)');

/**
 * How far above a pose a ring is drawn. The photoreal mesh's water sits
 * above the model's pose height for things in the fountain, so a ring at the
 * pose was drawn under it, invisible, while every check that it existed
 * passed. Found in the review capture.
 */
const RING_LIFT_M = 0.45;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) {
    e.className = cls;
  }
  if (text !== undefined) {
    e.textContent = text;
  }
  return e;
}

/** A short name for an entity id: `ent:duck:east` reads as `duck:east`. */
function shortId(id: string): string {
  return id.startsWith('ent:') ? id.slice(4) : id;
}

export class ClaimsOverlay {
  readonly timeline = new ClaimTimeline(180);
  private on = true;
  /** Cesium entities drawn for each edge in force, by edge id. */
  private readonly drawn = new Map<string, Cesium.Entity[]>();
  /** What each edge was drawn as, so a change redraws and nothing else does. */
  private readonly drawnAs = new Map<string, string>();
  private readonly cited: Cesium.Entity[] = [];
  private cardId: string | null = null;
  private stripTimer: number | null = null;
  /** What the strip was last built as, and its bars, for moving in place. */
  private stripShape = '';
  private readonly barEls = new Map<string, HTMLElement>();
  private readonly groupEls = new Map<string, {
    group: HTMLElement; label: HTMLElement; tracks: HTMLElement; asserters: string }>();
  private axisEl: HTMLElement | null = null;

  constructor(
    private readonly host: OverlayHost,
    private readonly store: ClaimStore,
    private readonly strip: HTMLElement,
    private readonly stripBody: HTMLElement,
    private readonly card: HTMLElement
  ) {
    this.buildLegend();
    host.viewer.selectedEntityChanged.addEventListener((picked?: Cesium.Entity) => {
      const id = picked?.id;
      if (typeof id === 'string' && id.startsWith(EDGE_PREFIX)) {
        // Our card, not Cesium's info box: the card can light the evidence.
        host.viewer.selectedEntity = undefined;
        this.openCard(id.slice(EDGE_PREFIX.length).split('#')[0]);
      }
    });
    this.startTimer();
  }

  get visible(): boolean {
    return this.on;
  }

  /** One lane event, after the store has applied it. */
  apply(ev: LaneEvent): void {
    this.timeline.apply(ev);
    this.sync();
    if (this.cardId && ev.key === this.cardId) {
      this.renderCard();
    }
  }

  setVisible(on: boolean): void {
    this.on = on;
    this.strip.classList.toggle('hidden', !on);
    for (const parts of this.drawn.values()) {
      for (const e of parts) {
        e.show = on;
      }
    }
    if (!on) {
      this.closeCard();
    }
    this.startTimer();
    this.host.onVisibilityChange(on);
    this.stripShape = '';
    this.renderStrip();
  }

  // -- the scene -------------------------------------------------------------

  /** Draw what is in force, remove what is not, redraw what changed. */
  sync(): void {
    const live = [...this.store.live.values()];
    const place = slots(live);
    const keep = new Set<string>();
    for (const c of live) {
      const slot = place.get(c.id) ?? { index: 0, of: 1, asserters: [c.asserter] };
      const gone = this.store.writerGone.has(c.id);
      const as = [c.basis, c.predicate, c.subject_ref, c.object_ref,
        slot.index, slot.of, gone].join('|');
      keep.add(c.id);
      if (this.drawnAs.get(c.id) === as) {
        continue;
      }
      this.remove(c.id);
      this.drawn.set(c.id, this.draw(c, slot, gone));
      this.drawnAs.set(c.id, as);
    }
    for (const id of [...this.drawn.keys()]) {
      if (!keep.has(id)) {
        this.remove(id);
      }
    }
    this.markCitedEdges();
    this.renderStrip();
  }

  private remove(id: string): void {
    for (const e of this.drawn.get(id) ?? []) {
      this.host.viewer.entities.remove(e);
    }
    this.drawn.delete(id);
    this.drawnAs.delete(id);
  }

  /** Where an edge attaches to an entity: the top of its box, or above it. */
  private anchor(id: string): Cesium.Cartesian3 | undefined {
    const item = this.host.item(id);
    if (!item) {
      return undefined;
    }
    if (item.extent) {
      return Cesium.Cartesian3.fromDegrees(item.extent.lon_deg, item.extent.lat_deg,
        item.extent.alt_m + item.extent.size[2] / 2);
    }
    return Cesium.Cartesian3.fromDegrees(item.geopose.lon_deg, item.geopose.lat_deg,
      item.geopose.alt_m + 0.8);
  }

  private ground(id: string): Cesium.Cartesian3 | undefined {
    const item = this.host.item(id);
    return item ? Cesium.Cartesian3.fromDegrees(item.geopose.lon_deg,
      item.geopose.lat_deg, item.geopose.alt_m + 0.05) : undefined;
  }

  private colour(c: Claim, gone: boolean, alpha?: number): Cesium.Color {
    const base = Cesium.Color.fromCssColorString(basisCss(c.basis));
    return gone ? base.withAlpha(0.45) : (alpha === undefined ? base : base.withAlpha(alpha));
  }

  private draw(c: Claim, slot: { index: number; of: number; asserters: string[] },
               gone: boolean): Cesium.Entity[] {
    const v = this.host.viewer;
    const id = (part: string) => `${EDGE_PREFIX}${c.id}#${part}`;
    const out: Cesium.Entity[] = [];
    const how = treatment(c.predicate);
    const subjectBox = this.host.item(c.subject_ref)?.extent;

    if (how === 'containment') {
      // A soft ring under the thing contained, while the edge holds. A
      // second asserter's ring sits just outside the first.
      const radius = 1.6 + slot.index * 0.7;
      out.push(v.entities.add({
        id: id('ring'),
        name: `${shortId(c.subject_ref)} within ${shortId(c.object_ref)}`,
        position: new Cesium.CallbackPositionProperty(
          () => this.ground(c.subject_ref), false),
        ellipse: {
          semiMajorAxis: radius,
          semiMinorAxis: radius,
          height: new Cesium.CallbackProperty(() => {
            const item = this.host.item(c.subject_ref);
            return item ? item.geopose.alt_m + RING_LIFT_M + slot.index * 0.02 : 0;
          }, false),
          material: this.colour(c, gone, gone ? 0.08 : 0.22),
          outline: true,
          outlineColor: this.colour(c, gone),
          outlineWidth: 2
        }
      }));
    } else if (how === 'part-of' && subjectBox) {
      // A faint outline nested around the part's own box: the box says what
      // it is, the outline says what it belongs to.
      const [sx, sy, sz] = subjectBox.size;
      const pad = 0.6 + slot.index * 0.4;
      out.push(v.entities.add({
        id: id('nest'),
        name: `${shortId(c.subject_ref)} part of ${shortId(c.object_ref)}`,
        position: Cesium.Cartesian3.fromDegrees(subjectBox.lon_deg, subjectBox.lat_deg,
          subjectBox.alt_m),
        orientation: new Cesium.Quaternion(subjectBox.orientation[0],
          subjectBox.orientation[1], subjectBox.orientation[2], subjectBox.orientation[3]),
        box: {
          dimensions: new Cesium.Cartesian3(sx + pad, sy + pad, sz + pad * 0.5),
          fill: false,
          outline: true,
          outlineColor: this.colour(c, gone, 0.45),
          outlineWidth: 1
        }
      }));
    } else {
      // Near is a plain straight line; anything else an arc, so an edge
      // between two things at one height is not lost against the ground.
      const straight = isNear(c.predicate);
      const lift = straight ? slot.index * 0.6 : 0;
      out.push(v.entities.add({
        id: id('line'),
        name: `${shortId(c.subject_ref)} ${predicateLabel(c.predicate)} ${shortId(c.object_ref)}`,
        polyline: {
          positions: new Cesium.CallbackProperty(
            () => this.path(c, straight, slot.index, lift), false),
          width: straight ? 2.5 : 3,
          arcType: Cesium.ArcType.NONE,
          material: gone
            ? new Cesium.PolylineDashMaterialProperty({
                color: this.colour(c, gone), dashLength: 14 })
            : new Cesium.ColorMaterialProperty(this.colour(c, gone))
        }
      }));
    }

    // Two asserters about one proposition: both named, once, by the first.
    if (slot.of > 1 && slot.index === 0) {
      out.push(v.entities.add({
        id: id('badges'),
        position: new Cesium.CallbackPositionProperty(() => this.badgeAt(c, how), false),
        label: {
          text: slot.asserters.map(asserterBadge).join('  ·  '),
          font: '12px "Segoe UI", system-ui, sans-serif',
          fillColor: Cesium.Color.fromCssColorString('#e9eef5'),
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString('rgba(8, 12, 18, 0.8)'),
          backgroundPadding: new Cesium.Cartesian2(6, 3),
          pixelOffset: new Cesium.Cartesian2(0, -14),
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }
      }));
    }
    // The panel's "asserter not alive", in the scene: a ring cannot be
    // dashed, and nothing about a dimmed one says why.
    if (gone) {
      out.push(v.entities.add({
        id: id('gone'),
        position: new Cesium.CallbackPositionProperty(() => this.badgeAt(c, how), false),
        label: {
          text: `${asserterBadge(c.asserter)}: asserter not alive`,
          font: 'italic 11px "Segoe UI", system-ui, sans-serif',
          fillColor: Cesium.Color.fromCssColorString('#c9d3e0'),
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString('rgba(8, 12, 18, 0.7)'),
          backgroundPadding: new Cesium.Cartesian2(5, 2),
          pixelOffset: new Cesium.Cartesian2(0, 8 + slot.index * 16),
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }
      }));
    }
    for (const e of out) {
      e.show = this.on;
    }
    return out;
  }

  /** The points of one edge, subject to object, recomputed every frame. */
  private path(c: Claim, straight: boolean, index: number, lift: number): Cesium.Cartesian3[] {
    const a = this.anchor(c.subject_ref);
    const b = this.anchor(c.object_ref);
    if (!a || !b) {
      return [];
    }
    const mid = Cesium.Cartesian3.midpoint(a, b, new Cesium.Cartesian3());
    const up = Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal(mid, new Cesium.Cartesian3());
    if (straight) {
      const off = Cesium.Cartesian3.multiplyByScalar(up, lift, new Cesium.Cartesian3());
      return [Cesium.Cartesian3.add(a, off, new Cesium.Cartesian3()),
        Cesium.Cartesian3.add(b, off, new Cesium.Cartesian3())];
    }
    const d = Cesium.Cartesian3.distance(a, b);
    const h = Math.max(1.5, 0.18 * d) + index * 1.5;
    const pts: Cesium.Cartesian3[] = [];
    const n = 24;
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      const p = Cesium.Cartesian3.lerp(a, b, t, new Cesium.Cartesian3());
      pts.push(Cesium.Cartesian3.add(p, Cesium.Cartesian3.multiplyByScalar(
        up, 4 * t * (1 - t) * h, new Cesium.Cartesian3()), p));
    }
    return pts;
  }

  private badgeAt(c: Claim, how: string): Cesium.Cartesian3 | undefined {
    if (how === 'containment') {
      const a = this.anchor(c.subject_ref);
      return a ? Cesium.Cartesian3.add(a, Cesium.Cartesian3.multiplyByScalar(
        Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal(a, new Cesium.Cartesian3()), 1.5,
        new Cesium.Cartesian3()), new Cesium.Cartesian3()) : undefined;
    }
    const pts = this.path(c, isNear(c.predicate), 0, 0);
    return pts.length ? pts[Math.floor(pts.length / 2)] : undefined;
  }

  // -- the card ---------------------------------------------------------------

  openCard(id: string): void {
    const c = this.store.live.get(id) ?? this.timeline.bars.get(id)?.claim;
    if (!c || !this.on) {
      return;
    }
    this.cardId = id;
    this.renderCard();
  }

  closeCard(): void {
    this.cardId = null;
    this.card.classList.add('hidden');
    this.card.replaceChildren();
    this.clearCited();
    this.markCitedEdges();
    this.renderStrip();
  }

  /** The edge whose card is open, if any. */
  get openId(): string | null {
    return this.cardId;
  }

  private renderCard(): void {
    const id = this.cardId;
    const c = id ? (this.store.live.get(id) ?? this.timeline.bars.get(id)?.claim) : undefined;
    if (!id || !c) {
      this.closeCard();
      return;
    }
    const head = el('div', 'claimCardHead');
    const swatch = el('span', 'swatch');
    swatch.style.background = basisCss(c.basis);
    const title = el('span', 'claimCardTitle',
      `${shortId(c.subject_ref)} ${predicateLabel(c.predicate)} ${shortId(c.object_ref)}`);
    const close = el('button', 'claimCardClose', '×');
    close.type = 'button';
    close.title = 'Close';
    close.addEventListener('click', () => this.closeCard());
    head.append(swatch, title, close);

    const table = el('table');
    for (const row of cardRows(c, { writerGone: this.store.writerGone.has(id) })) {
      const tr = table.insertRow();
      tr.dataset.label = row.label;
      const th = el('th', undefined, row.label);
      const td = el('td', undefined, row.value);
      if (row.title) {
        td.title = row.title;
      }
      tr.append(th, td);
    }
    const parts: Node[] = [head, table];
    const bar = this.timeline.bars.get(id);
    if (bar && bar.end !== null) {
      parts.push(el('div', `claimCardEnd ${bar.endKind}`,
        bar.endKind === 'relationship' ? 'The relationship ended.'
          : 'The assertion ended; the world was not consulted.'));
    }
    const cited = citedRecords(c);
    if (cited.entities.length || cited.claims.length) {
      parts.push(el('div', 'claimCardCited',
        `Lit in the scene: the ${cited.entities.length + cited.claims.length} record(s) it was derived from.`));
    }
    this.card.replaceChildren(...parts);
    this.card.dataset.claimId = id;
    this.card.classList.remove('hidden');
    this.lightCited(c);
    this.renderStrip();
  }

  /** Light the evidence a derived edge cites, for as long as its card is open. */
  private lightCited(c: Claim): void {
    this.clearCited();
    const { entities } = citedRecords(c);
    for (const entityId of entities) {
      if (!this.host.item(entityId)) {
        continue;
      }
      this.cited.push(this.host.viewer.entities.add({
        id: `${EDGE_PREFIX}cited#${entityId}`,
        position: new Cesium.CallbackPositionProperty(() => this.ground(entityId), false),
        ellipse: {
          semiMajorAxis: 2.4,
          semiMinorAxis: 2.4,
          height: new Cesium.CallbackProperty(() => {
            const item = this.host.item(entityId);
            return item ? item.geopose.alt_m + RING_LIFT_M + 0.03 : 0;
          }, false),
          material: CITED.withAlpha(0.12),
          outline: true,
          outlineColor: CITED,
          outlineWidth: 3
        }
      }));
    }
    this.markCitedEdges();
  }

  private clearCited(): void {
    for (const e of this.cited.splice(0)) {
      this.host.viewer.entities.remove(e);
    }
  }

  /** Edges a derived edge cites are drawn wider while its card is open. */
  private markCitedEdges(): void {
    const open = this.cardId ? this.store.live.get(this.cardId) : undefined;
    const claims = new Set(open ? citedRecords(open).claims : []);
    for (const [id, parts] of this.drawn) {
      for (const e of parts) {
        if (e.polyline) {
          e.polyline.width = new Cesium.ConstantProperty(
            claims.has(id) || id === this.cardId ? 6 : (e.id.endsWith('#line') && isNear(
              this.store.live.get(id)?.predicate ?? '') ? 2.5 : 3));
        }
      }
    }
  }

  /** The Cesium entities ids of what is lit as cited evidence. */
  citedEntityIds(): string[] {
    return this.cited.map((e) => String(e.id).slice(`${EDGE_PREFIX}cited#`.length));
  }

  // -- the strip --------------------------------------------------------------

  private startTimer(): void {
    if (this.stripTimer !== null) {
      window.clearInterval(this.stripTimer);
      this.stripTimer = null;
    }
    if (this.on) {
      // Open bars run to now, so they grow between events.
      this.stripTimer = window.setInterval(() => this.renderStrip(), 1000);
    }
  }

  private buildLegend(): void {
    const legend = this.strip.querySelector('#claimsLegend');
    if (!legend) {
      return;
    }
    const items: Node[] = [];
    for (const basis of BASIS_ORDER) {
      const item = el('span', 'legendItem');
      const sw = el('span', 'swatch');
      sw.style.background = basisCss(basis);
      item.append(sw, document.createTextNode(basis.toLowerCase()));
      items.push(item);
    }
    const rel = el('span', 'legendItem');
    rel.append(el('span', 'endSample relationship'),
      document.createTextNode('relationship ended'));
    const asr = el('span', 'legendItem');
    asr.append(el('span', 'endSample assertion'),
      document.createTextNode('assertion ended'));
    items.push(el('span', 'legendGap'), rel, asr);
    legend.replaceChildren(...items);
  }

  /**
   * The strip, rebuilt only when what it shows changed (an edge appeared,
   * ended, lost its asserter, or its card opened) and otherwise moved in
   * place. Rebuilding every second detached a bar under a pointer that was
   * clicking it, and made its hover text flicker.
   */
  renderStrip(now = Date.now() / 1000): void {
    if (!this.on) {
      return;
    }
    const span = this.timeline.window;
    const from = now - span;
    const pct = (t: number) => Math.max(0, Math.min(100, ((t - from) / span) * 100));
    const groups = groupBars(this.timeline.visible(now));
    const shape = JSON.stringify([this.cardId, groups.map((g) => [g.key, g.asserters,
      g.bars.map((b) => [b.id, b.end, b.endKind, b.writerGone, b.claim.basis])])]);
    if (shape !== this.stripShape) {
      this.stripShape = shape;
      this.buildStrip(groups);
    }
    // Every second: the window slides, so bars and ticks move.
    for (const tick of this.stripBody.querySelectorAll<HTMLElement>('.claimTick')) {
      tick.style.left = `${pct(now - Number(tick.dataset.minutes) * 60)}%`;
    }
    for (const g of groups) {
      for (const bar of g.bars) {
        const b = this.barEls.get(bar.id);
        if (!b) {
          continue;
        }
        const left = pct(bar.start);
        b.style.left = `${left}%`;
        b.style.width = `${Math.max(0.6, pct(bar.end ?? now) - left)}%`;
      }
    }
  }

  /**
   * Lay the strip out for these groups, reusing every group and bar element
   * already on it. The near edges churn every second or two (the ducks flee
   * at the threshold), so a strip rebuilt from new elements replaced a bar
   * while it was being clicked; reused, an element is moved, never swapped.
   */
  private buildStrip(groups: ReturnType<typeof groupBars>): void {
    if (!this.axisEl) {
      const span = this.timeline.window;
      this.axisEl = el('div', 'claimAxis');
      const ticks = el('div', 'claimTicks');
      for (let m = span / 60; m >= 0; m--) {
        const tick = el('span', 'claimTick', m ? `−${m} min` : 'now');
        tick.dataset.minutes = String(m);
        ticks.append(tick);
      }
      this.axisEl.append(el('div', 'claimGroupLabel'), ticks);
    }
    const rows: Node[] = [this.axisEl];
    const seenGroups = new Set<string>();
    const seenBars = new Set<string>();
    for (const g of groups) {
      seenGroups.add(g.key);
      let ge = this.groupEls.get(g.key);
      if (!ge) {
        const group = el('div', 'claimGroup');
        group.dataset.proposition = `${g.subject} ${g.predicate} ${g.object}`;
        const label = el('div', 'claimGroupLabel');
        const tracks = el('div', 'claimTracks');
        group.append(label, tracks);
        ge = { group, label, tracks, asserters: '' };
        this.groupEls.set(g.key, ge);
      }
      const asserters = g.asserters.join('|');
      if (ge.asserters !== asserters) {
        ge.asserters = asserters;
        const parts: Node[] = [el('span', 'claimProp',
          `${shortId(g.subject)} ${predicateLabel(g.predicate)} ${shortId(g.object)}`)];
        for (const a of g.asserters) {
          const badge = el('span', 'asserterBadge', asserterBadge(a));
          badge.title = a;
          parts.push(badge);
        }
        ge.label.replaceChildren(...parts);
      }
      const tracks: Node[] = [];
      for (const bar of g.bars) {
        seenBars.add(bar.id);
        tracks.push(this.barRow(bar));
      }
      ge.tracks.replaceChildren(...tracks);
      rows.push(ge.group);
    }
    for (const key of [...this.groupEls.keys()]) {
      if (!seenGroups.has(key)) {
        this.groupEls.delete(key);
      }
    }
    for (const id of [...this.barEls.keys()]) {
      if (!seenBars.has(id)) {
        this.barEls.delete(id);
      }
    }
    if (rows.length === 1) {
      rows.push(el('div', 'claimEmpty', 'No claims in the last three minutes.'));
    }
    this.stripBody.replaceChildren(...rows);
  }

  /** One bar, made once and kept up to date. */
  private barRow(bar: Bar): HTMLElement {
    let b = this.barEls.get(bar.id);
    if (!b) {
      const made = el('div', 'claimBar');
      made.dataset.claimId = bar.id;
      made.tabIndex = 0;
      made.setAttribute('role', 'button');
      made.addEventListener('click', () => this.openCard(bar.id));
      made.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          this.openCard(bar.id);
        }
      });
      const track = el('div', 'claimTrack');
      track.append(made);
      this.barEls.set(bar.id, made);
      b = made;
    }
    b.style.setProperty('--basis', basisCss(bar.claim.basis));
    b.dataset.asserter = bar.claim.asserter;
    b.dataset.endKind = bar.end === null ? 'open' : (bar.endKind ?? 'assertion');
    if (bar.writerGone) {
      b.dataset.writerGone = 'true';
    } else {
      delete b.dataset.writerGone;
    }
    b.classList.toggle('selected', bar.id === this.cardId);
    b.title = barTitle(bar);
    return b.parentElement as HTMLElement;
  }
}
