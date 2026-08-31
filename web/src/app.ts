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
  observeRest
} from './spatialdds_bridge';
import type { RestExchange } from './spatialdds_bridge';
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
const ddsMessages: string[] = [];

const START_LON = -97.739494;
const START_LAT = 30.284996;
const EYE_HEIGHT_M = 1.7;
const START_HEIGHT_M = 20_000_000.0;
const START_HEADING_DEG = 160.0;
const START_PITCH_DEG = -10.0;
const START_VIEW_HEADING_DEG = 0.0;
const START_VIEW_PITCH_DEG = -90.0;
const START_Q: [number, number, number, number] = [0.4967, -0.0336, -0.0585, 0.8653];
// START_Q is a body->ENU quaternion (ROS REP-103: x-forward, y-left, z-up).

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

function seedPriorGeopose(): GeoPose {
  const nowMs = Date.now();
  return {
    lat_deg: START_LAT,
    lon_deg: START_LON,
    alt_m: 18,
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

function addMarker(id: string, name: string, geopose: GeoPose, imageUrl: string) {
  if (!viewer) {
    return;
  }
  const entity = viewer.entities.add({
    id,
    position: Cesium.Cartesian3.fromDegrees(geopose.lon_deg, geopose.lat_deg, geopose.alt_m),
    billboard: {
      image: imageUrl,
      verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
      heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
      disableDepthTestDistance: 0,
      height: 32,
      width: 32
    },
    label: {
      text: name,
      font: '14px sans-serif',
      fillColor: Cesium.Color.WHITE,
      outlineColor: Cesium.Color.BLACK,
      outlineWidth: 2,
      style: Cesium.LabelStyle.FILL_AND_OUTLINE,
      verticalOrigin: Cesium.VerticalOrigin.TOP,
      pixelOffset: new Cesium.Cartesian2(0, -36),
      heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
      disableDepthTestDistance: 0
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
  const prior = seedPriorGeopose();
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
  response.items.forEach((item) => addItemEntity(item));
  readoutItems = response.items.length;
  readoutMessage = '';
  renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
  appLog(`discover:items ${response.items.length}`);
}

function addItemEntity(item: CatalogItem) {
  addMarker(item.id, item.name, item.geopose, itemUrl);
  if (!viewer) {
    return;
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
    entityIds.add(modelEntity.id as string);
    appLog(`content: ${item.name} -> ${uri}` +
           (item.asset_hash ? ` (${item.asset_hash.slice(0, 14)}…)` : ''));
    return;
  }

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
  'spatialdds/catalog/*'                 // content query and reply
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
