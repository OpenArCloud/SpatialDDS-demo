import * as Cesium from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import {
  loadQueryFrame,
  loadQueryFrameManifest,
  queryFrameUrl,
  type QueryFrameManifest
} from './query_frames';
import { mockDiscover, mockLocalize } from './mock_spatialdds';
import {
  BRIDGE_URL, bridgeDiscover, bridgeFindService, bridgeHealth, bridgeLocalize,
  BASIS_VALUES, bridgeFrames, bridgeModelSnapshot, catalogRefId, disambiguate,
  displayName, matchesBasis, modelEntityToItem, observeRest, parseBasisFilter,
  planModelRender, resolveContentIds, resolveInFrame, typeLabel
} from './spatialdds_bridge';
import type { ModelEntity, RestExchange } from './spatialdds_bridge';
import type { CatalogItem, GeoPose } from './types';

const readoutEl = document.getElementById('readout') as HTMLPreElement | null;
const localizeBtn = document.getElementById('btnLocalize') as HTMLButtonElement | null;
const localizeImageBtn = document.getElementById('btnLocalizeImage') as HTMLButtonElement | null;
const framePick = document.getElementById('framePick') as HTMLSpanElement | null;
const frameSelect = document.getElementById('frameSelect') as HTMLSelectElement | null;
const framePreview = document.getElementById('framePreview') as HTMLImageElement | null;
const discoverBtn = document.getElementById('btnDiscover') as HTMLButtonElement | null;
const toggleTilesBtn = document.getElementById('btnToggleTiles') as HTMLButtonElement | null;
const toggleDdsOverlayBtn = document.getElementById('btnToggleDdsOverlay') as HTMLButtonElement | null;
const toggleRestOverlayBtn = document.getElementById('btnToggleRestOverlay') as HTMLButtonElement | null;
const clearBtn = document.getElementById('btnClear') as HTMLButtonElement | null;
const modeBadgeEl = document.getElementById('modeBadge') as HTMLSpanElement | null;
const ddsOverlayEl = document.getElementById('ddsOverlay') as HTMLDivElement | null;
const ddsOverlayBodyEl = document.getElementById('ddsOverlayBody') as HTMLPreElement | null;

const markerSvg = encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
     <circle cx="16" cy="16" r="10" fill="#4dd0e1" stroke="#0f172a" stroke-width="2"/>
   </svg>`
);
const markerUrl = `data:image/svg+xml,${markerSvg}`;

const itemSvg = encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
     <rect x="6" y="6" width="20" height="20" rx="6" fill="#f97316" stroke="#1f2937" stroke-width="2"/>
   </svg>`
);
const itemUrl = `data:image/svg+xml,${itemSvg}`;

let viewer: Cesium.Viewer | null = null;
let currentPose: GeoPose | null = null;
const entityIds = new Set<string>();
const appLogs: string[] = [];
let frameCursor = 0;
/** The installed query-frame bundle, or null when none is installed. */
let queryFrames: QueryFrameManifest | null = null;
let readoutItems = 0;
let readoutMessage = 'ready';
let bridgeActive = false;
// The VPS this client discovered, cached for the session. Null until the first
// localize, and null again if discovery found nothing — in which case the
// bridge chooses, exactly as it did before discovery existed.
let vpsServiceId: string | null = null;
// Likewise for the content catalogue. Informational rather than routing:
// `CatalogQuery` names a reply topic, not a target service, so a client that
// has discovered a catalogue still asks on the well-known query topic. What
// discovery buys here is knowing a catalogue covers this position at all.
let contentServiceId: string | null = null;
let photorealisticTileset: Cesium.Cesium3DTileset | null = null;
let photorealisticEnabled = false;
const GEOPOSE_QUAT_IS_ENU_TO_BODY = false;
let ddsOverlayVisible = false;
let restOverlayVisible = false;
const restMessages: string[] = [];
let ddsSocket: WebSocket | null = null;

// --- world model layer (demo-local oarc_model) -------------------------------
// Present only when something is publishing; see handleDiscover.
let modelSocket: WebSocket | null = null;
const modelItems = new Map<string, CatalogItem>();
// The frames and catalogue rows the last discovery resolved against, kept so a
// live entity update can be placed without re-querying anything.
// The model as last read, kept so the catalogue can ask which rows it has
// already claimed without re-fetching. `null` means "not loaded yet", which
// is different from "loaded and empty" and drives the retry in handleDiscover.
let lastModel: { entities: ModelEntity[]; relationships: unknown[] } | null = null;
const tempoAnnounced = new Set<string>();
let modelPlaced = 0;
let catalogPlaced = 0;
let lastFrames: Record<string, any> = {};
let lastAssets: Record<string, { uri?: string; hash?: string }> = {};
const ddsMessages: string[] = [];

const START_LON = -97.7396265;
// About 20 m south of the duck, on the plaza, looking north up the Main Mall:
// far enough back that the pool's near rim does not hide what is floating in
// it, close enough that the fountain fills the view.
const START_LAT = 30.283660;
const EYE_HEIGHT_M = 1.7;
const START_HEIGHT_M = 20_000_000.0;
const START_VIEW_HEADING_DEG = 0.0;
const START_VIEW_PITCH_DEG = -90.0;
// A body->ENU quaternion (ROS REP-103: x-forward, y-left, z-up): a quarter
// turn about up, so the camera looks north along the Main Mall, level.
//
// This used to hold [0.4967, -0.0336, -0.0585, 0.8653], which is not a body
// orientation at all -- it is bit-for-bit the ENU->ECEF rotation for this
// latitude and longitude, i.e. the frame's own basis mistaken for a pose in
// it. Read as body->ENU it rolls the camera onto its side. Only the mock
// prior uses this; the real VPS path takes its prior from the query-frame
// manifest and is unaffected.
const START_Q: [number, number, number, number] =
  [0, 0, 0.7071067811865476, 0.7071067811865476];

const ENV = (import.meta as ImportMeta & { env: Record<string, string | undefined> }).env;
const HAS_ION_TOKEN = Boolean(ENV.VITE_CESIUM_ION_TOKEN);
const PHOTOREAL_ASSET_ID = ENV.VITE_CESIUM_ION_ASSET_ID
  ? Number(ENV.VITE_CESIUM_ION_ASSET_ID)
  : undefined;
// Imported, not re-derived — see spatialdds_bridge.ts.

function appLog(message: string) {
  appLogs.push(message);
  (window as Window & { __appLogs?: string[] }).__appLogs = appLogs;
  console.log(message);
}

/**
 * Height of the rendered surface under a point, ellipsoidal.
 *
 * Falls back to 0 — the ellipsoid — which is exactly right when nothing else
 * is loaded, and is what a viewer with no Ion token sees.
 */
async function surfaceHeightAt(lon: number, lat: number): Promise<number> {
  if (!viewer) {
    return 0;
  }
  try {
    const hits = await viewer.scene.clampToHeightMostDetailed(
      [Cesium.Cartesian3.fromDegrees(lon, lat, 0)]);
    const hit = hits[0];
    if (!Cesium.defined(hit)) {
      return 0;
    }
    const height = Cesium.Cartographic.fromCartesian(hit).height;
    return Number.isFinite(height) ? height : 0;
  } catch {
    return 0;
  }
}

async function seedPriorGeopose(): Promise<GeoPose> {
  const nowMs = Date.now();
  // Altitude is sampled, not hardcoded.
  //
  // `alt_m` is ellipsoidal, and a literal cannot be right for every viewer:
  // with the photorealistic tiles loaded the plaza here is near 144 m, on the
  // bare ellipsoid it is 0, and the two differ by more than the whole scene.
  // A fixed number put the camera either underground or a hundred metres
  // above the map, depending on what had loaded. Asking the scene what is
  // under the start point is right in both cases, and in any third one.
  const alt = (await surfaceHeightAt(START_LON, START_LAT)) + EYE_HEIGHT_M;
  return {
    lat_deg: START_LAT,
    lon_deg: START_LON,
    alt_m: alt,
    q: START_Q,
    stamp: { sec: Math.floor(nowMs / 1000), nanosec: (nowMs % 1000) * 1_000_000 },
    cov: 'COV_NONE'
  };
}

