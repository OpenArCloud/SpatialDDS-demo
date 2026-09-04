import type { CatalogItem, DiscoverResponse, GeoPose, LocalizeResponse } from './types';
import { geohashEncode } from './geohash';

const DEV_BRIDGE_URL = 'http://localhost:8088';

// `import.meta.env` is Vite's, so it is absent when this module is imported
// outside a bundle — which the parser tests do, to exercise the parsers
// without standing up a browser and a bridge.
const VITE_ENV = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;

/**
 * Where the bridge is, in the three places this app runs.
 *
 * 1. An explicit `VITE_SPATIALDDS_BRIDGE_URL` always wins. That is what
 *    `web/.env.local` sets for local development, where Vite serves the page
 *    on :5173 and the bridge is a separate process on :8088.
 * 2. Otherwise, when the page is served from anywhere but localhost, the
 *    bridge is assumed to be the same origin — which is true of the deployed
 *    build, where one process serves both the bundle and the API behind one
 *    load balancer. Hard-coding localhost there produced a bundle that could
 *    only ever talk to the viewer's own machine.
 * 3. Falling back to localhost keeps a bundle opened straight off disk, or
 *    served by any dev server without the env var, working as it always did.
 */
function resolveBridgeUrl(): string {
  const configured = VITE_ENV?.VITE_SPATIALDDS_BRIDGE_URL;
  if (configured) {
    return configured;
  }
  const origin = typeof window !== 'undefined' ? window.location : undefined;
  if (origin && origin.hostname && !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(origin.hostname)) {
    return origin.origin;
  }
  return DEV_BRIDGE_URL;
}

/**
 * The one resolved bridge URL. Exported so nothing else re-derives it: the
 * DDS overlay had its own copy of the old `ENV || localhost` rule, so when
 * this one learned to use the page origin the overlay kept dialling
 * localhost and reported "ws: error" on a working deployment.
 */
export const BRIDGE_URL = resolveBridgeUrl();

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
  // Frames come from the same bus: a row's pose is meaningless until the frame
  // it names resolves. Only asked for when something in the results actually
  // carries a pose — a catalogue of unplaced content needs no frames, and a
  // bridge older than `/v1/frames` should not be sent a request it will 404,
  // which the browser logs as a console error whatever the client does with
  // the rejection. A failure here is not fatal: content falls back to its
  // coverage centre rather than disappearing.
  const needsFrames = (dds.results || []).some(
    (entry: Record<string, any>) => entry?.has_pose && entry?.frame_ref?.fqn);
  let frames: FrameMap = {};
  if (needsFrames) {
    try {
      frames = ((await fetchJson('/v1/frames')) as { frames?: FrameMap }).frames || {};
    } catch {
      frames = {};
    }
  }
  const items = (dds.results || []).map((entry) => catalogEntryToItem(entry, frames));

  // The catalogue rows themselves, keyed by content_id. The model layer
  // resolves `catalog:<content_id>` against these -- a lookup over results
  // the client already has, because the catalogue cannot be queried by id.
  const assets: Record<string, { uri?: string; hash?: string }> = {};
  for (const entry of dds.results || []) {
    const id = (entry as Record<string, any>).content_id;
    const asset = (entry as Record<string, any>).has_asset
      ? (entry as Record<string, any>).asset : null;
    if (id) {
      assets[id] = { uri: asset?.uri || (entry as Record<string, any>).href,
                     hash: asset?.hash };
    }
  }

  return {
    query_id: dds.query_id || 'query-unknown',
    items,
    assets,
    frames
  };
}

/** A `spatial::disco::Transform` as the bridge serves it from `/v1/frames`. */
export type FrameTransform = {
  from?: { fqn?: string };
  to?: { fqn?: string };
  pose?: { t?: number[]; q?: number[] };
};

export type FrameMap = Record<string, FrameTransform>;

/** Hamilton product, [x, y, z, w] throughout (GeoPose order). */
function quatMultiply(a: number[], b: number[]): [number, number, number, number] {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz
  ];
}

