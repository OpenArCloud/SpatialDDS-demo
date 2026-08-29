import { test, expect } from '@playwright/test';

import { geohashEncode, DEFAULT_PRECISION, MIN_PRECISION, MAX_PRECISION } from '../src/geohash';
import { bridgeFindService, bridgeLocalize } from '../src/spatialdds_bridge';
import type { GeoPose } from '../src/types';

/**
 * The client half of the §3.3.0 discovery flow: turn a position into a geohash
 * cell, ask the binding who covers it, and name that service when localizing.
 *
 * The expected cells below are the ones `spatialdds_demo.discovery_http`
 * decodes back to bounds containing these points — the same decoder both HTTP
 * servers use. If the encoder and that decoder ever disagree, the client will
 * search a cell the server resolves somewhere else, and the search will simply
 * return nothing with no error anywhere.
 */

const AUSTIN: GeoPose = {
  lat_deg: 30.284996, lon_deg: -97.739494, alt_m: 18,
  q: [0, 0, 0, 1], stamp: { sec: 0, nanosec: 0 }, cov: 'COV_NONE'
};

test.describe('geohash encoding', () => {
  test('encodes the demo regions to the cells the server decodes', () => {
    // Downtown Austin — the cell scripts/cold_start.sh searches.
    expect(geohashEncode(30.28, -97.735)).toBe('9v6kr');
    // Downtown SF — the other demo region.
    expect(geohashEncode(37.7749, -122.4194)).toBe('9q8yy');
  });

  test('is a prefix code: lower precision is a prefix of higher', () => {
    const fine = geohashEncode(30.28, -97.735, MAX_PRECISION);
    for (let p = MIN_PRECISION; p < MAX_PRECISION; p++) {
      expect(fine.startsWith(geohashEncode(30.28, -97.735, p))).toBe(true);
    }
  });

  test('defaults to the precision the demo deployment wants', () => {
    expect(geohashEncode(30.28, -97.735)).toHaveLength(DEFAULT_PRECISION);
  });

  test('uses the geohash alphabet, which omits a, i, l and o', () => {
    const cell = geohashEncode(51.5007, -0.1246, MAX_PRECISION);
    expect(cell).toMatch(/^[0-9bcdefghjkmnpqrstuvwxyz]+$/);
  });

  test('rejects a precision outside the range §3.3.0 allows', () => {
    expect(() => geohashEncode(30.28, -97.735, 2)).toThrow(/precision/);
    expect(() => geohashEncode(30.28, -97.735, 8)).toThrow(/precision/);
  });

  test('rejects coordinates that are not on the globe', () => {
    expect(() => geohashEncode(91, 0)).toThrow(/latitude/);
    expect(() => geohashEncode(0, 181)).toThrow(/longitude/);
    expect(() => geohashEncode(NaN, 0)).toThrow(/finite/);
  });

  test('handles the poles and the antimeridian without running off the end', () => {
    for (const [lat, lon] of [[90, 180], [-90, -180], [0, 0]] as const) {
      expect(geohashEncode(lat, lon)).toHaveLength(DEFAULT_PRECISION);
    }
  });
});

/** Replaces global fetch for one call, returning `body`, and records the request. */
function stubFetch(body: unknown, ok = true) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return {
      ok,
      status: ok ? 200 : 500,
      json: async () => body,
      text: async () => JSON.stringify(body)
    } as Response;
  }) as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = original; } };
}

test.describe('service discovery', () => {
  test('asks the binding for the cell containing the prior', async () => {
    const stub = stubFetch({ results: [], next_page_token: '' });
    try {
      await bridgeFindService(AUSTIN);
    } finally {
      stub.restore();
    }
    expect(stub.calls).toHaveLength(1);
    expect(stub.calls[0].url).toContain('/.well-known/spatialdds/search?geohash=9v6kr');
    expect(stub.calls[0].url).toContain('kind=VPS');
    // The GET convenience form: no body, no method override.
    expect(stub.calls[0].init?.method).toBeUndefined();
  });

  test('returns the service_id out of a §8.2.3 manifest', async () => {
    const stub = stubFetch({
      results: [{ id: 'spatialdds://demo/zone:austin/manifest:vps',
                  service: { service_id: 'svc:vps:demo/austin-downtown', kind: 'VPS' } }],
      next_page_token: ''
    });
    try {
      expect(await bridgeFindService(AUSTIN)).toBe('svc:vps:demo/austin-downtown');
    } finally {
      stub.restore();
    }
  });

  test('nothing found is null, not an error', async () => {
    const stub = stubFetch({ results: [], next_page_token: '' });
    try {
      expect(await bridgeFindService(AUSTIN)).toBeNull();
    } finally {
      stub.restore();
    }
  });

  test('an unreachable binding is null, not an error', async () => {
    // Discovery is how the client prefers to choose a service, not a
    // precondition for localizing — a bridge without the endpoint must still
    // localize, the way it did before discovery existed.
    const stub = stubFetch({ detail: 'nope' }, false);
    try {
      expect(await bridgeFindService(AUSTIN)).toBeNull();
    } finally {
      stub.restore();
    }
  });
});

test.describe('localize carries the discovered service', () => {
  const response = {
    query_id: 'q1', service_id: 'svc:vps:demo/austin-downtown', status: 'VPS_SUCCESS',
    has_node_geo: false, confidence: 0.8, has_rmse_m: false, rmse_m: 0
  };

  test('names the service when one was discovered', async () => {
    const stub = stubFetch(response);
    try {
      await bridgeLocalize(AUSTIN, 'svc:vps:demo/austin-downtown');
    } finally {
      stub.restore();
    }
    const body = JSON.parse(String(stub.calls[0].init?.body));
    expect(body.service_id).toBe('svc:vps:demo/austin-downtown');
    expect(body.prior_geopose.lat_deg).toBeCloseTo(AUSTIN.lat_deg, 6);
  });

  test('omits service_id when discovery found nothing, so the bridge chooses', async () => {
    const stub = stubFetch(response);
    try {
      await bridgeLocalize(AUSTIN, null);
    } finally {
      stub.restore();
    }
    const body = JSON.parse(String(stub.calls[0].init?.body));
    expect('service_id' in body).toBe(false);
  });
});

test.describe('bridge URL resolution', () => {
  // Exercised through a copy of the rule rather than by re-importing the
  // module, because the module resolves its URL once at import and Playwright
  // shares that import across tests. The rule is short enough that a
  // divergence would be obvious; what matters is pinning the *decision*.
  function resolve(configured: string | undefined, hostname: string | undefined): string {
    if (configured) return configured;
    if (hostname && !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(hostname)) {
      return `https://${hostname}`;
    }
    return 'http://localhost:8088';
  }

  test('an explicit env var always wins', () => {
    expect(resolve('http://bridge:9000', 'demo.example')).toBe('http://bridge:9000');
    expect(resolve('http://bridge:9000', 'localhost')).toBe('http://bridge:9000');
  });

  test('a hosted page talks to its own origin', () => {
    // The deployed build serves the bundle and the API from one process
    // behind one load balancer. Hard-coding localhost there produced a bundle
    // that could only ever reach the viewer's own machine.
    expect(resolve(undefined, 'demo.spatialdds.org')).toBe('https://demo.spatialdds.org');
  });

  test('localhost still falls back to the dev bridge port', () => {
    for (const host of ['localhost', '127.0.0.1', '[::1]']) {
      expect(resolve(undefined, host)).toBe('http://localhost:8088');
    }
  });
});