function orientationFromGeoPose(geopose: GeoPose) {
  const qRaw = new Cesium.Quaternion(
    geopose.q[0],
    geopose.q[1],
    geopose.q[2],
    geopose.q[3]
  );
  const qBodyToEnu = GEOPOSE_QUAT_IS_ENU_TO_BODY
    ? Cesium.Quaternion.inverse(qRaw, new Cesium.Quaternion())
    : qRaw;

  const rBodyToEnu = Cesium.Matrix3.fromQuaternion(qBodyToEnu, new Cesium.Matrix3());
  const xBodyInEnu = Cesium.Matrix3.multiplyByVector(
    rBodyToEnu,
    new Cesium.Cartesian3(1, 0, 0),
    new Cesium.Cartesian3()
  );
  const yBodyInEnu = Cesium.Matrix3.multiplyByVector(
    rBodyToEnu,
    new Cesium.Cartesian3(0, 1, 0),
    new Cesium.Cartesian3()
  );
  const originEcef = Cesium.Cartesian3.fromDegrees(
    geopose.lon_deg,
    geopose.lat_deg,
    geopose.alt_m
  );
  const enuToEcef = Cesium.Transforms.eastNorthUpToFixedFrame(originEcef);
  const rEnuToEcef = Cesium.Matrix4.getMatrix3(enuToEcef, new Cesium.Matrix3());

  // Body axes, measured from what the localizer actually returns rather than
  // assumed: over four real poses, +X sits at +1.4 to +7.7 degrees elevation
  // (level, as a hand-held phone is), +Y at -82 to -89 (straight down) and +Z
  // within a couple of degrees of horizontal. So forward is +X and up is -Y.
  //
  // Note this disagrees with OpenVPS's own comment, which describes the output
  // as robotics convention — X forward, Y left, Z up. X forward matches; Y and
  // Z do not. The axes above are what the wire carries.
  //
  // The previous code took +Y as forward, which was right for the mock VPS's
  // synthetic quaternions and points the camera at the ground for real ones.
  const direction = Cesium.Matrix3.multiplyByVector(
    rEnuToEcef,
    xBodyInEnu,
    new Cesium.Cartesian3()
  );
  const up = Cesium.Matrix3.multiplyByVector(
    rEnuToEcef,
    Cesium.Cartesian3.negate(yBodyInEnu, new Cesium.Cartesian3()),
    new Cesium.Cartesian3()
  );
  Cesium.Cartesian3.normalize(direction, direction);
  Cesium.Cartesian3.normalize(up, up);
  return { direction, up };
}

function formatGeoPose(geopose: GeoPose | null): string {
  if (!geopose) {
    return 'pose: none';
  }
  const q = geopose.q.map((value) => value.toFixed(4)).join(', ');
  return `GeoPose: lat=${geopose.lat_deg.toFixed(6)} lon=${geopose.lon_deg.toFixed(6)} alt=${geopose.alt_m.toFixed(2)}m\nq: [${q}]`;
}

function setModeBadge(mode: 'bridge' | 'mock', detail: string) {
  if (!modeBadgeEl) {
    return;
  }
  modeBadgeEl.textContent = mode === 'bridge' ? 'DDS Bridge' : 'Mock Mode';
  modeBadgeEl.dataset.mode = mode;
  if (detail) {
    modeBadgeEl.title = detail;
  }
}

function renderReadout(geopose: GeoPose | null) {
  if (!readoutEl) {
    return;
  }
  const messageLine = readoutMessage ? `\nmessage: ${readoutMessage}` : '';
  // The pose is no longer printed. It was the widest thing in the panel by a
  // wide margin — a full lat/lon/alt line plus a quaternion — and it alone
  // pushed the toolbar to roughly twice the width the controls need. It stays
  // on the element as data, where a test or a console poke can still read it.
  readoutEl.dataset.geopose = formatGeoPose(geopose);
  readoutEl.textContent = `items: ${readoutItems}${messageLine}`;
}

function cameraGeoPose(activeViewer: Cesium.Viewer): GeoPose {
  const cartographic = Cesium.Cartographic.fromCartesian(activeViewer.camera.position);
  const headingPitchRoll = new Cesium.HeadingPitchRoll(
    activeViewer.camera.heading,
    activeViewer.camera.pitch,
    activeViewer.camera.roll
  );
  const orientation = Cesium.Transforms.headingPitchRollQuaternion(activeViewer.camera.position, headingPitchRoll);
  const nowMs = Date.now();
  return {
    lat_deg: Cesium.Math.toDegrees(cartographic.latitude),
    lon_deg: Cesium.Math.toDegrees(cartographic.longitude),
    alt_m: cartographic.height,
    q: [orientation.x, orientation.y, orientation.z, orientation.w],
    stamp: { sec: Math.floor(nowMs / 1000), nanosec: (nowMs % 1000) * 1_000_000 },
    cov: 'COV_NONE'
  };
}

/**
 * A pin for an item, or just its name.
 *
 * `labelOnly` is for content that draws itself. A glTF model with a billboard
 * pinned to the same position wears its own icon: the orange square sits over
 * the duck and hides the thing it is pointing at. The label still earns its
 * place -- three ducks that look identical need telling apart.
 */
// A plate is 15 px of bold text with 5 px of padding above and below, so it
// occupies about 25 px. Rungs closer than that overlap, which the first
// attempt at 19 px demonstrated: Bobbin and Waddles were legible only
// separately. Two pixels of air is enough to read them as distinct.
const LABEL_RUNG_PX = 27;

/**
 * Which rung of the label stack an entity sits on: 0, 1 or 2.
 *
 * A stable hash of the id, so a name keeps its height for as long as the
 * thing exists. Three rungs because that is what the venue needs when the
 * pond shrinks; a fourth thing in the same spot would still collide, and the
 * honest answer then is a wider camera rather than a taller stack.
 */
function labelRung(id: string): number {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % 3;
}

function addMarker(id: string, name: string, geopose: GeoPose, imageUrl: string,
                   labelOnly = false, clampToGround = true, isArea = false) {
  if (!viewer) {
    return;
  }
  const entity = viewer.entities.add({
    id,
    position: Cesium.Cartesian3.fromDegrees(geopose.lon_deg, geopose.lat_deg, geopose.alt_m),
    billboard: labelOnly ? undefined : {
      image: imageUrl,
      verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
      heightReference: clampToGround
        ? Cesium.HeightReference.CLAMP_TO_GROUND
        : Cesium.HeightReference.NONE,
      // Draw over the scene rather than inside it. Depth-tested, the fountain
      // pin sat within the fountain's own geometry: invisible, and therefore
      // unclickable -- the info panel it opens had no reachable target left
      // once the extent stopped being a solid.
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
      height: 32,
      width: 32
    },
    label: {
      text: name,
      // A filled plate, not outlined text. Outlined white over photorealistic
      // tiles is legible against the sky and nothing else; over water and
      // stonework it disappears into whatever is behind it.
      font: 'bold 15px system-ui, -apple-system, "Segoe UI", sans-serif',
      fillColor: Cesium.Color.WHITE,
      showBackground: true,
      backgroundColor: Cesium.Color.fromCssColorString('rgba(10, 16, 24, 0.78)'),
      backgroundPadding: new Cesium.Cartesian2(8, 5),
      style: Cesium.LabelStyle.FILL,
      verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
      // Clear of whatever is below it, and clear of its neighbours.
      //
      // A pin is 32 px tall, but a model draws itself at minimumPixelSize 64,
      // and the offset tuned for the pin left the name across the duck's
      // back. The second term is the harder problem: things in a world get
      // close together. Shrinking the pond puts three ducks and the pond's
      // own label within a few metres, and four names at one screen point is
      // one unreadable smear.
      //
      // The stagger is deterministic per id, not per index -- an index would
      // reshuffle every time an entity arrived or left, so the names would
      // dance while the things stood still. Same id, same rung, always.
      // Cesium can declutter a LabelCollection, but that hides names rather
      // than placing them, and a demo whose point is "these are the things
      // and this is what they are called" should not hide the names.
      // An area's name floats clear of everything inside it. Rungs separate
      // the points from each other; this separates the container from its
      // contents, which no hash can do reliably because they are always in
      // the same place by definition.
      pixelOffset: new Cesium.Cartesian2(
        0, (labelOnly ? -44 : -36) - labelRung(id) * LABEL_RUNG_PX
           - (isArea ? 2.5 * LABEL_RUNG_PX : 0)),
      heightReference: clampToGround
        ? Cesium.HeightReference.CLAMP_TO_GROUND
        : Cesium.HeightReference.NONE,
      disableDepthTestDistance: Number.POSITIVE_INFINITY
    }
  });

  entityIds.add(entity.id as string);
}

function clearEntities() {
  if (!viewer) {
    return;
  }
  entityIds.forEach((id) => viewer?.entities.removeById(id));
  entityIds.clear();
}

