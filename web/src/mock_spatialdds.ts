import type { CatalogItem, DiscoverResponse, GeoPose, LocalizeResponse, TimeStamp } from './types';

const FIXED_STAMP: TimeStamp = {
  sec: 1700000000,
  nanosec: 0
};

const BASE_GEOPOSE: GeoPose = {
  lat_deg: 30.284996,
  lon_deg: -97.739494,
  alt_m: 18,
  q: [0.4967, -0.0336, -0.0585, 0.8653],
  stamp: FIXED_STAMP,
  cov: 'COV_NONE'
};



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
