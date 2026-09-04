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
  bridgeModelSnapshot, catalogRefId, modelEntityToItem, observeRest, typeLabel
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
const START_HEADING_DEG = 160.0;
const START_PITCH_DEG = -10.0;
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
function addMarker(id: string, name: string, geopose: GeoPose, imageUrl: string,
                   labelOnly = false, clampToGround = true) {
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
      // Clear of whatever is below it. A pin is 32 px tall, but a model draws
      // itself at minimumPixelSize 64, and the offset tuned for the pin left
      // the name sitting across the duck's back.
      pixelOffset: new Cesium.Cartesian2(0, labelOnly ? -44 : -36),
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

async function handleLocalize() {
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
  // The chosen frame, or the next one round if there is no picker.
  const file = frameSelect?.value
    || queryFrames.frames[frameCursor++ % queryFrames.frames.length];

  let queryImage: string;
  try {
    queryImage = await loadQueryFrame(file);
  } catch (error) {
    appLog(`frame: load failed (${String(error)})`);
    return;
  }
  appLog(
    `frame: ${file} ${Math.round((queryImage.length * 3) / 4 / 1024)} KB` +
    (queryFrames.label ? ` (${queryFrames.label})` : '')
  );

  vpsServiceId = null;
  await localizeWith(priorFromManifest(queryFrames), queryImage);
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

async function handleDiscover() {
  if (!currentPose) {
    readoutItems = 0;
    readoutMessage = 'localize first';
    renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
    return;
  }

  if (bridgeActive) {
    await ensureContentService(currentPose);
  }
  const response = bridgeActive ? await bridgeDiscover(currentPose) : await mockDiscover(currentPose);

  // The world model, when one is publishing.
  //
  // No flag switches this on. The layer announces itself by existing: if
  // `/v1/model` returns entities, they are what gets placed, and the
  // catalogue contributes only the asset each one points at. If nothing is
  // publishing the call returns an empty model and everything below behaves
  // exactly as it did before, which is what keeps the existing demo intact.
  //
  // `?catalogpose=1` forces the legacy path for side-by-side comparison.
  const forceCatalogPose = new URLSearchParams(location.search).has('catalogpose');
  const model = (bridgeActive && !forceCatalogPose)
    ? await bridgeModelSnapshot() : { entities: [], relationships: [] };

  // Suppression is per content_id, not wholesale. A catalogue row that no
  // entity references still places itself -- only the rows the model has
  // taken responsibility for are skipped, so the two paths cannot both draw
  // the same duck and nothing else is silently dropped.
  const claimed = new Set<string>();
  for (const entity of model.entities) {
    const ref = catalogRefId(entity);
    if (ref) {
      claimed.add(ref);
    }
  }

  const fromCatalog = response.items.filter((item) => !claimed.has(item.id));
  fromCatalog.forEach((item) => addItemEntity(item));

  const frames = response.frames || {};
  const assets = response.assets || {};
  lastFrames = frames;
  lastAssets = assets;
  let placed = 0;
  for (const entity of model.entities) {
    const item = modelEntityToItem(entity, frames, (id) => assets[id]);
    if (!item) {
      appLog(`model:unresolved ${entity.entity_id} — frame ${entity.frame_ref?.fqn}`);
      continue;
    }
    modelItems.set(entity.entity_id, item);
    addItemEntity(item);
    placed += 1;
  }

  readoutItems = fromCatalog.length + placed;
  readoutMessage = '';
  renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
  if (model.entities.length) {
    appLog(`discover:model ${placed} entities, ${model.relationships.length} ` +
           `relationships; ${claimed.size} catalog row(s) superseded`);
    connectModelStream();
  }
  appLog(`discover:items ${readoutItems}`);
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
      if (msg.type !== 'data' || msg.msg_type !== 'oarc.model_entity') {
        return;
      }
      const entity = msg.payload;
      if (!entity?.entity_id) {
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
  addMarker(item.id, item.name, markerPose, itemUrl, drawsItself, !item.extent);
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
        outlineColor: Cesium.Color.fromCssColorString('rgba(95, 196, 205, 0.9)'),
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
    const dt = activeViewer.clock.deltaTime || 0.016;
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
}