/**
 * Find the VPS covering a position, once per session.
 *
 * Discovery runs from the prior rather than at startup, because the prior is
 * the position the client is actually asking about — and it is the only
 * position it has before a fix. That ordering is the cold start: find who
 * serves here, then ask them where you are.
 */
async function ensureVpsService(prior: GeoPose): Promise<string | null> {
  if (vpsServiceId) {
    return vpsServiceId;
  }
  vpsServiceId = await bridgeFindService(prior);
  appLog(
    vpsServiceId
      ? `discover:vps ${vpsServiceId}`
      : 'discover:vps none — bridge will choose'
  );
  return vpsServiceId;
}

/**
 * The frame the VPS is asked to localize.
 *
 * A browser has no camera, so the demo sends what it is looking at: the
 * rendered Cesium view, as a real JPEG. That is not a photograph and a real
 * VPS would not match against it — but it is real bytes of a realistic size,
 * which is what makes the request honest. Until this existed the request
 * referenced a blob containing the ASCII string `MOCK_IMAGE_3`, and nothing
 * ever published even that.
 *
 * Returns null if the canvas cannot be read, in which case the bridge falls
 * back to its placeholder and localization still works.
 */
function captureQueryImage(): string | null {
  if (!viewer) {
    return null;
  }
  try {
    viewer.render();
    const dataUrl = viewer.canvas.toDataURL('image/jpeg', 0.7);
    const comma = dataUrl.indexOf(',');
    return comma > 0 ? dataUrl.slice(comma + 1) : null;
  } catch (error) {
    appLog(`capture: failed (${String(error)})`);
    return null;
  }
}

/**
 * One localization at a time.
 *
 * A second request started while the first is in flight gives two answers to
 * "where am I", each followed by its own `clearEntities()` and model
 * bootstrap, so whichever finishes last wipes the other's work. It also
 * queues behind the bridge's request lock, which is how it first showed up:
 * a test clicked Localize while the page was already auto-localizing, and
 * the catalogue query behind it timed out.
 *
 * A person can do the same thing by double-clicking. The guard belongs in the
 * app rather than in the harness.
 */
let localizeInFlight = false;

async function withLocalizeLock(work: () => Promise<void>) {
  if (localizeInFlight) {
    appLog('localize: already in flight — ignoring');
    return;
  }
  localizeInFlight = true;
  if (localizeBtn) localizeBtn.disabled = true;
  if (localizeImageBtn) localizeImageBtn.disabled = true;
  try {
    await work();
  } finally {
    localizeInFlight = false;
    if (localizeBtn) localizeBtn.disabled = false;
    // Restored to what the mode decided, not unconditionally: without a
    // query-frame bundle this button was disabled for a reason.
    if (localizeImageBtn) localizeImageBtn.disabled = !bridgeActive || queryFrames === null;
  }
}

async function handleLocalize() {
  await withLocalizeLock(() => localizeOnce());
}

async function localizeOnce() {
  const prior = await seedPriorGeopose();
  const queryImage = bridgeActive ? captureQueryImage() : null;
  if (queryImage) {
    appLog(`capture: ${Math.round((queryImage.length * 3) / 4 / 1024)} KB query frame`);
  }
  await localizeWith(prior, queryImage);
}

/**
 * Localize using a real photograph from the scan a VPS map was built from.
 *
 * The prior comes from the installed bundle's map anchor rather than the demo's
 * start position. That is not cosmetic: discovery is a geohash search around
 * the prior, and the map is wherever it was scanned, so the demo's own start
 * position finds no VPS covering it. The cached service id is cleared for the
 * same reason — the service that answers downtown is not the one holding it.
 */
/** Show what is about to be sent — a filename is not a photograph. */
function showFramePreview() {
  if (!framePreview || !frameSelect) return;
  framePreview.src = queryFrameUrl(frameSelect.value);
}

async function handleLocalizeWithImage() {
  if (!queryFrames) {
    appLog('frame: no query-frame bundle installed (see ar_demo/README.md)');
    return;
  }
  await withLocalizeLock(async () => {
    // The chosen frame, or the next one round if there is no picker.
    const file = frameSelect?.value
      || queryFrames!.frames[frameCursor++ % queryFrames!.frames.length];

    let queryImage: string;
    try {
      queryImage = await loadQueryFrame(file);
    } catch (error) {
      appLog(`frame: load failed (${String(error)})`);
      return;
    }
    appLog(
      `frame: ${file} ${Math.round((queryImage.length * 3) / 4 / 1024)} KB` +
      (queryFrames!.label ? ` (${queryFrames!.label})` : '')
    );

    vpsServiceId = null;
    await localizeWith(priorFromManifest(queryFrames!), queryImage);
  });
}

/** The prior a bundle implies: its map's anchor, level and facing north. */
function priorFromManifest(manifest: QueryFrameManifest): GeoPose {
  const nowMs = Date.now();
  return {
    lat_deg: manifest.anchor.lat_deg,
    lon_deg: manifest.anchor.lon_deg,
    alt_m: manifest.anchor.alt_m ?? 0,
    q: [0, 0, 0, 1],
    stamp: { sec: Math.floor(nowMs / 1000), nanosec: (nowMs % 1000) * 1_000_000 },
    cov: 'COV_NONE'
  };
}

/** Everything both localize paths share once the request is decided. */
async function localizeWith(prior: GeoPose, queryImage: string | null) {
  const serviceId = bridgeActive ? await ensureVpsService(prior) : null;
  const response = bridgeActive
    ? await bridgeLocalize(prior, serviceId, queryImage)
    : await mockLocalize();
  currentPose = response.geopose;
  clearEntities();
  addMarker('user-location', 'You are here', response.geopose, markerUrl);

  appLog(`localize:success ${response.geopose.lat_deg.toFixed(5)},${response.geopose.lon_deg.toFixed(5)}`);

  // Knowing where we are is the only precondition the model had. Waiting for
  // a second click to ask for it was never a decision, just where the code
  // happened to live.
  modelPlaced = 0;
  catalogPlaced = 0;
  lastModel = null;
  void bootstrapModel();

  if (viewer) {
    try {
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(
          response.geopose.lon_deg,
          response.geopose.lat_deg,
          response.geopose.alt_m + 2
        ),
        orientation: orientationFromGeoPose(response.geopose),
        duration: 1.0
      });
    } catch (error) {
      console.warn('camera:flyTo failed', error);
    }
  }

  readoutItems = 0;
  readoutMessage = '';
  renderReadout(viewer ? cameraGeoPose(viewer) : response.geopose);
}

async function ensureContentService(position: GeoPose): Promise<string | null> {
  if (contentServiceId) {
    return contentServiceId;
  }
  contentServiceId = await bridgeFindService(position, 'CONTENT');
  appLog(
    contentServiceId
      ? `discover:content ${contentServiceId}`
      : 'discover:content none — querying the well-known topic anyway'
  );
  return contentServiceId;
}

/**
 * Load the world model and start following it.
 *
 * This used to live inside `handleDiscover`, which meant the model only
 * appeared after someone pressed "Discover Content". A person opening the
 * page saw an empty venue and had no way to know a button stood between them
 * and it -- and the headless tests pressed the same button, so nothing ever
 * reported the gap. The two actions are different questions and are now
 * separate: *what is here* is the model, and it loads as soon as the client
 * knows where it is; *what content is published for this area* is the
 * catalogue, and that is still Discover.
 *
 * Everything it needs it fetches itself. Frames come from `/v1/frames`, and
 * `catalog:` references resolve by id -- which is why P2.5's `content_id_in`
 * is load-bearing here rather than only under a test flag: without it this
 * function could not resolve an asset without first running a coverage query.
 */