function rotate(q: number[], v: number[]): [number, number, number] {
  const [x, y, z, w] = q;
  const R = [
    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]
  ];
  return [0, 1, 2].map((i) => R[i][0] * v[0] + R[i][1] * v[1] + R[i][2] * v[2]) as
    [number, number, number];
}

/** ECEF metres to geodetic degrees + ellipsoidal height, WGS84 (Bowring). */
function ecefToGeodetic(x: number, y: number, z: number) {
  const a = 6378137.0;
  const f = 1 / 298.257223563;
  const e2 = f * (2 - f);
  const lon = Math.atan2(y, x);
  const p = Math.hypot(x, y);
  let lat = Math.atan2(z, p * (1 - e2));
  let height = 0;
  for (let i = 0; i < 8; i += 1) {
    const n = a / Math.sqrt(1 - e2 * Math.sin(lat) ** 2);
    height = p / Math.cos(lat) - n;
    lat = Math.atan2(z, p * (1 - (e2 * n) / (n + height)));
  }
  return {
    lat_deg: (lat * 180) / Math.PI,
    lon_deg: (lon * 180) / Math.PI,
    alt_m: height
  };
}

/**
 * A pose in a local frame, carried into earth-fixed by that frame's transform.
 *
 * Shared by the catalogue path and the model path deliberately: both are
 * saying the same thing — a pose in a named frame — and if the two resolved it
 * separately they could disagree about where the same duck is, which is
 * exactly the class of bug this layer exists to remove.
 */
export function resolveInFrame(
  frame: FrameTransform | undefined,
  local: { t?: number[]; q?: number[] } | null
): { placed: { lat_deg: number; lon_deg: number; alt_m: number };
     orientation: [number, number, number, number] } | null {
  if (!frame || !local || !Array.isArray(local.t)) {
    return null;
  }
  const ft = frame.pose?.t;
  const fq = frame.pose?.q;
  if (!Array.isArray(ft) || !Array.isArray(fq)) {
    return null;
  }
  const [ex, ey, ez] = rotate(fq, local.t);
  const q = Array.isArray(local.q) ? local.q : [0, 0, 0, 1];
  return {
    placed: ecefToGeodetic(ex + ft[0], ey + ft[1], ez + ft[2]),
    // The content's orientation within its frame, carried into earth-fixed by
    // the frame's own rotation.
    orientation: quatMultiply(fq, q)
  };
}

/**
 * The world model layer — demo-local `oarc_model`, non-normative.
 *
 * The catalogue says what content exists: one duck.glb, one checksum, one
 * URI. The model says what is *there* — entities with identity, each pointing
 * back at a catalogue row when it has an asset. Three ducks, one glb.
 */
export type ModelEntity = {
  entity_id: string;
  properties?: { key: string; value: string }[];
  external_refs?: { key: string; value: string }[];
  state_reason?: string;
  source_id?: string;
  stamp?: { sec: number; nanosec: number };
  extent?: { min_xyz?: number[]; max_xyz?: number[] };
  basis?: string;
  layer?: string;
  type_uris?: string[];
  frame_ref?: { fqn?: string };
  has_pose?: boolean;
  pose?: { t?: number[]; q?: number[] };
  has_extent?: boolean;
  content_refs?: string[];
  state?: string;
};

export type ModelSnapshot = {
  entities: ModelEntity[];
  relationships: Record<string, any>[];
};

/**
 * Type URI to a human label.
 *
 * A table, not a function, and exported as one: a consumer that wants to know
 * whether a type is known asks the map and gets undefined, so an unrecognised
 * URI is a lookup miss rather than a branch someone has to remember to write.
 * That matters because the interesting case is the *miss* -- a publisher we
 * have never met, using a vocabulary we do not carry, must still render.
 *
 * The layer mints no types, so every key here is borrowed. Entries are added
 * when the demo publishes something that uses them; this is a convenience for
 * display, not an authority on what the URIs mean, and a client that has
 * never heard of a type is not thereby wrong about the world.
 */
export const TYPE_LABELS: Readonly<Record<string, string>> = Object.freeze({
  'http://www.wikidata.org/entity/Q483453': 'Fountain',
  'http://www.wikidata.org/entity/Q851478': 'Rubber duck'
});

