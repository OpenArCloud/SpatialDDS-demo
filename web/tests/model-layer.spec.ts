import { expect, test } from '@playwright/test';
import {
  BASIS_VALUES, TYPE_LABELS, catalogEntryToItem, catalogRefId, displayName,
  hasUnknownType, matchesBasis, modelEntityToItem, parseBasisFilter,
  planModelRender, resolveInFrame, typeLabel
} from '../src/spatialdds_bridge';

/**
 * The world model layer on the client — demo-local `oarc_model`.
 *
 * Two things are pinned here that the running demo demonstrates but does not
 * prove: that the catalogue path and the model path resolve a pose to the
 * same place, and that the model supersedes exactly the catalogue rows it
 * claims and no others.
 */

const VENUE = 'map/ut-littlefield-fountain';

// The frame transform the catalogue's service announces, as measured.
const FRAMES = {
  [VENUE]: {
    from: { fqn: VENUE },
    to: { fqn: 'earth-fixed' },
    pose: {
      t: [-742398.4920798013, -5462355.283318999, 3197669.647851296],
      q: [0.49671722355450676, -0.033600407060018246,
          -0.058532108356669665, 0.8652843448856144]
    }
  }
};

const DUCK_CONTENT_ID = '89f2d953-076d-5c7d-9b74-1193f71685a6';
const POSE = { t: [11.708, -14.273, -1.423], q: [0, 0, -0.7071067811865475, 0.7071067811865476] };

test.describe('one pose, one answer', () => {
  /**
   * The obligation created by sharing `resolveInFrame` between the two paths.
   *
   * Both are saying the same thing — a pose in a named frame — and the whole
   * reason the layer exists is to stop two sources disagreeing about where
   * one duck is. Sharing the resolver makes that true today; this makes it
   * true tomorrow, when someone changes one caller and not the other.
   */
  test('the catalog path and the model path place the same pose identically', () => {
    const fromCatalog = catalogEntryToItem({
      content_id: DUCK_CONTENT_ID,
      name: 'Rubber duck',
      kind: 'model',
      coverage: [{ has_bbox: true, bbox: [-97.7397, 30.2838, -97.7396, 30.2839] }],
      frame_ref: { fqn: VENUE },
      has_pose: true,
      pose: POSE,
      has_asset: true,
      asset: { uri: 'http://example.test/duck.glb', hash: 'sha256:abc' }
    }, FRAMES as any);

    const fromModel = modelEntityToItem({
      entity_id: 'ent:duck:catalog-pose',
      frame_ref: { fqn: VENUE },
      has_pose: true,
      pose: POSE,
      content_refs: [`catalog:${DUCK_CONTENT_ID}`]
    }, FRAMES as any, () => ({ uri: 'http://example.test/duck.glb', hash: 'sha256:abc' }));

    expect(fromModel).not.toBeNull();
    expect(fromModel!.geopose.lat_deg).toBeCloseTo(fromCatalog.geopose.lat_deg, 9);
    expect(fromModel!.geopose.lon_deg).toBeCloseTo(fromCatalog.geopose.lon_deg, 9);
    expect(fromModel!.geopose.alt_m).toBeCloseTo(fromCatalog.geopose.alt_m, 9);
    expect(fromModel!.orientation).toEqual(fromCatalog.orientation);
  });

  test('an entity whose frame does not resolve is not placed at all', () => {
    // Better to draw nothing than to draw it at the centre of the earth,
    // which is where an unresolved local pose would otherwise land.
    const item = modelEntityToItem({
      entity_id: 'ent:duck:orphan',
      frame_ref: { fqn: 'map/nobody-announced-this' },
      has_pose: true,
      pose: POSE,
      content_refs: [`catalog:${DUCK_CONTENT_ID}`]
    }, FRAMES as any, () => undefined);
    expect(item).toBeNull();
  });

  test('resolveInFrame declines rather than guessing', () => {
    expect(resolveInFrame(undefined, POSE)).toBeNull();
    expect(resolveInFrame(FRAMES[VENUE] as any, null)).toBeNull();
    expect(resolveInFrame(FRAMES[VENUE] as any, { q: POSE.q })).toBeNull();
  });
});