async function bootstrapModel(): Promise<void> {
  if (!bridgeActive || !currentPose) {
    return;
  }
  // `?catalogpose=1` forces the legacy path for side-by-side comparison: no
  // model, and the catalogue places content from its own pose.
  if (new URLSearchParams(location.search).has('catalogpose')) {
    lastModel = { entities: [], relationships: [] };
    return;
  }

  const model = await bridgeModelSnapshot();
  lastModel = model;
  if (!model.entities.length) {
    // No model layer publishing is not an error; it is an empty world, and
    // the catalogue path below behaves exactly as it did before it existed.
    return;
  }

  const frames = model.entities.some((e) => e.has_pose) ? await bridgeFrames() : {};
  const assets: Record<string, { uri?: string; hash?: string }> = {};

  // References the model carries. There is no coverage query in front of this
  // any more, so every one of them is resolved by id -- the path the demo
  // could not exercise at all before the bootstrap moved ahead of it.
  const references = model.entities
    .map((entity) => catalogRefId(entity))
    .filter((id): id is string => !!id);
  const wanted = [...new Set(references)];
  if (wanted.length) {
    const found = await resolveContentIds(wanted);
    Object.assign(assets, found);
    const missing = wanted.filter((id) => !found[id]);
    // References and ids counted separately, because they are not the same
    // number and "asked for 3, resolved 1" invites the reading that two
    // failed. Three ducks share one row: the asset-versus-instance split the
    // layer exists to express, showing up in a log line.
    appLog(`catalog:by-id ${references.length} reference(s) over ` +
           `${wanted.length} id(s); resolved ${Object.keys(found).length}` +
           (missing.length ? `, unresolved ${missing.join(', ')}` : ''));
  }

  lastFrames = frames;
  lastAssets = assets;

  const basisFilter = parseBasisFilter(
    new URLSearchParams(location.search).get('basis'));
  const plan = planModelRender(model.entities, [], basisFilter);
  if (basisFilter.unrecognised.length) {
    appLog(`model:basis unrecognised ${basisFilter.unrecognised.join(',')} — ` +
           `showing all; known values ${BASIS_VALUES.join(', ')}`);
  }
  for (const entity of plan.unstated) {
    appLog(`model:basis-unstated ${entity.entity_id} — hidden by the filter`);
  }

  // Two services can describe one thing and give it the same name. Qualify
  // the collisions before drawing, so the map says which claim is which.
  const names = disambiguate(plan.visible);
  modelPlaced = 0;
  for (const entity of plan.visible) {
    const item = modelEntityToItem(entity, frames, (id) => assets[id]);
    if (!item) {
      appLog(`model:unresolved ${entity.entity_id} — frame ${entity.frame_ref?.fqn}`);
      continue;
    }
    const qualified = names.get(entity.entity_id);
    if (qualified && qualified !== item.name) {
      appLog(`model:name ${entity.entity_id} shown as ${qualified} — ` +
             `another entity claims the same name`);
      item.name = qualified;
    }
    modelItems.set(entity.entity_id, item);
    addItemEntity(item);
    modelPlaced += 1;
  }

  // A filtered scene must not read as the whole world. "items: 1" with no
  // further comment is a claim about the venue; this makes it a claim about
  // the view.
  readoutMessage = (basisFilter.wanted && plan.hidden)
    ? `basis=${[...basisFilter.wanted].join(',')} — ${plan.hidden} hidden`
    : '';
  refreshItemCount();
  appLog(`model:loaded ${modelPlaced} entities, ${model.relationships.length} ` +
         `relationships` +
         (plan.hidden ? `; ${plan.hidden} hidden by basis filter` : ''));
  connectModelStream();
}

/** The readout counts both sources, because the user sees one scene. */
function refreshItemCount() {
  readoutItems = modelPlaced + catalogPlaced;
  renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
}

/**
 * The catalogue: what content is published for where we are standing.
 *
 * Model placement is not done here any more -- see `bootstrapModel`. What is
 * still done here is *suppression*: a catalogue row the model has taken
 * responsibility for must not draw itself a second time, and that decision
 * needs both lists, so it stays in `planModelRender` where its ordering is
 * fixed and tested.
 */
async function handleDiscover() {
  if (!currentPose) {
    catalogPlaced = 0;
    readoutMessage = 'localize first';
    refreshItemCount();
    return;
  }

  if (bridgeActive) {
    await ensureContentService(currentPose);
  }
  const response = bridgeActive ? await bridgeDiscover(currentPose) : await mockDiscover(currentPose);

  // If the model never loaded -- bootstrap raced the bridge coming up, or the
  // page was opened before localization -- take the chance now rather than
  // letting the catalogue draw ducks the model has already claimed.
  if (lastModel === null) {
    await bootstrapModel();
  }
  const model = lastModel || { entities: [], relationships: [] };

  const basisFilter = parseBasisFilter(
    new URLSearchParams(location.search).get('basis'));
  const plan = planModelRender(model.entities, response.items, basisFilter);
  plan.fromCatalog.forEach((item) => addItemEntity(item));
  catalogPlaced = plan.fromCatalog.length;

  // Frames and rows the model layer may not have needed. Merged rather than
  // replaced: the by-id lookups the bootstrap already did are still valid.
  lastFrames = { ...(response.frames || {}), ...lastFrames };
  lastAssets = { ...(response.assets || {}), ...lastAssets };

  refreshItemCount();
  if (plan.claimed.size) {
    appLog(`discover:superseded ${plan.claimed.size} catalog row(s) the model claims`);
  }
  appLog(`discover:items ${catalogPlaced} from the catalogue`);
}

/**
 * Live model updates.
 *
 * The snapshot is a starting point, not the state: an entity republished with
 * a new pose has to move what is already on screen. Latching means the two
 * agree -- whatever a fresh client would be handed is what a connected client
 * has been kept at.
 */
function connectModelStream() {
  if (modelSocket || !bridgeActive) {
    return;
  }
  const wsUrl = `${wsUrlFromBridgeUrl(BRIDGE_URL)}/ws`;
  try {
    modelSocket = new WebSocket(wsUrl);
  } catch (error) {
    appLog(`model:ws failed (${String(error)})`);
    modelSocket = null;
    return;
  }
  modelSocket.onopen = () => {
    modelSocket?.send(JSON.stringify({
      type: 'subscribe', id: 'model', pattern: 'spatialdds/model/*'
    }));
    appLog('model:ws watching spatialdds/model/*');
  };
  modelSocket.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data as string) as {
        type?: string; msg_type?: string; payload?: ModelEntity;
      };
      if (msg.type !== 'data') {
        return;
      }
      // The entity is gone. The bridge sends this under its own type because
      // a dispose carries no sample; see the bridge's _ModelPump.
      if (msg.msg_type === 'oarc.model_entity_disposed') {
        const goneId = (msg.payload as { entity_id?: string } | undefined)?.entity_id;
        if (goneId) {
          const removed = removeModelEntity(goneId);
          if (removed) {
            modelPlaced = Math.max(0, modelPlaced - 1);
          }
          refreshItemCount();
          appLog(`model:disposed ${goneId} — removed from the scene`);
        }
        return;
      }
      // The tempo lane: a bare pose for something already on screen. It
      // carries no identity, type or extent, because none of those changed --
      // which is the whole reason it exists as a separate message.
      if (msg.msg_type === 'oarc.model_pose') {
        const fast = msg.payload as unknown as
          { entity_id?: string; pose?: { t: number[]; q: number[] } };
        if (fast?.entity_id && fast.pose) {
          applyFastPose(fast.entity_id, fast.pose);
        }
        return;
      }
      if (msg.msg_type !== 'oarc.model_entity') {
        return;
      }
      const entity = msg.payload;
      if (!entity?.entity_id) {
        return;
      }

      // A tombstone: the last thing the entity says, and the only place the
      // reason is carried. It is logged before the dispose that follows,
      // because after the dispose there is nothing left to ask.
      if (entity.state === 'RETIRED') {
        appLog(`model:retired ${entity.entity_id} — ` +
               `${entity.state_reason || 'no reason given'}`);
        const marker = viewer?.entities.getById(entity.entity_id);
        if (marker?.label) {
          // Say it on the map too, for the beat before it disappears.
          marker.label.text = new Cesium.ConstantProperty(
            `${displayName(entity)} — retired`);
        }
        return;
      }
      // The filter applies to updates too, and says so rather than relying
      // on `known` being absent for a hidden entity. That happens to be true
      // today -- hidden entities were never drawn, so there is nothing to
      // move -- but it is an accident of the guard below, and a filter whose
      // enforcement is accidental leaks the first time the guard changes.
      // Re-read from the URL rather than caching: it cannot go stale.
      const filter = parseBasisFilter(
        new URLSearchParams(location.search).get('basis'));
      if (!matchesBasis(entity, filter)) {
        return;
      }
      const known = modelItems.get(entity.entity_id);
      const item = modelEntityToItem(
        entity, lastFrames, (id) => lastAssets[id]);
      if (!item || !known) {
        return;
      }
      modelItems.set(entity.entity_id, item);
      moveItemEntity(item);
      appLog(`model:moved ${entity.entity_id} -> ` +
             `${item.geopose.lat_deg.toFixed(6)}, ${item.geopose.lon_deg.toFixed(6)}`);
    } catch {
      // A malformed frame is not worth tearing the socket down for.
    }
  };
  modelSocket.onclose = () => { modelSocket = null; };
}

