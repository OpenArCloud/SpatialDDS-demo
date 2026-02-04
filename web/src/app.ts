import * as Cesium from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import { mockDiscover, mockLocalize } from './mock_spatialdds';
import { bridgeDiscover, bridgeHealth, bridgeLocalize } from './spatialdds_bridge';
import type { CatalogItem, GeoPose } from './types';

const readoutEl = document.getElementById('readout') as HTMLPreElement | null;
const localizeBtn = document.getElementById('btnLocalize') as HTMLButtonElement | null;
const discoverBtn = document.getElementById('btnDiscover') as HTMLButtonElement | null;
const toggleTilesBtn = document.getElementById('btnToggleTiles') as HTMLButtonElement | null;
const clearBtn = document.getElementById('btnClear') as HTMLButtonElement | null;
const modeBadgeEl = document.getElementById('modeBadge') as HTMLSpanElement | null;

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
let readoutItems = 0;
let readoutMessage = 'ready';
let bridgeActive = false;
let photorealisticTileset: Cesium.Cesium3DTileset | null = null;
let photorealisticEnabled = false;
const GEOPOSE_QUAT_IS_ENU_TO_BODY = false;

const START_LON = -97.739494;
const START_LAT = 30.284996;
const EYE_HEIGHT_M = 1.7;
const START_HEIGHT_M = 20_000_000.0;
const START_HEADING_DEG = 160.0;
const START_PITCH_DEG = -10.0;
const START_VIEW_HEADING_DEG = 0.0;
const START_VIEW_PITCH_DEG = -90.0;
const START_Q_XYZW: [number, number, number, number] = [0.4967, -0.0336, -0.0585, 0.8653];
// START_Q_XYZW is a body->ENU quaternion (ROS REP-103: x-forward, y-left, z-up).

const ENV = (import.meta as ImportMeta & { env: Record<string, string | undefined> }).env;
const PHOTOREAL_ASSET_ID = ENV.VITE_CESIUM_ION_ASSET_ID
  ? Number(ENV.VITE_CESIUM_ION_ASSET_ID)
  : undefined;

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
    q_xyzw: START_Q_XYZW,
    frame_kind: 'ENU',
    frame_ref: { uuid: 'web-seed', fqn: 'earth.enu' },
    stamp: { sec: Math.floor(nowMs / 1000), nanosec: (nowMs % 1000) * 1_000_000 },
    cov: 'COV_NONE'
  };
}

function orientationFromGeoPose(geopose: GeoPose) {
  const qRaw = new Cesium.Quaternion(
    geopose.q_xyzw[0],
    geopose.q_xyzw[1],
    geopose.q_xyzw[2],
    geopose.q_xyzw[3]
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
  const zBodyInEnu = Cesium.Matrix3.multiplyByVector(
    rBodyToEnu,
    new Cesium.Cartesian3(0, 0, 1),
    new Cesium.Cartesian3()
  );

  const originEcef = Cesium.Cartesian3.fromDegrees(
    geopose.lon_deg,
    geopose.lat_deg,
    geopose.alt_m
  );
  const enuToEcef = Cesium.Transforms.eastNorthUpToFixedFrame(originEcef);
  const rEnuToEcef = Cesium.Matrix4.getMatrix3(enuToEcef, new Cesium.Matrix3());

  // NOTE: For this dataset, body +Y maps to forward (instead of +X).
  const direction = Cesium.Matrix3.multiplyByVector(
    rEnuToEcef,
    yBodyInEnu,
    new Cesium.Cartesian3()
  );
  const up = Cesium.Matrix3.multiplyByVector(
    rEnuToEcef,
    zBodyInEnu,
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
  const q = geopose.q_xyzw.map((value) => value.toFixed(4)).join(', ');
  return `GeoPose: lat=${geopose.lat_deg.toFixed(6)} lon=${geopose.lon_deg.toFixed(6)} alt=${geopose.alt_m.toFixed(2)}m\nq_xyzw: [${q}]`;
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
  readoutEl.textContent = `${formatGeoPose(geopose)}\nitems: ${readoutItems}${messageLine}`;
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
    q_xyzw: [orientation.x, orientation.y, orientation.z, orientation.w],
    frame_kind: 'ENU',
    frame_ref: { uuid: 'camera-frame', fqn: 'camera.enu' },
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

async function handleLocalize() {
  const prior = seedPriorGeopose();
  const response = bridgeActive ? await bridgeLocalize(prior) : await mockLocalize();
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

async function handleDiscover() {
  if (!currentPose) {
    readoutItems = 0;
    readoutMessage = 'localize first';
    renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
    return;
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

  try {
    activeViewer.terrainProvider = await Cesium.createWorldTerrainAsync();
    activeViewer.scene.globe.depthTestAgainstTerrain = true;
  } catch (error) {
    console.warn('World terrain unavailable:', error);
  }
}

export function initApp() {
  viewer = new Cesium.Viewer('cesiumContainer', {
    terrain: undefined
  });

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

  discoverBtn?.addEventListener('click', () => {
    void handleDiscover();
  });

  clearBtn?.addEventListener('click', () => {
    handleClear();
  });

  toggleTilesBtn?.addEventListener('click', () => {
    void togglePhotorealisticTiles();
  });


  void loadSceneAssets(viewer);

  void togglePhotorealisticTiles();

  void initBridgeMode();
}

async function initBridgeMode() {
  const status = await bridgeHealth();
  bridgeActive = status.ok;

  if (bridgeActive) {
    setModeBadge('bridge', `DDS domain ${status.dds_domain ?? 'unknown'}`);
    if (localizeBtn) localizeBtn.textContent = 'Localize (DDS)';
    if (discoverBtn) discoverBtn.textContent = 'Discover Content (DDS)';
    readoutMessage = 'dds bridge online';
    appLog('bridge:online');
  } else {
    setModeBadge('mock', status.message);
    if (localizeBtn) localizeBtn.textContent = 'Localize (Mock VPS)';
    if (discoverBtn) discoverBtn.textContent = 'Discover Content (Mock Catalog)';
    readoutMessage = `mock mode (${status.message})`;
    appLog(`bridge:offline ${status.message}`);
  }

  renderReadout(viewer ? cameraGeoPose(viewer) : currentPose);
}
