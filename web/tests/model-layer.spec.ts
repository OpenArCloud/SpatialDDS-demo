import { expect, test } from '@playwright/test';
import {
  catalogEntryToItem, catalogRefId, modelEntityToItem, resolveInFrame
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
