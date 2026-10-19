import type { CatalogItem, DiscoverResponse, GeoPose, LocalizeResponse } from './types';



/**
 * Looking north, level and upright, as a body->ENU quaternion.
 *
 * **Not a yaw.** `orientationFromGeoPose` reads forward from +X and up from
 * *-Y*, measured from what the localizer actually returns. A quarter turn
 * about Z -- which is what `START_Q` held, described in its own comment as
 * "a quarter turn about up, so the camera looks north ... level" -- does give
 * forward=north, but it carries -Y onto the horizon, so up points *east* and
 * the camera arrives rolled 90 degrees. That roll is what made this visible
 * on screen.
 *
 * This quaternion is the one that satisfies both axes: +X -> north,
 * -Y -> up. Checked numerically against the same arithmetic the page applies.
 */
export const LOOKING_NORTH_UPRIGHT: [number, number, number, number] =
  [-0.5, -0.5, 0.5, 0.5];

/**
 * A localization that did not happen, and says where it put you.
 *
 * Takes the position rather than inventing one. It used to return
 * `BASE_GEOPOSE` -- 30.284996, -97.739494, about 148 m north of the venue,
 * carrying `[0.4967, -0.0336, -0.0585, 0.8653]`, which is bit-for-bit the
 * ENU->ECEF rotation for that latitude and longitude: the frame's own basis
 * mistaken for a pose in it, reading as a camera pitched 60 degrees out of
 * level. So the mock dropped you in the wrong place, facing the wrong way.
 *
 * Given the demo's own start position it now drops you exactly there, upright
 * and looking north along the Main Mall.
 */
export async function mockLocalize(at: GeoPose): Promise<LocalizeResponse> {
  return {
    request_id: 'req-localize-001',
    service_id: 'mock-vps-01',
    geopose: { ...at, q: LOOKING_NORTH_UPRIGHT },
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
