import type { CatalogItem, DiscoverResponse, GeoPose, LocalizeResponse } from './types';
import { geohashEncode } from './geohash';

const DEFAULT_BRIDGE_URL = 'http://localhost:8088';
// `import.meta.env` is Vite's, so it is absent when this module is imported
// outside a bundle — which the parser tests do, to exercise the parsers
// without standing up a browser and a bridge.
const VITE_ENV = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
const BRIDGE_URL = VITE_ENV?.VITE_SPATIALDDS_BRIDGE_URL || DEFAULT_BRIDGE_URL;

export type BridgeStatus = {
  ok: boolean;
  message: string;
  dds_domain?: number;
  announce?: Record<string, unknown> | null;
};

// spatial::argeo::VpsResponse, as the bridge serialises it. The shape
// changed with the 1.7 batch-3 types: the demo-local LocalizeQuality struct
// became a VpsStatus enum with confidence and rmse alongside it, and the
// correlation id is query_id rather than request_id.
//
// Every field the old parser read is gone, and because each read was
// `?? <default>` the UI degraded silently — always "success", always 0.00
// confidence, always 0.00 rmse, even on a VPS failure.
type DdsLocalizeResponse = {
  query_id?: string;
  service_id?: string;
  status?: string;                    // VpsStatus identifier, e.g. VPS_SUCCESS
  has_node_geo?: boolean;
  node_geo?: { has_geopose?: boolean; geopose?: GeoPose };
  confidence?: number;
  has_rmse_m?: boolean;
  rmse_m?: number;
};

type DdsCatalogResponse = {
  query_id?: string;
  results?: Array<Record<string, any>>;
};

/** One HTTP exchange with the bridge, for the REST overlay. */
export type RestExchange = {
  method: string;
  path: string;
  status: number;
  ms: number;
  request?: unknown;
  response?: unknown;
  error?: string;
};

type RestObserver = (exchange: RestExchange) => void;
let restObserver: RestObserver | null = null;

/**
 * Watch every REST call this module makes.
 *
 * Registered here rather than at each call site because `fetchJson` is the
 * one place all of them pass through — so an endpoint added later is observed
 * without anyone remembering to instrument it.
 */
export function observeRest(observer: RestObserver | null): void {
  restObserver = observer;
}

