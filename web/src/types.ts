export type FrameRef = {
  uuid: string;
  fqn: string;
};

export type TimeStamp = {
  sec: number;
  nanosec: number;
};

// core::GeoPose. 1.7 removed frame_kind and frame_ref: the quaternion is
// fixed to the local ENU tangent frame at (lat_deg, lon_deg, alt_m), so
// there is no frame left to declare. `sec` is int64 on the wire — JS Numbers
// stay exact past year 285,000,000, so no BigInt and no 2^31 bound here.
export type GeoPose = {
  lat_deg: number;
  lon_deg: number;
  alt_m: number;
  q: [number, number, number, number];
  stamp: TimeStamp;
  cov: 'COV_NONE';
};

export type LocalizeRequest = {
  request_id: string;
  prior_geopose?: GeoPose;
};

export type LocalizeResponse = {
  request_id: string;
  service_id: string;
  geopose: GeoPose;
  quality: {
    success: boolean;
    confidence: number;
    /** null when the service did not report one — VpsResponse.has_rmse_m
     *  is false. Not the same as a perfect 0.00 m fit. */
    rmse_m: number | null;
  };
};

export type CatalogItem = {
  id: string;
  name: string;
  kind: 'overlay' | 'poi' | 'model';
  geopose: GeoPose;
  icon?: string;
  model_url?: string;
};

export type DiscoverResponse = {
  query_id: string;
  items: CatalogItem[];
};