/** Move an already-rendered entity rather than rebuilding it. */
function moveItemEntity(item: CatalogItem) {
  if (!viewer) {
    return;
  }
  const position = Cesium.Cartesian3.fromDegrees(
    item.geopose.lon_deg, item.geopose.lat_deg, item.geopose.alt_m);
  const model = viewer.entities.getById(`${item.id}-model`);
  if (model) {
    model.position = new Cesium.ConstantPositionProperty(position);
    if (item.orientation) {
      model.orientation = new Cesium.ConstantProperty(new Cesium.Quaternion(
        item.orientation[0], item.orientation[1],
        item.orientation[2], item.orientation[3]));
    }
  }
  const marker = viewer.entities.getById(item.id);
  if (marker) {
    marker.position = new Cesium.ConstantPositionProperty(position);
  }
}

/**
 * Move something already on screen, from a pose and nothing else.
 *
 * The snapshot placed it and said what it is; this says only where it is now.
 * A client that has not seen the entity ignores the pose rather than
 * inventing one from it -- a position with no identity, type or asset is not
 * enough to draw anything honestly, and the entity record will arrive on its
 * own schedule.
 *
 * Filtered like everything else: a duck hidden by `?basis=authored` does not
 * come back because it moved.
 */
function applyFastPose(entityId: string, pose: { t: number[]; q: number[] }) {
  const known = modelItems.get(entityId);
  const entity = known?.entity as ModelEntity | undefined;
  if (!known || !entity) {
    return;
  }
  const filter = parseBasisFilter(
    new URLSearchParams(location.search).get('basis'));
  if (!matchesBasis(entity, filter)) {
    return;
  }
  const fqn = entity.frame_ref?.fqn;
  const placed = fqn ? resolveInFrame(lastFrames[fqn], pose) : null;
  if (!placed) {
    return;
  }
  const nowMs = Date.now();
  const item: CatalogItem = {
    ...known,
    geopose: {
      ...placed.placed,
      // The pose lane carries an orientation, and it is applied to the model
      // below; the GeoPose's own q stays identity for the same reason the
      // catalogue path leaves it identity -- it describes the observer's
      // frame convention, not the thing's facing.
      q: [0, 0, 0, 1],
      stamp: { sec: Math.floor(nowMs / 1000), nanosec: (nowMs % 1000) * 1_000_000 },
      cov: 'COV_NONE'
    },
    orientation: placed.orientation
  };
  modelItems.set(entityId, item);
  moveItemEntity(item);

  // Said once per entity, not once per pose. At six a second a line each
  // would drown the panel and tell a reader nothing they could not see by
  // watching the duck; saying nothing at all would leave no trace that the
  // tempo lane is what is driving the motion. The DDS overlay carries the
  // traffic itself for anyone who wants it.
  if (!tempoAnnounced.has(entityId)) {
    tempoAnnounced.add(entityId);
    appLog(`model:tempo ${entityId} — following ${'spatialdds/model/pose/v1'}`);
  }
}

/**
 * Take an entity out of the scene, in every form it was drawn in.
 *
 * A model entity can be on screen as up to three Cesium entities -- marker,
 * glTF, extent -- and removing only the one whose id matches leaves a duck
 * floating with no name, or a name with no duck. Removing the whole set is
 * the only honest reading of "it is gone".
 */
function removeModelEntity(entityId: string): number {
  if (!viewer) {
    return 0;
  }
  let removed = 0;
  for (const suffix of ['', '-model', '-extent', '-box']) {
    const id = `${entityId}${suffix}`;
    if (viewer.entities.getById(id)) {
      viewer.entities.removeById(id);
      entityIds.delete(id);
      removed += 1;
    }
  }
  modelItems.delete(entityId);
  return removed;
}

/**
 * What the model says about a thing, as an info panel.
 *
 * Every line is read off the entity. Nothing here is authored by the client,
 * which is the point: if the panel is wrong the model is wrong, and there is
 * no second description to drift away from the first.
 */
