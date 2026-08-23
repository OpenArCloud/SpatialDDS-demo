import type { CatalogItem, DiscoverResponse, GeoPose, LocalizeResponse } from './types';

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

async function fetchJson(path: string, options?: RequestInit) {
  const response = await fetch(`${BRIDGE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers || {})
    }
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`bridge ${response.status}: ${detail}`);
  }
  return response.json();
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

export async function bridgeLocalize(prior: GeoPose): Promise<LocalizeResponse> {
  const payload = await fetchJson('/v1/localize', {
    method: 'POST',
    body: JSON.stringify({ prior_geopose: prior })
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
