import type { CatalogItem, DiscoverResponse, FrameRef, GeoPose, LocalizeResponse, TimeStamp } from './types';

const FRAME_REF: FrameRef = {
  uuid: 'f30b4e64-6b58-4d45-8da8-2a0f5b3b6a01',
  fqn: 'earth.enu'
};

const FIXED_STAMP: TimeStamp = {
  sec: 1700000000,
  nanosec: 0
};

const BASE_GEOPOSE: GeoPose = {
  lat_deg: 30.2847,
  lon_deg: -97.739475,
  alt_m: 18,
  q_xyzw: [0, 0, 0, 1],
  frame_kind: 'ENU',
  frame_ref: FRAME_REF,
  stamp: FIXED_STAMP,
  cov: 'COV_NONE'
};

const METERS_TO_LAT = 1e-5;
const METERS_TO_LON = 1e-5 * Math.cos((BASE_GEOPOSE.lat_deg * Math.PI) / 180);

function offsetGeoPose(base: GeoPose, north_m: number, east_m: number, alt_m = base.alt_m): GeoPose {
  return {
    ...base,
    lat_deg: base.lat_deg + north_m * METERS_TO_LAT,
    lon_deg: base.lon_deg + east_m * METERS_TO_LON,
    alt_m,
    stamp: FIXED_STAMP
  };
}

export async function mockLocalize(): Promise<LocalizeResponse> {
  return {
    request_id: 'req-localize-001',
    service_id: 'mock-vps-01',
    geopose: BASE_GEOPOSE,
    quality: {
      success: true,
      confidence: 0.91,
      rmse_m: 0.45
    }
  };
}

export async function mockDiscover(geopose: GeoPose): Promise<DiscoverResponse> {
  const items: CatalogItem[] = [
    {
      id: 'overlay-001',
      name: 'Congress Ave Overlay',
      kind: 'overlay',
      geopose: offsetGeoPose(geopose, 3, 1)
    },
    {
      id: 'poi-001',
      name: 'Capitol POI',
      kind: 'poi',
      geopose: offsetGeoPose(geopose, -3, -2)
    },
    {
      id: 'overlay-002',
      name: 'Lady Bird Lake',
      kind: 'overlay',
      geopose: offsetGeoPose(geopose, 4, -3)
    },
    {
      id: 'poi-002',
      name: 'Downtown Plaza',
      kind: 'poi',
      geopose: offsetGeoPose(geopose, -3, 4)
    }
  ];

  return {
    query_id: 'query-001',
    items
  };
}