function describeEntity(item: CatalogItem): string | undefined {
  const entity = item.entity;
  if (!entity) {
    return undefined;
  }
  const rows: [string, string][] = [];
  const note = (entity.properties || [])
    .find((kv: any) => kv.key === 'demo.note')?.value;
  for (const uri of entity.type_uris || []) {
    // A known type shows its label with the URI behind it; an unknown one
    // shows the URI, which is all anybody can honestly say about it.
    const label = typeLabel(uri);
    rows.push(['Type', label
      ? `${label} <a href="${uri}" target="_blank" rel="noopener" style="opacity:.65">${uri}</a>`
      : `<a href="${uri}" target="_blank" rel="noopener">${uri}</a>` +
        ` <span style="opacity:.6">— not a vocabulary this client carries</span>`]);
  }
  rows.push(['Basis', `${entity.basis} — how the claim was arrived at`]);
  rows.push(['Layer', `${entity.layer} — how fast it is expected to change`]);
  rows.push(['State', entity.state_reason
    ? `${entity.state} (${entity.state_reason})` : String(entity.state)]);
  rows.push(['Frame', `${entity.frame_ref?.fqn} (${entity.frame_ref?.uuid})`]);
  if (entity.has_pose && entity.pose?.t) {
    const [x, y, z] = entity.pose.t;
    rows.push(['Pose', `${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)} m in that frame`]);
  }
  if (entity.has_extent && entity.extent?.min_xyz && entity.extent?.max_xyz) {
    const size = [0, 1, 2].map(
      (i) => (entity.extent.max_xyz[i] - entity.extent.min_xyz[i]).toFixed(1));
    rows.push(['Extent', `${size[0]} × ${size[1]} × ${size[2]} m (bounding box)`]);
  }
  for (const ref of entity.content_refs || []) {
    rows.push(['Content', `${ref}${item.model_url ? ` → ${item.model_url}` : ''}`]);
  }
  if (item.asset_hash) {
    rows.push(['Integrity', item.asset_hash]);
  }
  // External references, linked where the namespace is one we know how to
  // resolve. An unknown namespace is shown as-is rather than guessed at: the
  // point of a reference is that it resolves, and a fabricated URL does not.
  const refUrl = (ns: string, id: string): string | null => {
    if (ns === 'wikidata') return `https://www.wikidata.org/wiki/${id}`;
    if (ns === 'osm') return `https://www.openstreetmap.org/${id}`;
    return null;
  };
  for (const kv of entity.external_refs || []) {
    const url = refUrl(kv.key, kv.value);
    rows.push([`Also known as`, url
      ? `<a href="${url}" target="_blank" rel="noopener">${kv.key}:${kv.value}</a>`
      : `${kv.key}:${kv.value}`]);
  }
  rows.push(['Published by', String(entity.source_id)]);
  if (entity.stamp) {
    rows.push(['Stamp', new Date(entity.stamp.sec * 1000).toISOString()]);
  }
  rows.push(['Entity id', `<code>${entity.entity_id}</code>`]);

  return `<div style="font:13px system-ui,sans-serif;line-height:1.5">
    ${note ? `<p style="margin:0 0 10px">${note}</p>` : ''}
    <table style="border-collapse:collapse">${rows.map(([k, v]) =>
      `<tr><td style="padding:2px 10px 2px 0;vertical-align:top;opacity:.65;
        white-space:nowrap">${k}</td><td style="padding:2px 0">${v}</td></tr>`).join('')}
    </table>
    <p style="margin:10px 0 0;opacity:.6">From the world model layer
      (<code>oarc_model</code>, demo-local) — every field above is read off the
      entity, not authored here.</p>
  </div>`;
}

/**
 * An extent's colour says how the claim was arrived at.
 *
 * Deliberately not a per-entity palette: the point of drawing two boxes over
 * one pond is that a viewer can see *why* they differ, and "the blue one is
 * the pond entity" would not survive a second opinion arriving.
 */
function extentColour(basis: string | undefined): Cesium.Color {
  switch (basis) {
    case 'OBSERVED':                            // measured; the tiles saw it
      return Cesium.Color.fromCssColorString('rgba(95, 196, 205, 0.9)');
    case 'DECLARED':                            // asserted by the venue
      return Cesium.Color.fromCssColorString('rgba(240, 196, 78, 0.9)');
    case 'DERIVED':                             // computed by somebody
      return Cesium.Color.fromCssColorString('rgba(197, 128, 240, 0.9)');
    default:                                    // AUTHORED, or unstated
      return Cesium.Color.fromCssColorString('rgba(200, 200, 200, 0.75)');
  }
}

function addItemEntity(item: CatalogItem) {
  const drawsItself = item.kind === 'model' && !!item.model_url
    && /\.glb($|\?)/i.test(item.model_url);

  // A thing with a declared extent gets its pin on top of that volume rather
  // than clamped to the ground under it. Clamped, the fountain's pin landed
  // inside the fountain -- invisible, and with the extent drawn as a
  // wireframe there was then nothing left to click to open its details. The
  // name should sit above the thing it names.
  const markerPose: GeoPose = item.extent
    ? { ...item.geopose,
        lat_deg: item.extent.lat_deg,
        lon_deg: item.extent.lon_deg,
        alt_m: item.extent.alt_m + item.extent.size[2] / 2 }
    : item.geopose;
  // Never clamped. The altitude here was resolved from a stated pose through
  // an announced frame, exactly like the glTF drawn below, and the two must
  // agree or the name floats away from the thing it names.
  //
  // Clamping was worse than merely redundant. This deployment carries no
  // terrain provider -- world terrain was removed because it drew a false
  // surface over the photorealistic tiles' water -- so CLAMP_TO_GROUND had
  // nothing to clamp to but the ellipsoid, roughly 143 m beneath Austin. The
  // markers looked right only in the window before the tiles finished loading
  // and the clamp resolved, then silently dropped underground: visible in
  // every quick screenshot and absent from every patient one, which is how it
  // survived this long.
  addMarker(item.id, item.name, markerPose, itemUrl, drawsItself, false,
            !!item.extent);
  if (!viewer) {
    return;
  }
  const description = describeEntity(item);
  if (description) {
    const marker = viewer.entities.getById(item.id);
    if (marker) {
      marker.description = new Cesium.ConstantProperty(description);
      marker.name = item.name;
    }
  }

  // Content that names a glTF asset is drawn as that asset. A row carrying an
  // AssetRef gives an absolute URI; the older relative `href` is resolved
  // against the page so the same seed works at the dev server's root and under
  // the deployed `/ar/` base.
  if (item.kind === 'model' && item.model_url && /\.glb($|\?)/i.test(item.model_url)) {
    const uri = /^(https?:)?\//i.test(item.model_url)
      ? item.model_url
      : `${import.meta.env.BASE_URL}${item.model_url}`;
    const modelEntity = viewer.entities.add({
      id: `${item.id}-model`,
      // Absolute height, resolved from the row's pose through the frame its
      // service announced. Clamping to the surface was the obvious alternative
      // and the wrong one: it snaps to the topmost thing under the point,
      // which over this plaza is the tree canopy, so the duck ended up in the
      // branches rather than on the water.
      position: Cesium.Cartesian3.fromDegrees(
        item.geopose.lon_deg, item.geopose.lat_deg, item.geopose.alt_m),
      // The publisher's orientation, in ECEF, when it stated one. Cesium
      // applies glTF's Y-up-to-Z-up itself, so an identity rotation points the
      // asset's authored forward (+Z) along the frame's -North.
      orientation: item.orientation
        ? new Cesium.Quaternion(
            item.orientation[0], item.orientation[1],
            item.orientation[2], item.orientation[3])
        : undefined,
      model: {
        uri,
        scale: 0.6,
        // Keeps it findable from across the mall; without this a duck a metre
        // long is a couple of pixels from the far end of the plaza.
        minimumPixelSize: 64,
        maximumScale: 4,
        heightReference: Cesium.HeightReference.NONE
      }
    });
    if (description) {
      modelEntity.description = new Cesium.ConstantProperty(description);
      modelEntity.name = item.name;
    }
    entityIds.add(modelEntity.id as string);
    appLog(`content: ${item.name} -> ${uri}` +
           (item.asset_hash ? ` (${item.asset_hash.slice(0, 14)}…)` : ''));
    return;
  }

  // An entity that declares an extent gets that extent drawn.
  //
  // The alternative was the placeholder below: a 1.2 m orange cube, which for
  // a fountain is both wrong and uninformative -- a solid block sitting in
  // the water, telling you nothing the label did not. The model already says
  // how big the thing is, so draw that: a translucent volume with its edges
  // picked out, which reads as a claim about extent rather than as an object.
  if (item.extent) {
    const [sx, sy, sz] = item.extent.size;
    const extentEntity = viewer.entities.add({
      id: `${item.id}-extent`,
      position: Cesium.Cartesian3.fromDegrees(
        item.extent.lon_deg, item.extent.lat_deg, item.extent.alt_m),
      orientation: new Cesium.Quaternion(
        item.extent.orientation[0], item.extent.orientation[1],
        item.extent.orientation[2], item.extent.orientation[3]),
      box: {
        dimensions: new Cesium.Cartesian3(sx, sy, sz),
        // Wireframe, not a translucent solid. A filled volume big enough to
        // bound a fountain is also big enough to swallow every click aimed at
        // something inside it -- the ducks became unselectable. Edges alone
        // say the same thing and leave the contents reachable.
        fill: false,
        outline: true,
        // Coloured by basis, not by which entity it is. Two volumes over the
        // same water -- the venue's declared bounds and a service's observed
        // ones -- have to be told apart by what kind of claim they are, and a
        // palette keyed to entity ids would say nothing when a third turns up.
        outlineColor: extentColour(item.entity?.basis),
        outlineWidth: 2
      }
    });
    if (description) {
      extentEntity.description = new Cesium.ConstantProperty(description);
      extentEntity.name = item.name;
    }
    entityIds.add(extentEntity.id as string);
    appLog(`content: ${item.name} extent ${sx.toFixed(1)}x${sy.toFixed(1)}x${sz.toFixed(1)} m`);
    return;
  }

  // A model entity that declares neither an asset nor an extent is left as
  // its marker: pin, name, and the details panel. The placeholder cube below
  // would be the client asserting a size the publisher never claimed, which
  // is the same mistake the fountain's 1.2 m box was -- and for a stranger's
  // entity of an unknown type it would be inventing twice over.
  if (item.entity) {
    appLog(`content: ${item.name} — marker only (no asset, no extent)`);
    return;
  }

  // Legacy catalogue content, which has no entity to have declared anything.
  // The cube is a stand-in for "something is here", and nothing more.
  const boxEntity = viewer.entities.add({
    id: `${item.id}-box`,
    position: Cesium.Cartesian3.fromDegrees(item.geopose.lon_deg, item.geopose.lat_deg, item.geopose.alt_m),
    box: {
      dimensions: new Cesium.Cartesian3(1.2, 1.2, 1.1),
      material: Cesium.Color.ORANGE.withAlpha(0.7),
      outline: true,
      outlineColor: Cesium.Color.BLACK,
      heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND
    }
  });
  entityIds.add(boxEntity.id as string);
}

function handleClear() {
  clearEntities();
  modelItems.clear();
  currentPose = null;
  readoutItems = 0;
  readoutMessage = 'cleared';
  renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
  appLog('clear:done');
}

async function togglePhotorealisticTiles() {
  if (!viewer) {
    return;
  }
  if (!PHOTOREAL_ASSET_ID) {
    readoutMessage = 'missing VITE_CESIUM_ION_ASSET_ID';
    renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
    return;
  }

  if (!photorealisticTileset) {
    try {
      const resource = await Cesium.IonResource.fromAssetId(PHOTOREAL_ASSET_ID);
      photorealisticTileset = await Cesium.Cesium3DTileset.fromUrl(resource);
      viewer.scene.primitives.add(photorealisticTileset);
    } catch (error) {
      console.warn('photorealistic tileset load failed', error);
      readoutMessage = 'failed to load photorealistic tiles';
      renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
      return;
    }
  }

  photorealisticEnabled = !photorealisticEnabled;
  photorealisticTileset.show = photorealisticEnabled;
  if (toggleTilesBtn) {
    toggleTilesBtn.textContent = `Photorealistic 3D Tiles: ${photorealisticEnabled ? 'On' : 'Off'}`;
  }
  appLog(`tileset:photorealistic ${photorealisticEnabled ? 'on' : 'off'}`);
}

function wsUrlFromBridgeUrl(url: string): string {
  if (url.startsWith('https://')) {
    return url.replace('https://', 'wss://');
  }
  if (url.startsWith('http://')) {
    return url.replace('http://', 'ws://');
  }
  return `ws://${url}`;
}

/**
 * JSON.stringify replacer that summarises payloads instead of printing them.
 *
 * The localize request carries a base64 JPEG. Printed in full it is tens of
 * thousands of characters and the panel becomes useless, so it is shown as its
 * size — which is the part a reader actually wants from it.
 */
function abbreviateBlobs(_key: string, value: unknown): unknown {
  if (typeof value === 'string' && value.length > 256) {
    return `<${Math.round((value.length * 3) / 4 / 1024)} KB base64, ${value.length} chars>`;
  }
  return value;
}

function renderRestOverlay() {
  const body = document.getElementById('restOverlayBody');
  if (body) {
    body.textContent = restMessages.join('\n\n');
  }
}

