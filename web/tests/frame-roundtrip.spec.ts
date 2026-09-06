import { expect, test } from '@playwright/test';
import { localInFrame, resolveInFrame } from '../src/spatialdds_bridge';

const VENUE = 'map/ut-littlefield-fountain';
const FRAME = {
  from: { fqn: VENUE }, to: { fqn: 'earth-fixed' },
  pose: { t: [-742398.4920798013, -5462355.283318999, 3197669.647851296],
          q: [0.49671722355450676, -0.033600407060018246,
              -0.058532108356669665, 0.8652843448856144] }
};

test('a point survives the round trip through the frame', () => {
  // The obligation created by having two directions: a tap converted to
  // local metres and back must land where it started, or a click lands
  // somewhere other than where it was aimed.
  for (const local of [[14.0, -22.0, -1.9], [0, 0, 0], [-6.5, 11.02, 2.5]]) {
    const placed = resolveInFrame(FRAME as any, { t: local, q: [0, 0, 0, 1] })!;
    expect(placed).not.toBeNull();
    // Back to ECEF the way Cesium would, then into the frame again.
    const rad = Math.PI / 180;
    const a = 6378137.0, f = 1 / 298.257223563;
    const e2 = f * (2 - f);
    const lat = placed.placed.lat_deg * rad, lon = placed.placed.lon_deg * rad;
    const N = a / Math.sqrt(1 - e2 * Math.sin(lat) ** 2);
    const ecef = {
      x: (N + placed.placed.alt_m) * Math.cos(lat) * Math.cos(lon),
      y: (N + placed.placed.alt_m) * Math.cos(lat) * Math.sin(lon),
      z: (N * (1 - e2) + placed.placed.alt_m) * Math.sin(lat)
    };
    const back = localInFrame(FRAME as any, ecef)!;
    for (let i = 0; i < 3; i += 1) {
      expect(Math.abs(back[i] - local[i])).toBeLessThan(0.01);
    }
  }
});