test.describe('asset versus instance', () => {
  test('three entities share one content ref and differ in id and pose', () => {
    const entities = [
      { entity_id: 'ent:duck:a', frame_ref: { fqn: VENUE }, has_pose: true,
        pose: { t: [11.7, -14.2, -1.4], q: [0, 0, 0, 1] },
        content_refs: [`catalog:${DUCK_CONTENT_ID}`] },
      { entity_id: 'ent:duck:b', frame_ref: { fqn: VENUE }, has_pose: true,
        pose: { t: [6.5, -8.0, -1.4], q: [0, 0, 0, 1] },
        content_refs: [`catalog:${DUCK_CONTENT_ID}`] },
      { entity_id: 'ent:duck:c', frame_ref: { fqn: VENUE }, has_pose: true,
        pose: { t: [16.5, -10.5, -1.4], q: [0, 0, 0, 1] },
        content_refs: [`catalog:${DUCK_CONTENT_ID}`] }
    ];
    const assets = { [DUCK_CONTENT_ID]: { uri: 'http://example.test/duck.glb' } };
    const items = entities.map(
      (e) => modelEntityToItem(e, FRAMES as any, (id) => (assets as any)[id])!);

    expect(items.every((i) => i !== null)).toBe(true);
    // One asset...
    expect(new Set(items.map((i) => i.model_url)).size).toBe(1);
    // ...three instances.
    expect(new Set(items.map((i) => i.id)).size).toBe(3);
    expect(new Set(items.map((i) => i.geopose.lat_deg)).size).toBe(3);
  });

  test('an entity with no asset is not drawn as a model', () => {
    // The fountain: it is already in the photorealistic tiles.
    const item = modelEntityToItem({
      entity_id: 'ent:fountain:littlefield',
      frame_ref: { fqn: VENUE },
      has_pose: true,
      pose: { t: [11.708, -5.23, -1.42], q: [0, 0, 0, 1] },
      content_refs: []
    }, FRAMES as any, () => undefined);
    expect(item!.kind).toBe('poi');
    expect(item!.model_url).toBeUndefined();
  });
});

test.describe('suppression is per content_id', () => {
  /**
   * The model supersedes the rows it claims, and only those.
   *
   * A future second catalogue row must not be swept up by the model taking
   * responsibility for the first — the count is asserted so that widening
   * cannot happen quietly.
   */
  const claimedBy = (entities: any[]) => {
    const claimed = new Set<string>();
    for (const entity of entities) {
      const ref = catalogRefId(entity);
      if (ref) {
        claimed.add(ref);
      }
    }
    return claimed;
  };

  test('three ducks supersede exactly one catalog row', () => {
    const entities = ['a', 'b', 'c'].map((s) => ({
      entity_id: `ent:duck:${s}`, content_refs: [`catalog:${DUCK_CONTENT_ID}`]
    }));
    const claimed = claimedBy(entities);
    expect(claimed.size).toBe(1);
    expect(claimed.has(DUCK_CONTENT_ID)).toBe(true);
  });

  test('an unreferenced catalog row still places itself', () => {
    const entities = [{ entity_id: 'ent:duck:a',
                        content_refs: [`catalog:${DUCK_CONTENT_ID}`] }];
    const claimed = claimedBy(entities);
    const rows = [{ id: DUCK_CONTENT_ID }, { id: 'some-other-content-id' }];
    const survives = rows.filter((r) => !claimed.has(r.id));
    expect(survives.map((r) => r.id)).toEqual(['some-other-content-id']);
  });

  test('an entity with no catalog ref claims nothing', () => {
    expect(claimedBy([{ entity_id: 'ent:fountain', content_refs: [] }]).size).toBe(0);
    // A scheme this client does not understand must not be read as a claim.
    expect(claimedBy([{ entity_id: 'ent:x',
                        content_refs: ['spatialdds://elsewhere/manifest:1'] }]).size)
      .toBe(0);
  });
});

