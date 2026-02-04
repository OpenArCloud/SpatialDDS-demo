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
  lat_deg: 30.284996,
  lon_deg: -97.739494,
  alt_m: 18,
  q_xyzw: [0.4967, -0.0336, -0.0585, 0.8653],
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
      id: '5f8b2f2a-7c2b-4f15-9b68-8a9a7c5f7e01',
      name: 'POI-001',
      kind: 'poi',
      geopose: {
        ...geopose,
        lat_deg: 30.285201,
        lon_deg: -97.73939
      }
    },
    {
      id: '3c1a0fd2-2e4b-4c0e-9b12-6d2c3c1b7e02',
      name: 'POI-002',
      kind: 'poi',
      geopose: {
        ...geopose,
        lat_deg: 30.285223,
        lon_deg: -97.739542
      }
    }
  ];

  return {
    query_id: 'query-001',
    items
  };
}