/**
 * The label for a type URI, or undefined when it is not one we know.
 *
 * Undefined is a legitimate answer and callers are expected to handle it --
 * see the degraded rendering an unknown type gets.
 */
export function typeLabel(uri: string): string | undefined {
  return TYPE_LABELS[uri];
}

/** A namespaced property, or undefined. */
export function modelProperty(entity: ModelEntity, key: string): string | undefined {
  return (entity.properties || []).find((kv) => kv.key === key)?.value;
}

/** `catalog:<content_id>` is the Part 1 scheme; anything else is ignored. */
export function catalogRefId(entity: ModelEntity): string | null {
  for (const ref of entity.content_refs || []) {
    if (ref.startsWith('catalog:')) {
      return ref.slice('catalog:'.length);
    }
  }
  return null;
}

/**
 * Whether a model layer is present at all.
 *
 * Returns an empty model rather than throwing when the bridge predates the
 * endpoint or nothing is publishing — "no model" and "no model layer" lead to
 * the same client behaviour, which is to fall back to catalogue placement.
 */
export async function bridgeModelSnapshot(): Promise<ModelSnapshot> {
  try {
    const payload = await fetchJson('/v1/model') as Partial<ModelSnapshot>;
    return {
      entities: Array.isArray(payload.entities) ? payload.entities : [],
      relationships: Array.isArray(payload.relationships) ? payload.relationships : []
    };
  } catch {
    return { entities: [], relationships: [] };
  }
}

/**
 * A model entity as something the renderer can draw.
 *
 * `assetFor` resolves `catalog:<content_id>` against catalogue rows the client
 * already has. It has to be a lookup over cached results rather than a query,
 * because the catalogue filters on coverage and kind and cannot be asked for a
 * row by id — see the gap noted in SPEC_COMPLIANCE. Returns null for an entity
 * whose frame does not resolve: better to draw nothing than to draw it at the
 * centre of the earth.
 */
/** The entity's extent as a placed box, or undefined if it declares none. */
function extentOf(entity: ModelEntity, frames: FrameMap) {
  if (!entity.has_extent || !entity.extent?.min_xyz || !entity.extent?.max_xyz) {
    return undefined;
  }
  const min = entity.extent.min_xyz;
  const max = entity.extent.max_xyz;
  const size: [number, number, number] = [max[0] - min[0], max[1] - min[1], max[2] - min[2]];
  if (size.some((v) => !(v > 0))) {
    return undefined;
  }
  const centre = [0, 1, 2].map((i) => (min[i] + max[i]) / 2);
  const placed = resolveInFrame(frames[entity.frame_ref?.fqn || ''],
                                { t: centre, q: [0, 0, 0, 1] });
  if (!placed) {
    return undefined;
  }
  return { ...placed.placed, size, orientation: placed.orientation };
}

/**
 * The best honest name for an entity.
 *
 * Ordered by how much the publisher actually told us, and stopping at the
 * first thing that is true. A URI tail is a poor name and an accurate one: it
 * is what this publisher said the thing is, in a vocabulary nobody here can
 * resolve, which is exactly the situation worth showing rather than hiding.
 */