/**
 * Record one HTTP exchange with the bridge.
 *
 * The counterpart to the DDS window: that one shows what reached the bus,
 * this one shows what the browser actually sent and got back. Open both and
 * a single REST call is visible turning into the several bus messages it
 * causes — which is the bridge's whole job, and otherwise invisible.
 */
function pushRestExchange(exchange: RestExchange) {
  const status = exchange.error
    ? `${exchange.status || 'ERR'} ${exchange.error}`.trim()
    : String(exchange.status);
  const lines = [`${exchange.method} ${exchange.path} -> ${status} (${exchange.ms}ms)`];
  if (exchange.request !== undefined) {
    lines.push(`request:  ${JSON.stringify(exchange.request, abbreviateBlobs)}`);
  }
  if (exchange.response !== undefined) {
    lines.push(`response: ${JSON.stringify(exchange.response, null, 2)}`);
  }
  restMessages.push(lines.join('\n'));
  const limit = 50;
  if (restMessages.length > limit) {
    restMessages.splice(0, restMessages.length - limit);
  }
  renderRestOverlay();
}

function toggleRestOverlay() {
  restOverlayVisible = !restOverlayVisible;
  if (toggleRestOverlayBtn) {
    toggleRestOverlayBtn.textContent = `REST Messages: ${restOverlayVisible ? 'On' : 'Off'}`;
  }
  const panel = document.getElementById('restOverlay');
  panel?.classList.toggle('hidden', !restOverlayVisible);
  renderRestOverlay();
}

function renderDdsOverlay() {
  if (!ddsOverlayBodyEl) {
    return;
  }
  ddsOverlayBodyEl.textContent = ddsMessages.join('\n\n');
}

function pushDdsMessage(entry: string) {
  ddsMessages.push(entry);
  const limit = 50;
  if (ddsMessages.length > limit) {
    ddsMessages.splice(0, ddsMessages.length - limit);
  }
  renderDdsOverlay();
}

/**
 * The bus lanes this demo drives.
 *
 * A deployment can carry more than one demo on one bus — the hosted stack runs
 * the multi-operator fusion demo beside this one — and the fusion side
 * publishes detections and entity bindings continuously. Subscribing to
 * everything drowns the four exchanges an AR client actually makes in traffic
 * belonging to a different demo.
 *
 * Server-side patterns rather than filtering here, so the unwanted samples are
 * never sent at all. That is what `/ws` is for; `/v1/stream` is the
 * unfiltered firehose and has no way to say "only these".
 */
const DDS_OVERLAY_PATTERNS = [
  'spatialdds/discovery/*',              // CoverageQuery and the replies
  'spatialdds/*/discovery/announce/v1',  // services arriving and departing
  'spatialdds/vps/*',                    // localize request and reply
  'spatialdds/catalog/*',                // content query and reply
  // The world model lanes. Nothing appears here at start-up even though the
  // model is already on the bus: both topics are latched, so the seed was
  // delivered to the bridge's reader when *it* joined, and a browser opening
  // later is a new subscriber to the bridge, not to DDS. What shows up here
  // is what crosses the bus while you are watching -- an entity republished
  // by scripts/move_duck.py, for instance.
  'spatialdds/model/*'
];

function connectDdsOverlay() {
  if (ddsSocket) {
    return;
  }
  const wsUrl = `${wsUrlFromBridgeUrl(BRIDGE_URL)}/ws`;
  try {
    ddsSocket = new WebSocket(wsUrl);
  } catch (error) {
    pushDdsMessage(`ws: failed to connect (${String(error)})`);
    ddsSocket = null;
    return;
  }

  ddsSocket.onopen = () => {
    pushDdsMessage(`ws: connected ${wsUrl}`);
    DDS_OVERLAY_PATTERNS.forEach((pattern, i) => {
      ddsSocket?.send(JSON.stringify({
        type: 'subscribe', id: `overlay_${i}`, pattern
      }));
    });
    pushDdsMessage(`ws: watching ${DDS_OVERLAY_PATTERNS.join('  ')}`);
  };
  ddsSocket.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data as string) as {
        type?: string;
        msg_type?: string;
        logical_topic?: string;
        timestamp_ns?: number;
        payload?: unknown;
        message?: string;
      };
      if (msg.type === 'error') {
        pushDdsMessage(`ws: ${msg.message || 'error'}`);
        return;
      }
      // subscribed / pong / topics acknowledgements are protocol chatter, not
      // bus traffic — this window is for what crossed the bus.
      if (msg.type !== 'data') {
        return;
      }
      const ts = msg.timestamp_ns
        ? new Date(msg.timestamp_ns / 1_000_000).toISOString()
        : new Date().toISOString();
      const header = `[${ts}] ${msg.msg_type || '?'} ${msg.logical_topic || ''}`.trim();
      const body = msg.payload ? JSON.stringify(msg.payload, null, 2) : '';
      pushDdsMessage(body ? `${header}\n${body}` : header);
    } catch (error) {
      pushDdsMessage(`ws: parse error ${String(error)}`);
    }
  };
  ddsSocket.onerror = () => {
    pushDdsMessage('ws: error');
  };
  ddsSocket.onclose = () => {
    pushDdsMessage('ws: closed');
    ddsSocket = null;
  };
}

function disconnectDdsOverlay() {
  if (!ddsSocket) {
    return;
  }
  ddsSocket.close();
  ddsSocket = null;
}

function toggleDdsOverlay() {
  ddsOverlayVisible = !ddsOverlayVisible;
  if (toggleDdsOverlayBtn) {
    toggleDdsOverlayBtn.textContent = `DDS Messages: ${ddsOverlayVisible ? 'On' : 'Off'}`;
  }
  if (ddsOverlayEl) {
    ddsOverlayEl.classList.toggle('hidden', !ddsOverlayVisible);
  }
  if (ddsOverlayVisible) {
    connectDdsOverlay();
  } else {
    disconnectDdsOverlay();
  }
}

function enableFpsControls(activeViewer: Cesium.Viewer) {
  const scene = activeViewer.scene;
  const camera = scene.camera;
  const canvas = activeViewer.canvas;

  canvas.setAttribute('tabindex', '0');
  canvas.style.outline = 'none';
  canvas.addEventListener('click', () => canvas.focus());

  const keys: Record<string, boolean> = Object.create(null);
  window.addEventListener('keydown', (event) => {
    keys[event.code] = true;
  });
  window.addEventListener('keyup', (event) => {
    keys[event.code] = false;
  });

  let mouseDown = false;
  let lastX = 0;
  let lastY = 0;

  canvas.addEventListener('mousedown', (event) => {
    mouseDown = true;
    lastX = event.clientX;
    lastY = event.clientY;
  });

  window.addEventListener('mouseup', () => {
    mouseDown = false;
  });

  window.addEventListener('mousemove', (event) => {
    if (!mouseDown) {
      return;
    }

    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    lastX = event.clientX;
    lastY = event.clientY;

    const lookSpeed = 0.0025;
    camera.lookRight(dx * lookSpeed);
    camera.lookUp(-dy * lookSpeed);
  });

  activeViewer.clock.onTick.addEventListener(() => {
    // Cesium's Clock has no public deltaTime in these typings; the
    // fixed step is what this used in practice anyway, since the
    // clock is paused for a static scene.
    const dt = 0.016;
    const moveSpeed = 2.0;
    const step = moveSpeed * dt;
    const turnSpeed = 1.2;
    const turnStep = turnSpeed * dt;

    if (keys['KeyW']) camera.moveForward(step);
    if (keys['KeyS']) camera.moveBackward(step);
    if (keys['KeyA']) camera.moveLeft(step);
    if (keys['KeyD']) camera.moveRight(step);

    if (keys['KeyE']) camera.moveUp(step);
    if (keys['KeyQ']) camera.moveDown(step);

    if (keys['ArrowLeft']) camera.lookLeft(turnStep);
    if (keys['ArrowRight']) camera.lookRight(turnStep);

    renderReadout(cameraGeoPose(activeViewer));
  });
}

async function loadSceneAssets(activeViewer: Cesium.Viewer) {
  try {
    const buildings = await Cesium.createOsmBuildingsAsync();
    activeViewer.scene.primitives.add(buildings);
  } catch (error) {
    console.warn('OSM Buildings unavailable:', error);
  }

  // No world terrain, deliberately.
  //
  // The photorealistic tiles carry their own ground, and world terrain draws a
  // second, coarser one on top of it. At Littlefield Fountain the terrain
  // surface sits 2.5 m above the tiles' water (146.22 m against 143.75 m,
  // both measured from this scene), so anything placed on the water is behind
  // the globe and simply never appears — no error, no warning, just missing
  // content. A duck placed correctly to within 10 cm was invisible for exactly
  // this reason.
  //
  // The globe itself stays: it is what the opening view of the Earth is made
  // of. On the ellipsoid its surface is ~145 m below the tiles here, far
  // enough not to occlude anything standing on them. `CLAMP_TO_GROUND` still
  // resolves against the 3D tiles, so markers are unaffected.
  activeViewer.scene.globe.depthTestAgainstTerrain = true;
}

