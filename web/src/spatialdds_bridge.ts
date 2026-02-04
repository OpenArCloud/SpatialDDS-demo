import type { CatalogItem, DiscoverResponse, GeoPose, LocalizeResponse } from './types';

const DEFAULT_BRIDGE_URL = 'http://localhost:8088';
const BRIDGE_URL = (import.meta as ImportMeta & { env: Record<string, string | undefined> }).env
  .VITE_SPATIALDDS_BRIDGE_URL || DEFAULT_BRIDGE_URL;

export type BridgeStatus = {
  ok: boolean;
  message: string;
  dds_domain?: number;
  announce?: Record<string, unknown> | null;
};

type DdsLocalizeResponse = {
  request_id?: string;
  service_id?: string;
  node_geo?: { geopose?: GeoPose };
  quality?: { success?: boolean; confidence?: number; rmse_m?: number };
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
  const dds = payload as DdsLocalizeResponse;
  const geopose = dds.node_geo?.geopose ?? prior;
  return {
    request_id: dds.request_id || 'request-unknown',
    service_id: dds.service_id || 'service-unknown',
    geopose,
    quality: {
      success: dds.quality?.success ?? true,
      confidence: dds.quality?.confidence ?? 0.0,
      rmse_m: dds.quality?.rmse_m ?? 0.0
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

function catalogEntryToItem(entry: Record<string, any>): CatalogItem {
  const coverage = Array.isArray(entry.coverage) ? entry.coverage : [];
  const bbox = coverage.find((item) => Array.isArray(item.bbox) && item.bbox.length >= 4)?.bbox;
  const lon = bbox ? (bbox[0] + bbox[2]) / 2 : -122.4194;
  const lat = bbox ? (bbox[1] + bbox[3]) / 2 : 37.7749;
  const nowMs = Date.now();
  const geopose: GeoPose = {
    lat_deg: lat,
    lon_deg: lon,
    alt_m: 5,
    q_xyzw: [0, 0, 0, 1],
    frame_kind: 'ENU',
    frame_ref: entry.frame_ref || { uuid: 'unknown', fqn: 'map/unknown' },
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