export function displayName(entity: ModelEntity): string {
  const label = modelProperty(entity, 'demo.label');
  if (label) {
    return label;
  }
  for (const uri of entity.type_uris || []) {
    const known = typeLabel(uri);
    if (known) {
      return known;
    }
  }
  const uri = (entity.type_uris || [])[0];
  if (uri) {
    // "…/vocab/garden-gnome" -> "garden-gnome". Trailing slashes and empty
    // segments are dropped so a tidy URI and an untidy one read the same.
    const tail = uri.split(/[/#]/).filter(Boolean).pop();
    if (tail) {
      return tail;
    }
  }
  return entity.entity_id.split(':').filter(Boolean).pop() || entity.entity_id;
}

/** True when nothing in `type_uris` is a vocabulary this client carries. */
export function hasUnknownType(entity: ModelEntity): boolean {
  const uris = entity.type_uris || [];
  return uris.length > 0 && uris.every((uri) => typeLabel(uri) === undefined);
}

/**
 * The four ways a claim can be arrived at, from `oarc_model`'s Basis enum.
 *
 * Listed in full rather than as the two this deployment happens to publish.
 * A filter that only knows the values it has seen is not a filter, it is a
 * hardcoded demo -- and DECLARED and DERIVED are exactly the ones a second
 * publisher would show up with.
 */
export const BASIS_VALUES: readonly string[] =
  Object.freeze(['OBSERVED', 'DECLARED', 'AUTHORED', 'DERIVED']);

export type BasisFilter = {
  /** null means no filter: everything renders. */
  wanted: ReadonlySet<string> | null;
  /** Values asked for that are not in the enum, kept so the UI can say so. */
  unrecognised: string[];
};

export const NO_BASIS_FILTER: BasisFilter =
  Object.freeze({ wanted: null, unrecognised: [] });

/**
 * `?basis=observed` or `?basis=observed,declared`. Case-insensitive.
 *
 * An unrecognised value does not hide the world. Falling back to showing
 * everything and saying loudly that the value was not understood is the
 * lesser wrong: a typo that silently empties the scene reads as "there is
 * nothing here", which is a claim about the world rather than about the URL.
 * The caller is expected to surface `unrecognised` rather than swallow it.
 */
export function parseBasisFilter(raw: string | null | undefined): BasisFilter {
  if (raw === null || raw === undefined || raw.trim() === '') {
    return NO_BASIS_FILTER;
  }
  const wanted = new Set<string>();
  const unrecognised: string[] = [];
  for (const part of raw.split(',')) {
    const value = part.trim().toUpperCase();
    if (value === '') {
      continue;
    }
    if (BASIS_VALUES.includes(value)) {
      wanted.add(value);
    } else {
      unrecognised.push(part.trim());
    }
  }
  if (wanted.size === 0) {
    return { wanted: null, unrecognised };
  }
  return { wanted, unrecognised };
}

/**
 * Whether an entity survives the filter.
 *
 * An entity that does not state its basis is excluded while a filter is
 * active, and the caller reports how many. Including it would put something
 * on screen the filter cannot vouch for; the alternative -- dropping it
 * silently -- loses it without saying so.
 */
export function matchesBasis(entity: ModelEntity, filter: BasisFilter): boolean {
  if (!filter.wanted) {
    return true;
  }
  return typeof entity.basis === 'string' && filter.wanted.has(entity.basis);
}

/**
 * What to draw, given a model, a catalogue and a view filter.
 *
 * This exists as one function rather than a few lines in the render path
 * because the order of the two steps is load-bearing and easy to get wrong in
 * the obvious way. Suppression is decided from *every* entity, and only then
 * is the filter applied. Reversing that hands the ducks back to the catalogue
 * the moment `?basis=observed` hides the model's ducks, and they reappear at
 * the catalogue pose -- a filter that puts a duck on screen. Hiding a claim
 * must not restore the claim it superseded.
 */
export type RenderPlan = {
  /** Catalogue content_ids the model has taken responsibility for. */
  claimed: ReadonlySet<string>;
  /** Catalogue rows still drawing themselves. */
  fromCatalog: CatalogItem[];
  /** Entities the filter admits. */
  visible: ModelEntity[];
  /** How many entities the filter removed. */
  hidden: number;
  /** Entities excluded for having no stated basis, so the caller can say so. */
  unstated: ModelEntity[];
};

export function planModelRender(
  entities: ModelEntity[],
  catalogItems: CatalogItem[],
  filter: BasisFilter
): RenderPlan {
  // Step one, from every entity. Not from the survivors -- see above.
  const claimed = new Set<string>();
  for (const entity of entities) {
    const ref = catalogRefId(entity);
    if (ref) {
      claimed.add(ref);
    }
  }
  const fromCatalog = catalogItems.filter((item) => !claimed.has(item.id));

  // Step two.
  const visible = entities.filter((e) => matchesBasis(e, filter));
  const unstated = filter.wanted
    ? entities.filter((e) => typeof e.basis !== 'string')
    : [];
  return {
    claimed, fromCatalog, visible,
    hidden: entities.length - visible.length, unstated
  };
}

export function modelEntityToItem(
  entity: ModelEntity,
  frames: FrameMap,
  assetFor: (contentId: string) => { uri?: string; hash?: string } | undefined
): CatalogItem | null {
  const resolved = resolveInFrame(
    frames[entity.frame_ref?.fqn || ''], entity.has_pose ? entity.pose || null : null);
  if (!resolved) {
    return null;
  }
  const contentId = catalogRefId(entity);
  const asset = contentId ? assetFor(contentId) : undefined;
  const nowMs = Date.now();
  return {
    id: entity.entity_id,
    // What to call it, in descending order of how much the publisher told us.
    //
    // A stranger's entity has no reason to speak this demo's property
    // conventions, so the label may be absent; it may also use a vocabulary
    // this client cannot resolve. Each step down says less, and none of them
    // invents anything: the publisher's own name, then the type if we happen
    // to know it, then the tail of the type URI -- which at least reports what
    // the publisher claimed -- then the tail of the id.
    name: displayName(entity),
    kind: asset?.uri ? 'model' : 'poi',
    geopose: {
      lat_deg: resolved.placed.lat_deg,
      lon_deg: resolved.placed.lon_deg,
      alt_m: resolved.placed.alt_m,
      q: [0, 0, 0, 1],
      stamp: { sec: Math.floor(nowMs / 1000), nanosec: (nowMs % 1000) * 1_000_000 },
      cov: 'COV_NONE'
    },
    orientation: resolved.orientation,
    // The declared extent, resolved so a renderer can draw the volume the
    // model actually claims instead of a placeholder. Its centre is derived
    // from min/max rather than assumed to be the pose: an extent need not be
    // centred on the thing it bounds.
    extent: extentOf(entity, frames),
    model_url: asset?.uri,
    asset_hash: asset?.hash,
    // Carried so the renderer can show what the model actually says about
    // this thing, rather than a description the client made up.
    entity
  };
}

export function catalogEntryToItem(
  entry: Record<string, any>,
  frames: FrameMap = {}
): CatalogItem {
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

  // Placement.
  //
  // Coverage answers "would I find this here?" — it is a search key, not a
  // position, and the bbox centre above is only a stand-in for content that
  // does not say where it sits. A row that carries `pose` says exactly where,
  // inside the frame it names, and the frame resolves through the transform
  // its service announced (see /v1/frames). That chain is what makes altitude
  // and orientation real data rather than renderer defaults.
  //
  // An earlier attempt put the altitude in the coverage `aabb`, which is a
  // metres-in-the-declared-frame field: writing degrees into it was wrong in
  // proportion to distance from null island, and wrong silently.
  const resolved = resolveInFrame(
    frames[entry.frame_ref?.fqn || ''], entry.has_pose ? entry.pose : null);
  const placed = resolved?.placed ?? null;
  const orientation = resolved?.orientation;

  const nowMs = Date.now();
  const geopose: GeoPose = {
    lat_deg: placed ? placed.lat_deg : lat,
    lon_deg: placed ? placed.lon_deg : lon,
    // 5 m is the fallback for content that neither places itself nor names a
    // resolvable frame: a nominal height, not a claim about the ground.
    alt_m: placed ? placed.alt_m : 5,
    q: [0, 0, 0, 1],
    stamp: { sec: Math.floor(nowMs / 1000), nanosec: (nowMs % 1000) * 1_000_000 },
    cov: 'COV_NONE'
  };

  // `asset.uri` is absolute and carries a hash; `href` is the older relative
  // form, kept for consumers that already read it.
  const asset = entry.has_asset ? entry.asset : null;

  return {
    id: entry.content_id || entry.id || 'item-unknown',
    name: entry.name || entry.content_id || 'SpatialDDS Item',
    kind: entry.kind || 'model',
    geopose,
    orientation,
    model_url: asset?.uri || entry.href,
    asset_hash: asset?.hash || undefined
  };
}