export function initApp() {
  // Cesium's default imagery, terrain, geocoder and base-layer picker are all
  // Ion services, so with no token the globe renders as nothing at all — not
  // as a plain globe. A deployment that ships no credential still has to show
  // something, so fall back to OpenStreetMap raster tiles and switch off the
  // widgets that would only fail.
  //
  // Local development keeps Ion when web/.env.local supplies a token, which is
  // also what the photorealistic-tiles toggle needs.
  const viewerOptions: Cesium.Viewer.ConstructorOptions = {
    terrain: undefined,
    // Without preserveDrawingBuffer the WebGL back buffer is cleared after
    // each present, and reading the canvas returns a blank image. The demo
    // captures the rendered view as the VPS query frame, so it needs the
    // buffer to survive long enough to be read.
    contextOptions: { webgl: { preserveDrawingBuffer: true } }
  };
  if (!HAS_ION_TOKEN) {
    viewerOptions.baseLayer = new Cesium.ImageryLayer(
      new Cesium.OpenStreetMapImageryProvider({
        url: 'https://tile.openstreetmap.org/'
      })
    );
    viewerOptions.baseLayerPicker = false;   // lists Ion assets
    viewerOptions.geocoder = false;          // Ion geocoding service
    appLog('cesium: no Ion token — OpenStreetMap imagery, no photoreal tiles');
  }
  viewer = new Cesium.Viewer('cesiumContainer', viewerOptions);
  // `?debug=1` publishes the viewer so a test or a console can drive the
  // camera and inspect what was actually placed. Checking where content ended
  // up otherwise means eyeballing a screenshot and guessing, which is how a
  // model sitting in a tree canopy passed for correct twice.
  if (new URLSearchParams(location.search).has('debug')) {
    (window as unknown as Record<string, unknown>).__viewer = viewer;
  }

  viewer.scene.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(START_LON, START_LAT, START_HEIGHT_M),
    orientation: {
      heading: Cesium.Math.toRadians(START_VIEW_HEADING_DEG),
      pitch: Cesium.Math.toRadians(START_VIEW_PITCH_DEG),
      roll: 0.0
    }
  });

  const controller = viewer.scene.screenSpaceCameraController;
  controller.enableLook = true;
  controller.enableTilt = true;
  controller.enableTranslate = false;
  controller.enableZoom = true;
  controller.minimumZoomDistance = 0.5;
  controller.maximumZoomDistance = 200.0;

  enableFpsControls(viewer);

  appLog('viewer:ready');
  readoutMessage = 'ready';
  renderReadout(cameraGeoPose(viewer));

  localizeBtn?.addEventListener('click', () => {
    void handleLocalize();
  });

  localizeImageBtn?.addEventListener('click', () => {
    void handleLocalizeWithImage();
  });

  discoverBtn?.addEventListener('click', () => {
    void handleDiscover();
  });

  clearBtn?.addEventListener('click', () => {
    handleClear();
  });

  toggleTilesBtn?.addEventListener('click', () => {
    void togglePhotorealisticTiles();
  });

  toggleDdsOverlayBtn?.addEventListener('click', () => {
    toggleDdsOverlay();
  });

  toggleRestOverlayBtn?.addEventListener('click', () => {
    toggleRestOverlay();
  });

  // Record from startup, not from when the panel is opened, so the calls the
  // app made while booting (/health, and the first discovery) are already
  // there when you look.
  observeRest(pushRestExchange);


  void loadSceneAssets(viewer);

  void togglePhotorealisticTiles();

  void initBridgeMode();
}

async function initBridgeMode() {
  // Before the mode check, so the button's enabled state can depend on it.
  try {
    queryFrames = await loadQueryFrameManifest();
    if (queryFrames) {
      appLog(`frames: ${queryFrames.frames.length} installed` +
             (queryFrames.label ? ` (${queryFrames.label})` : ''));
      if (frameSelect && framePick) {
        for (const name of queryFrames.frames) {
          const option = document.createElement('option');
          option.value = name;
          option.textContent = name.replace(/\.jpg$/i, '');
          frameSelect.append(option);
        }
        framePick.hidden = false;
        showFramePreview();
        frameSelect.addEventListener('change', showFramePreview);
      }
    }
  } catch (error) {
    appLog(`frames: manifest invalid (${String(error)})`);
    queryFrames = null;
  }

  const status = await bridgeHealth();
  bridgeActive = status.ok;

  if (bridgeActive) {
    setModeBadge('bridge', `DDS domain ${status.dds_domain ?? 'unknown'}`);
    if (localizeBtn) localizeBtn.textContent = 'Localize (DDS)';
    if (discoverBtn) discoverBtn.textContent = 'Discover Content (DDS)';
    // Enabled only with a bundle installed: without one there is no real
    // photograph to send, and no anchor to search for a VPS around.
    if (localizeImageBtn) {
      localizeImageBtn.disabled = queryFrames === null;
      if (!queryFrames) {
        localizeImageBtn.title = 'No query-frame bundle installed (see ar_demo/README.md)';
      }
    }
    readoutMessage = 'dds bridge online';
    appLog('bridge:online');
  } else {
    setModeBadge('mock', status.message);
    if (localizeBtn) localizeBtn.textContent = 'Localize (Mock VPS)';
    if (discoverBtn) discoverBtn.textContent = 'Discover Content (Mock Catalog)';
    // The mock returns a canned downtown pose and never reads the image, so
    // offering the button here would imply a localization that never happened.
    if (localizeImageBtn) {
      localizeImageBtn.disabled = true;
      localizeImageBtn.title = 'Needs the DDS bridge — the mock VPS ignores query imagery';
    }
    readoutMessage = `mock mode (${status.message})`;
    appLog(`bridge:offline ${status.message}`);
  }

  renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
  await autoLocalizeIfThisIsTheDemosOwnVps();
}

/**
 * A demo tab should show the venue, not an empty sky and two buttons.
 *
 * But only against our own mock VPS. A real one -- OpenVPS on a GPU box --
 * would get a localization request per page load if this fired blindly, which
 * is rude to the service and, worse, hides what localization costs behind a
 * page refresh. This demo exists partly to make that exchange visible, and a
 * demo that hides its own cost teaches the wrong lesson.
 *
 * So the discriminator is asked of the bus rather than configured: the demo's
 * mock announces itself under `svc:vps:demo/`, the real deployment as
 * `svc:vps:oarc/openvps-scan`. Finding a service is a read of the announce
 * cache and costs nobody anything. Anything unrecognised -- or nothing
 * announced at all -- leaves the button exactly where it was, which is the
 * safe direction to fail in.
 */
const DEMO_VPS_PREFIX = 'svc:vps:demo/';

async function autoLocalizeIfThisIsTheDemosOwnVps() {
  if (currentPose) {
    return;                      // somebody was faster than we were.
  }
  // `?autostart=0` for anyone who wants the pre-localization state: a test
  // that drives the exchange itself and would otherwise race a second one,
  // or a person who wants to watch the request happen rather than find it
  // already in the overlay. The bridge serialises localize requests, so a
  // suite where every open page asks for one queues behind itself.
  if (new URLSearchParams(location.search).get('autostart') === '0') {
    appLog('autostart: disabled by ?autostart=0 — press Localize');
    return;
  }
  if (!bridgeActive) {
    // No bridge: `mockLocalize` is a local function returning a canned pose,
    // so nothing is asked of anyone.
    appLog('autostart: mock mode — localizing without a click');
    await handleLocalize();
    return;
  }
  try {
    const prior = await seedPriorGeopose();
    const serviceId = await ensureVpsService(prior);
    if (!serviceId?.startsWith(DEMO_VPS_PREFIX)) {
      appLog(`autostart: held — ${serviceId || 'no VPS announced'} is not the ` +
             `demo's own; press Localize to send a real request`);
      return;
    }
    appLog(`autostart: ${serviceId} is the demo's mock — localizing without a click`);
    await handleLocalize();
  } catch (error) {
    // Never let the convenience break the page it was meant to improve.
    appLog(`autostart: skipped (${String(error)})`);
  }
}