async function fetchJson(path: string, options?: RequestInit) {
  const started = Date.now();
  const method = options?.method || 'GET';
  let requestBody: unknown;
  if (typeof options?.body === 'string') {
    try {
      requestBody = JSON.parse(options.body);
    } catch {
      requestBody = options.body;
    }
  }
  const report = (status: number, response?: unknown, error?: string) => {
    restObserver?.({
      method, path, status, ms: Date.now() - started,
      request: requestBody, response, error
    });
  };

  let response: Response;
  try {
    response = await fetch(`${BRIDGE_URL}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options?.headers || {})
      }
    });
  } catch (networkError) {
    report(0, undefined, String(networkError));
    throw networkError;
  }
  if (!response.ok) {
    const detail = await response.text();
    report(response.status, undefined, detail);
    throw new Error(`bridge ${response.status}: ${detail}`);
  }
  const body = await response.json();
  report(response.status, body);
  return body;
}

export async function bridgeHealth(): Promise<BridgeStatus> {
  try {
    const payload = await fetchJson('/health');
    return {
      ok: true,
      message: 'bridge online',
      dds_domain: payload.dds_domain,
      announce: payload.announce ?? null
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : 'bridge offline'
    };
  }
}

/** One `results[]` entry of the §3.3.0 search response: a §8.2.3 service manifest. */
type ServiceManifest = {
  id?: string;
  service?: { service_id?: string; kind?: string; name?: string };
};

/**
 * Ask the discovery binding which service covers a position.
 *
 * This is the spec's GET convenience form — `?geohash=` — which exists for
 * exactly this case: a client that has a position and needs to find out who
 * serves it. Same call `scripts/cold_start.sh` makes with curl.
 *
 * Returns null rather than throwing when nothing is found or the endpoint is
 * unavailable: discovery is how the client *prefers* to choose a service, not
 * a precondition for localizing. Without it the bridge falls back to the
 * announce it last saw, which is what happened before this existed.
 */
export async function bridgeFindService(
  position: GeoPose,
  kind: string = 'VPS'
): Promise<string | null> {
  try {
    const cell = geohashEncode(position.lat_deg, position.lon_deg);
    const payload = await fetchJson(
      `/.well-known/spatialdds/search?geohash=${encodeURIComponent(cell)}` +
        `&kind=${encodeURIComponent(kind)}`
    );
    const results = (payload as { results?: ServiceManifest[] }).results || [];
    // The demo deployment announces one VPS. Taking the first result keeps
    // that honest without pretending to a selection policy we do not have;
    // results are ordered by service_id, so the choice is at least stable.
    return results[0]?.service?.service_id || null;
  } catch {
    return null;
  }
}

export async function bridgeLocalize(
  prior: GeoPose,
  serviceId?: string | null,
  queryImage?: string | null
): Promise<LocalizeResponse> {
  const body: Record<string, unknown> = { prior_geopose: prior };
  if (queryImage) {
    // base64 JPEG. Inline here because HTTP has no side channel; the bridge
    // chunks it onto the blob lane, which is where §3.2 requires the bytes to
    // ride once they reach the bus.
    body.query_image = queryImage;
  }
  if (serviceId) {
    // Names the service the client discovered. Absent, the bridge picks
    // whichever announce arrived most recently.
    body.service_id = serviceId;
  }
  const payload = await fetchJson('/v1/localize', {
    method: 'POST',
    body: JSON.stringify(body)
  });
  return parseLocalizeResponse(payload as DdsLocalizeResponse, prior);
}

// Exported so a fixture test can pin it to a captured bridge response
// without needing a bridge, a bus, or a browser.
export function parseLocalizeResponse(
  dds: DdsLocalizeResponse,
  prior: GeoPose
): LocalizeResponse {
  // Presence flags, not nullability: node_geo and geopose are always on the
  // wire and the has_* boolean is what says whether to read them.
  const located = dds.has_node_geo !== false && dds.node_geo?.has_geopose !== false;
  const geopose = (located && dds.node_geo?.geopose) || prior;

  return {
    request_id: dds.query_id || 'request-unknown',
    service_id: dds.service_id || 'service-unknown',
    geopose,
    quality: {
      // VpsStatus is an enum identifier (§2.8). Anything that is not an
      // explicit success is a failure — an unknown status must not read as
      // one, which is what the old `?? true` did.
      success: dds.status === 'VPS_SUCCESS',
      confidence: dds.confidence ?? 0.0,
      // rmse is presence-flagged: absent means "not reported", which is not
      // the same as a perfect 0.00 m fit, so it is null rather than 0.
      rmse_m: dds.has_rmse_m ? (dds.rmse_m ?? 0.0) : null
    }
  };
}

export async function bridgeDiscover(geopose: GeoPose): Promise<DiscoverResponse> {
  const payload = await fetchJson('/v1/catalog/query', {
    method: 'POST',
    body: JSON.stringify({ geopose })
  });
  const dds = payload as DdsCatalogResponse;
  const items = (dds.results || []).map((entry) => catalogEntryToItem(entry));
  return {
    query_id: dds.query_id || 'query-unknown',
    items
  };
}

export function catalogEntryToItem(entry: Record<string, any>): CatalogItem {
  const coverage = Array.isArray(entry.coverage) ? entry.coverage : [];
  // `has_bbox` decides, not the presence of the key. Every CoverageElement
  // carries a bbox member whatever the flag says, so testing the array alone
  // matched [0, 0, 0, 0] and placed the item off Africa instead of falling
  // back to the default.
  const bbox = coverage.find(
    (item) => item.has_bbox && Array.isArray(item.bbox) && item.bbox.length >= 4
  )?.bbox;
  const lon = bbox ? (bbox[0] + bbox[2]) / 2 : -122.4194;
  const lat = bbox ? (bbox[1] + bbox[3]) / 2 : 37.7749;
  const nowMs = Date.now();
  const geopose: GeoPose = {
    lat_deg: lat,
    lon_deg: lon,
    alt_m: 5,
    q: [0, 0, 0, 1],
    stamp: { sec: Math.floor(nowMs / 1000), nanosec: (nowMs % 1000) * 1_000_000 },
    cov: 'COV_NONE'
  };

  return {
    id: entry.content_id || entry.id || 'item-unknown',
    name: entry.name || entry.content_id || 'SpatialDDS Item',
    kind: entry.kind || 'model',
    geopose,
    model_url: entry.href
  };
}