test.describe('borrowed vocabulary', () => {
  /**
   * The table is data, so "do we know this type?" is a lookup rather than a
   * branch. The miss is the case that matters: a publisher we have never met,
   * using a vocabulary we do not carry, still has to render.
   */
  test('the types this demo publishes resolve to labels', () => {
    expect(typeLabel('http://www.wikidata.org/entity/Q483453')).toBe('Fountain');
    expect(typeLabel('http://www.wikidata.org/entity/Q851478')).toBe('Rubber duck');
  });

  test('an unknown type is a miss, not an error', () => {
    expect(typeLabel('https://example.org/vocab/garden-gnome')).toBeUndefined();
    expect(typeLabel('')).toBeUndefined();
    expect(typeLabel('not even a uri')).toBeUndefined();
  });

  test('the table is borrowed vocabulary only', () => {
    // The layer mints no types of its own. Every key is somebody else's URI,
    // which is the property that makes the model interoperable rather than a
    // private ontology.
    for (const uri of Object.keys(TYPE_LABELS)) {
      expect(uri).toMatch(/^https?:\/\//);
      expect(uri).not.toContain('oarc');
      expect(uri).not.toContain('spatialdds');
    }
  });

  test('the table cannot be mutated by a consumer', () => {
    // Frozen on purpose: a display convenience that a caller could edit would
    // make "what does this type mean" depend on load order.
    expect(Object.isFrozen(TYPE_LABELS)).toBe(true);
  });
});

test.describe("a stranger's entity", () => {
  /**
   * The case the layer exists to survive: a publisher we have never met,
   * using a vocabulary we do not carry, speaking none of this demo's property
   * conventions. It must render — correctly placed and honestly labelled —
   * and change nothing about what was already on screen.
   */
  const GNOME = {
    entity_id: 'ent:gnome:visitor',
    type_uris: ['https://example.org/vocab/garden-gnome'],
    frame_ref: { fqn: VENUE },
    has_pose: true,
    pose: { t: [20.0, -21.0, -0.98], q: [0, 0, 0, 1] },
    content_refs: [],
    properties: [],
    source_id: 'svc:model:visitor/garden-ornaments'
  };

  test('an unknown type is reported, not hidden', () => {
    expect(hasUnknownType(GNOME)).toBe(true);
    expect(typeLabel(GNOME.type_uris[0])).toBeUndefined();
  });

  test('it is named from the URI tail — poor, and accurate', () => {
    // No demo.label, no known type. The tail is what this publisher claimed
    // the thing is, which beats both a made-up name and no name at all.
    expect(displayName(GNOME)).toBe('garden-gnome');
  });

  test('the naming fallback stops at the first thing that is true', () => {
    const withLabel = { ...GNOME, properties: [{ key: 'demo.label', value: 'Gnorman' }] };
    expect(displayName(withLabel)).toBe('Gnorman');

    const knownType = { ...GNOME, type_uris: ['http://www.wikidata.org/entity/Q483453'] };
    expect(displayName(knownType)).toBe('Fountain');

    const nothingButAnId = { ...GNOME, type_uris: [] };
    expect(displayName(nothingButAnId)).toBe('visitor');
  });

  test('it is placed, and placed correctly', () => {
    const item = modelEntityToItem(GNOME, FRAMES as any, () => undefined);
    expect(item).not.toBeNull();
    // Beside the basin, not in it: south-east of the fountain's centre.
    expect(item!.geopose.lat_deg).toBeCloseTo(30.28378, 4);
    expect(item!.geopose.lon_deg).toBeCloseTo(-97.73954, 4);
  });

  test('it draws as a marker, not as content it never claimed', () => {
    const item = modelEntityToItem(GNOME, FRAMES as any, () => undefined)!;
    expect(item.kind).toBe('poi');       // no asset
    expect(item.model_url).toBeUndefined();
    expect(item.extent).toBeUndefined(); // declared no size, so none is drawn
  });

  test('a known entity is unaffected by the stranger', () => {
    // The regression that would matter: adding an unknown type changing how
    // the things we do understand are treated.
    const duck = {
      entity_id: 'ent:duck:west',
      type_uris: ['http://www.wikidata.org/entity/Q851478'],
      properties: [{ key: 'demo.label', value: 'Bobbin' }],
      frame_ref: { fqn: VENUE }, has_pose: true, pose: POSE,
      content_refs: [`catalog:${DUCK_CONTENT_ID}`]
    };
    expect(hasUnknownType(duck)).toBe(false);
    expect(displayName(duck)).toBe('Bobbin');
    const item = modelEntityToItem(duck, FRAMES as any,
      () => ({ uri: 'http://example.test/duck.glb' }))!;
    expect(item.kind).toBe('model');
    expect(item.model_url).toBe('http://example.test/duck.glb');
  });

  test('an entity with no types at all is not treated as unknown', () => {
    // "Says nothing about its type" and "says something we cannot resolve"
    // are different claims; only the second is worth flagging to a user.
    expect(hasUnknownType({ entity_id: 'ent:x', type_uris: [] })).toBe(false);
    expect(hasUnknownType({ entity_id: 'ent:x' })).toBe(false);
  });
});

test.describe('the basis filter', () => {
  /**
   * `?basis=observed` is a view, not a subscription.
   *
   * The client still receives the whole model and chooses what to draw. That
   * distinction is the point of the feature: a consumer filtering its own
   * view must not change what the bus carries, or two clients pointed at one
   * venue would disagree about what is there.
   */

  const observed = (id: string) => ({ entity_id: id, basis: 'OBSERVED' });
  const authored = (id: string) => ({ entity_id: id, basis: 'AUTHORED' });

  test('no parameter means no filter', () => {
    for (const raw of [null, undefined, '', '   ']) {
      const filter = parseBasisFilter(raw);
      expect(filter.wanted).toBeNull();
      expect(matchesBasis(authored('ent:duck:west'), filter)).toBe(true);
      expect(matchesBasis(observed('ent:fountain:littlefield'), filter)).toBe(true);
    }
  });

  test('observed keeps the fountain and hides the ducks and the gnome', () => {
    const filter = parseBasisFilter('observed');
    expect(matchesBasis(observed('ent:fountain:littlefield'), filter)).toBe(true);
    expect(matchesBasis(authored('ent:duck:west'), filter)).toBe(false);
    expect(matchesBasis(authored('ent:gnome:visitor'), filter)).toBe(false);
  });

  test('authored is the exact inverse', () => {
    const all = [observed('ent:fountain:littlefield'), authored('ent:duck:west'),
                 authored('ent:duck:east'), authored('ent:duck:catalog-pose'),
                 authored('ent:gnome:visitor')];
    const o = parseBasisFilter('observed');
    const a = parseBasisFilter('authored');
    // Nothing is in both views, and nothing falls out of both.
    for (const entity of all) {
      expect(matchesBasis(entity, o)).toBe(!matchesBasis(entity, a));
    }
    expect(all.filter((e) => matchesBasis(e, o)).length).toBe(1);
    expect(all.filter((e) => matchesBasis(e, a)).length).toBe(4);
  });

  test('case does not matter and several values can be asked for', () => {
    const filter = parseBasisFilter('Observed, AUTHORED');
    expect([...filter.wanted!].sort()).toEqual(['AUTHORED', 'OBSERVED']);
    expect(filter.unrecognised).toEqual([]);
  });

  test('it knows the whole enum, not just what this demo publishes', () => {
    // DECLARED and DERIVED are exactly what a second publisher turns up with.
    expect([...BASIS_VALUES].sort())
      .toEqual(['AUTHORED', 'DECLARED', 'DERIVED', 'OBSERVED']);
    for (const value of BASIS_VALUES) {
      expect(parseBasisFilter(value.toLowerCase()).wanted!.has(value)).toBe(true);
    }
  });

  test('a value nobody recognises shows everything and reports itself', () => {
    // A typo that silently empties the scene reads as "there is nothing
    // here", which is a claim about the venue rather than about the URL.
    const filter = parseBasisFilter('obsevred');
    expect(filter.wanted).toBeNull();
    expect(filter.unrecognised).toEqual(['obsevred']);
    expect(matchesBasis(authored('ent:duck:west'), filter)).toBe(true);
  });

  test('a recognised value survives alongside an unrecognised one', () => {
    const filter = parseBasisFilter('observed,banana');
    expect([...filter.wanted!]).toEqual(['OBSERVED']);
    expect(filter.unrecognised).toEqual(['banana']);
    expect(matchesBasis(authored('ent:duck:west'), filter)).toBe(false);
  });

  test('an entity that does not state its basis is hidden, not assumed', () => {
    const filter = parseBasisFilter('observed');
    expect(matchesBasis({ entity_id: 'ent:mystery' }, filter)).toBe(false);
    // ...but only while a filter is active. With no filter it draws as before.
    expect(matchesBasis({ entity_id: 'ent:mystery' }, parseBasisFilter(null)))
      .toBe(true);
  });

  test('filtering hides entities without unclaiming their catalogue rows', () => {
    /**
     * The ordering bug this guards, in the shipped function rather than in a
     * re-statement of it.
     *
     * Suppression is decided from every entity, before the filter runs. If it
     * were decided from the survivors, hiding the model's ducks under
     * `?basis=observed` would hand the duck back to the catalogue and it would
     * reappear at the catalogue pose -- a filter putting a duck on screen.
     */
    const entities = [
      { entity_id: 'ent:fountain:littlefield', basis: 'OBSERVED', content_refs: [] },
      { entity_id: 'ent:duck:west', basis: 'AUTHORED',
        content_refs: [`catalog:${DUCK_CONTENT_ID}`] }
    ];
    const catalogItems = [{ id: DUCK_CONTENT_ID, name: 'Rubber duck' } as any];

    const plan = planModelRender(entities, catalogItems, parseBasisFilter('observed'));
    expect(plan.claimed.has(DUCK_CONTENT_ID)).toBe(true);
    // The duck is hidden, and it stays hidden: the catalogue does not get it
    // back. Nothing is drawn from either source for that content_id.
    expect(plan.visible.map((e) => e.entity_id)).toEqual(['ent:fountain:littlefield']);
    expect(plan.fromCatalog).toEqual([]);
    expect(plan.hidden).toBe(1);
  });

  test('an unclaimed catalogue row still draws itself under a filter', () => {
    // Suppression is per content_id, not wholesale. A row no entity claims is
    // not the model's to hide.
    const entities = [
      { entity_id: 'ent:fountain:littlefield', basis: 'OBSERVED', content_refs: [] }
    ];
    const catalogItems = [{ id: 'some-other-row', name: 'Signpost' } as any];
    const plan = planModelRender(entities, catalogItems, parseBasisFilter('authored'));
    expect(plan.fromCatalog.map((i) => i.id)).toEqual(['some-other-row']);
    expect(plan.visible).toEqual([]);
    expect(plan.hidden).toBe(1);
  });

  test('with no filter the plan draws the whole model and claims as before', () => {
    const entities = [
      { entity_id: 'ent:fountain:littlefield', basis: 'OBSERVED', content_refs: [] },
      { entity_id: 'ent:duck:west', basis: 'AUTHORED',
        content_refs: [`catalog:${DUCK_CONTENT_ID}`] }
    ];
    const plan = planModelRender(
      entities, [{ id: DUCK_CONTENT_ID } as any], parseBasisFilter(null));
    expect(plan.visible.length).toBe(2);
    expect(plan.hidden).toBe(0);
    expect(plan.fromCatalog).toEqual([]);
    expect(plan.unstated).toEqual([]);
  });

  test('an entity with no stated basis is reported, not just dropped', () => {
    const entities = [{ entity_id: 'ent:mystery', content_refs: [] }];
    const filtered = planModelRender(entities, [], parseBasisFilter('observed'));
    expect(filtered.visible).toEqual([]);
    expect(filtered.unstated.map((e) => e.entity_id)).toEqual(['ent:mystery']);
    // Unfiltered it draws as before and there is nothing to report.
    const unfiltered = planModelRender(entities, [], parseBasisFilter(null));
    expect(unfiltered.visible.length).toBe(1);
    expect(unfiltered.unstated).toEqual([]);
  });
});
