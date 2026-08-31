/**
 * Georeference a map by eye, against photorealistic 3D tiles.
 *
 * OpenVPS ships MapAligner for this, over a vector basemap. That works
 * indoors, where you are placing a room inside a building outline, and badly
 * outdoors, where the reference is OSM footprints and the thing being placed
 * is a sparse cloud. This puts the map's own dense cloud over Google's
 * photorealistic tiles instead, so you align façade to façade.
 *
 * What it emits is exactly what OpenVPS stores: a geodetic anchor plus a
 * `matrix` taking map coordinates into the anchor's ENU frame — the same
 * `map_to_enu` the localizer applies before converting a pose to geodetic.
 *
 * Only yaw and position are exposed. A map that has been metrically aligned
 * to its ARKit prior (`hloc_metric_alignment --mode rescale_model`) is already
 * level and metric, so roll, pitch and scale are not free parameters — and
 * offering them would invite undoing gravity by hand.
 */
import * as Cesium from 'cesium';

/** Map frame is the ARKit frame with Y and Z negated, so up is -Y. */
const MAP_TO_ENU_BASIS = new Cesium.Matrix3(
  1, 0, 0,
  0, 0, 1,
  0, -1, 0
);

interface Cloud {
  positions: Float64Array;
  colors: Uint8Array;
  count: number;
}

/** Binary little-endian PLY with x,y,z float32 and r,g,b uchar. */
function parsePly(buffer: ArrayBuffer, maxPoints: number): Cloud {
  const bytes = new Uint8Array(buffer);
  const marker = 'end_header\n';
  let headerEnd = -1;
  const head = new TextDecoder().decode(bytes.subarray(0, Math.min(4096, bytes.length)));
  const at = head.indexOf(marker);
  if (at >= 0) headerEnd = at + marker.length;
  if (headerEnd < 0) throw new Error('not a PLY: no end_header');
  const header = head.slice(0, headerEnd);
  if (!/format\s+binary_little_endian/.test(header)) {
    throw new Error('only binary_little_endian PLY is supported');
  }
  const total = Number(/element vertex (\d+)/.exec(header)?.[1] ?? 0);
  if (!total) throw new Error('PLY declares no vertices');

  const STRIDE = 15; // 3 float32 + 3 uchar
  const stride = Math.max(1, Math.ceil(total / maxPoints));
  const count = Math.ceil(total / stride);
  const view = new DataView(buffer, headerEnd);
  const positions = new Float64Array(count * 3);
  const colors = new Uint8Array(count * 3);
  let w = 0;
  for (let i = 0; i < total; i += stride) {
    const o = i * STRIDE;
    positions[w * 3] = view.getFloat32(o, true);
    positions[w * 3 + 1] = view.getFloat32(o + 4, true);
    positions[w * 3 + 2] = view.getFloat32(o + 8, true);
    colors[w * 3] = view.getUint8(o + 12);
    colors[w * 3 + 1] = view.getUint8(o + 13);
    colors[w * 3 + 2] = view.getUint8(o + 14);
    w += 1;
  }
  return { positions, colors, count: w };
}

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`missing #${id}`);
  return node as T;
}

