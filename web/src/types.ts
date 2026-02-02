export type FrameRef = {
  uuid: string;
  fqn: string;
};

export type TimeStamp = {
  sec: number;
  nanosec: number;
};

export type GeoPose = {
  lat_deg: number;
  lon_deg: number;
  alt_m: number;
  q_xyzw: [number, number, number, number];
  frame_kind: 'ENU';
  frame_ref: FrameRef;
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
    rmse_m: number;
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