export async function initAligner() {
  const params = new URLSearchParams(location.search);
  const lat = Number(params.get('lat') ?? 30.284996);
  const lon = Number(params.get('lon') ?? -97.739494);
  const height = Number(params.get('h') ?? 0);
  const cloudUrl = params.get('ply')
    ?? `${import.meta.env.BASE_URL}aligner/fountain2.ply`;

  const status = el<HTMLParagraphElement>('status');
  const viewer = new Cesium.Viewer('cesiumContainer', {
    timeline: false, animation: false, baseLayerPicker: false,
    geocoder: false, homeButton: false, sceneModePicker: false,
    navigationHelpButton: false, infoBox: false, selectionIndicator: false
  });
  viewer.scene.globe.depthTestAgainstTerrain = true;

  // The anchor is where the map's origin sits on Earth. It starts from the
  // URL (or a default) and is meant to be moved: click the ground where the
  // map's origin belongs. Guessing it from a nearby landmark is how the first
  // attempt ended up at the wrong end of the lawn.
  const anchor = { lat, lon, height };
  let anchorCartesian = Cesium.Cartesian3.fromDegrees(anchor.lon, anchor.lat, anchor.height);
  let enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(anchorCartesian);
  viewer.camera.lookAt(
    anchorCartesian, new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-35), 120));

  const marker = viewer.entities.add({
    position: new Cesium.CallbackProperty(() => anchorCartesian, false),
    point: { pixelSize: 12, color: Cesium.Color.ORANGE,
             outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
             disableDepthTestDistance: Number.POSITIVE_INFINITY },
    label: { text: 'anchor', font: '12px sans-serif', pixelOffset: new Cesium.Cartesian2(0, -18),
             fillColor: Cesium.Color.ORANGE, showBackground: true,
             backgroundColor: Cesium.Color.fromCssColorString('#0b0e14cc'),
             disableDepthTestDistance: Number.POSITIVE_INFINITY }
  });
  void marker;

  // Photorealistic tiles are the whole point of using Cesium here; without a
  // token this degrades to the default imagery, which is no better than the
  // basemap we were trying to get away from. Say so rather than look broken.
  const assetId = Number(import.meta.env.VITE_CESIUM_ION_ASSET_ID ?? 0);
  let tileset: Cesium.Cesium3DTileset | null = null;
  if (assetId) {
    try {
      tileset = await Cesium.Cesium3DTileset.fromIonAssetId(assetId);
      viewer.scene.primitives.add(tileset);
    } catch (error) {
      status.textContent = `3D tiles unavailable (${String(error)})`;
    }
  } else {
    status.textContent = 'no VITE_CESIUM_ION_ASSET_ID — aligning against plain imagery';
  }

  const response = await fetch(cloudUrl);
  if (!response.ok) throw new Error(`${cloudUrl}: HTTP ${response.status}`);
  const cloud = parsePly(await response.arrayBuffer(), 300_000);

  const points = viewer.scene.primitives.add(new Cesium.PointPrimitiveCollection());
  for (let i = 0; i < cloud.count; i += 1) {
    points.add({
      position: new Cesium.Cartesian3(
        cloud.positions[i * 3], cloud.positions[i * 3 + 1], cloud.positions[i * 3 + 2]),
      color: Cesium.Color.fromBytes(
        cloud.colors[i * 3], cloud.colors[i * 3 + 1], cloud.colors[i * 3 + 2]),
      pixelSize: 2
    });
  }
  const state = { yaw: 0, east: 0, north: 0, up: 0 };
  const describe = () =>
    `${cloud.count.toLocaleString()} points · anchor `
    + `${anchor.lat.toFixed(6)}, ${anchor.lon.toFixed(6)} · click ground to move it`;

  /** map -> ENU: basis first, then yaw about up, then the offset. */
  function mapToEnu(): Cesium.Matrix4 {
    const spin = Cesium.Matrix3.fromRotationZ(Cesium.Math.toRadians(state.yaw));
    const rot = Cesium.Matrix3.multiply(spin, MAP_TO_ENU_BASIS, new Cesium.Matrix3());
    return Cesium.Matrix4.fromRotationTranslation(
      rot, new Cesium.Cartesian3(state.east, state.north, state.up));
  }

  function apply() {
    points.modelMatrix = Cesium.Matrix4.multiply(
      enuToFixed, mapToEnu(), new Cesium.Matrix4());
    el('yawVal').textContent = `${state.yaw.toFixed(1)}°`;
    el('eastVal').textContent = `${state.east.toFixed(1)} m`;
    el('northVal').textContent = `${state.north.toFixed(1)} m`;
    el('upVal').textContent = `${state.up.toFixed(1)} m`;
    el('out').textContent = JSON.stringify(transform(), null, 1);
    status.textContent = describe();
  }

  // Left click on the tiles moves the anchor there. Cesium reports a drag as a
  // separate event, so this does not fire while orbiting.
  const picker = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
  picker.setInputAction((click: { position: Cesium.Cartesian2 }) => {
    const hit = viewer.scene.pickPosition(click.position);
    if (!Cesium.defined(hit)) return;
    const carto = Cesium.Cartographic.fromCartesian(hit);
    anchor.lat = Cesium.Math.toDegrees(carto.latitude);
    anchor.lon = Cesium.Math.toDegrees(carto.longitude);
    anchor.height = carto.height;
    anchorCartesian = Cesium.Cartesian3.fromDegrees(anchor.lon, anchor.lat, anchor.height);
    enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(anchorCartesian);
    apply();
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

  /** Exactly the shape OpenVPS stores in transform.json. */
  function transform() {
    // Cesium stores Matrix4 column-major; transform.json wants rows.
    const flat = Cesium.Matrix4.toArray(mapToEnu());
    const rows: number[][] = [];
    for (let r = 0; r < 4; r += 1) {
      rows.push([0, 1, 2, 3].map((c) => Number(flat[c * 4 + r].toFixed(9))));
    }
    return { latitude: anchor.lat, longitude: anchor.lon,
             height: Number(anchor.height.toFixed(3)), matrix: rows };
  }

  const bind = (id: string, key: keyof typeof state) => {
    const input = el<HTMLInputElement>(id);
    input.addEventListener('input', () => {
      state[key] = Number(input.value);
      apply();
    });
  };
  bind('yaw', 'yaw'); bind('east', 'east'); bind('north', 'north'); bind('up', 'up');

  document.querySelectorAll<HTMLButtonElement>('[data-nudge-yaw]').forEach((b) => {
    b.addEventListener('click', () => {
      state.yaw = (state.yaw + Number(b.dataset.nudgeYaw) + 360) % 360;
      el<HTMLInputElement>('yaw').value = String(state.yaw);
      apply();
    });
  });

  el<HTMLInputElement>('size').addEventListener('input', (e) => {
    const px = Number((e.target as HTMLInputElement).value);
    el('sizeVal').textContent = String(px);
    for (let i = 0; i < points.length; i += 1) points.get(i).pixelSize = px;
  });

  el('toggleTiles').addEventListener('click', (e) => {
    if (!tileset) return;
    tileset.show = !tileset.show;
    (e.target as HTMLButtonElement).textContent =
      tileset.show ? 'Hide 3D tiles' : 'Show 3D tiles';
  });

  el('reset').addEventListener('click', () => {
    state.yaw = 0; state.east = 0; state.north = 0; state.up = 0;
    for (const id of ['yaw', 'east', 'north', 'up']) el<HTMLInputElement>(id).value = '0';
    apply();
  });

  el('copy').addEventListener('click', async () => {
    const text = JSON.stringify(transform(), null, 2);
    try {
      await navigator.clipboard.writeText(text);
      el('copy').textContent = 'Copied';
      setTimeout(() => { el('copy').textContent = 'Copy transform JSON'; }, 1200);
    } catch {
      el('out').textContent = text;  // clipboard blocked; it is on screen anyway
    }
  });

  apply();
}
