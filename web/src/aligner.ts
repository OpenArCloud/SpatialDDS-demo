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
 * Two layers, because depth from a phone is only trustworthy up to a point.
 * Measured on this capture, ARKit's depth is unbiased out to ~8 m and then
 * under-reports: at 18-30 m even the least-occluded returns come back at 0.70
 * of true distance, which pulls far structure toward the camera and makes a
 * building look both too close and too small. So the dense layer is capped at
 * the honest range, and COLMAP's sparse points — triangulated from imagery,
 * correct at any distance — carry everything beyond it.
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
  // Littlefield Fountain, from OpenStreetMap. Previously seeded from the AR
  // demo's start position, which is 119 m north — the other end of the South
  // Mall lawn. A landmark's coordinates are worth looking up, not inferring.
  const lat = Number(params.get('lat') ?? 30.2839212);
  const lon = Number(params.get('lon') ?? -97.7396265);
  const height = Number(params.get('h') ?? 0);
  const base = import.meta.env.BASE_URL;
  const nearUrl = params.get('near') ?? `${base}aligner/fountain2_near.ply`;
  const farUrl = params.get('far') ?? `${base}aligner/fountain2_sparse.ply`;

  const status = el<HTMLParagraphElement>('status');
  const viewer = new Cesium.Viewer('cesiumContainer', {
    timeline: false, animation: false, baseLayerPicker: false,
    geocoder: false, homeButton: false, sceneModePicker: false,
    navigationHelpButton: false, infoBox: false, selectionIndicator: false
  });
  // Google's photorealistic tiles carry their own ground. Leaving the globe on
  // draws Cesium's flat imagery over them, which looks like tiles that loaded
  // but rendered as a smeared texture with no buildings — exactly the symptom.
  viewer.scene.globe.show = false;
  viewer.scene.skyAtmosphere.show = false;

  // The anchor is where the map's origin sits on Earth. It starts from the
  // URL (or a default) and is meant to be moved: click the ground where the
  // map's origin belongs. Guessing it from a nearby landmark is how the first
  // attempt ended up at the wrong end of the lawn.
  const anchor = { lat, lon, height };
  let anchorCartesian = Cesium.Cartesian3.fromDegrees(anchor.lon, anchor.lat, anchor.height);
  let enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(anchorCartesian);
  /**
   * Street level by default: aligning a ground-level capture against façades
   * is done from roughly where the capture was taken, not from above. The
   * overhead view is a click away for checking footprint.
   */
  function viewFrom(mode: 'street' | 'overhead') {
    if (mode === 'street') {
      // 45 m south of the anchor and 6 m up, looking north at it: about 8
      // degrees above horizontal, which is roughly how the capture saw it.
      // The offset is in the target's own east-north-up frame — Cesium does
      // that conversion, so passing a world-space difference tilts it back to
      // near-overhead, which is what happened first time round.
      viewer.camera.lookAt(anchorCartesian, new Cesium.Cartesian3(0, -45, 6));
    } else {
      viewer.camera.lookAt(
        anchorCartesian, new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-60), 160));
    }
  }
  viewFrom('street');

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
  // Reported on its own line: the status line is rewritten on every control
  // change, so a failure written there vanishes on the first slider move and
  // the tiles just look wrong for no stated reason.
  const tilesNote = el<HTMLParagraphElement>('tiles');
  const assetId = Number(import.meta.env.VITE_CESIUM_ION_ASSET_ID ?? 0);
  let tileset: Cesium.Cesium3DTileset | null = null;
  if (assetId) {
    try {
      tileset = await Cesium.Cesium3DTileset.fromIonAssetId(assetId);
      viewer.scene.primitives.add(tileset);
      await tileset.readyPromise?.catch(() => undefined);
      tilesNote.textContent = `3D tiles: Ion asset ${assetId}`;
      tilesNote.className = 'note ok';
    } catch (error) {
      viewer.scene.globe.show = true;   // something to look at, at least
      tilesNote.textContent = `3D tiles FAILED (asset ${assetId}): ${String(error).slice(0, 90)}`;
      tilesNote.className = 'note bad';
    }
  } else {
    viewer.scene.globe.show = true;
    tilesNote.textContent = 'no VITE_CESIUM_ION_ASSET_ID — no 3D buildings';
    tilesNote.className = 'note bad';
  }

  // A height of 0 is 0 *above the ellipsoid*, which at Austin is about 150 m
  // underground. Left alone the anchor, the cloud and the camera all start
  // buried, which reads as "the tiles did not load" — the ground is simply
  // above you. Sample the real surface and put the anchor on it.
  if (tileset) {
    try {
      const sampled = await viewer.scene.clampToHeightMostDetailed([anchorCartesian.clone()]);
      const hit = sampled[0];
      if (Cesium.defined(hit)) {
        anchor.height = Cesium.Cartographic.fromCartesian(hit).height;
        anchorCartesian = Cesium.Cartesian3.fromDegrees(anchor.lon, anchor.lat, anchor.height);
        enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(anchorCartesian);
        viewFrom('street');
      }
    } catch {
      /* keep the supplied height; the click-to-place path still corrects it */
    }
  }

  async function fetchCloud(url: string, cap: number) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return parsePly(await res.arrayBuffer(), cap);
  }
  const [nearCloud, farCloud] = await Promise.all([
    fetchCloud(nearUrl, 500_000),
    fetchCloud(farUrl, 200_000)
  ]);

  function build(cloud: Cloud, size: number) {
    const c = viewer.scene.primitives.add(new Cesium.PointPrimitiveCollection());
    for (let i = 0; i < cloud.count; i += 1) {
      c.add({
        position: new Cesium.Cartesian3(
          cloud.positions[i * 3], cloud.positions[i * 3 + 1], cloud.positions[i * 3 + 2]),
        color: Cesium.Color.fromBytes(
          cloud.colors[i * 3], cloud.colors[i * 3 + 1], cloud.colors[i * 3 + 2]),
        pixelSize: size,
        disableDepthTestDistance: Number.POSITIVE_INFINITY
      });
    }
    return c;
  }
  const points = build(nearCloud, 2);
  // Sparse points are ~30x rarer, so they need to be bigger to read as
  // structure rather than as speckle.
  const far = build(farCloud, 4);
  const state = { yaw: 0, east: 0, north: 0, up: 0, scale: 1 };
  const describe = () =>
    `${nearCloud.count.toLocaleString()} near + ${farCloud.count.toLocaleString()} sparse`
    + ` · anchor ${anchor.lat.toFixed(6)}, ${anchor.lon.toFixed(6)}`
    + ` · click ground to move it`;

  /** map -> ENU: basis first, then yaw about up, then the offset. */
  function mapToEnu(): Cesium.Matrix4 {
    const spin = Cesium.Matrix3.fromRotationZ(Cesium.Math.toRadians(state.yaw));
    const rot = Cesium.Matrix3.multiply(spin, MAP_TO_ENU_BASIS, new Cesium.Matrix3());
    // Scale is a diagnostic, not a normal control. The map should already be
    // metric; if a value other than 1 fits, that is evidence about the map
    // rather than a setting to leave adjusted.
    Cesium.Matrix3.multiplyByScalar(rot, state.scale, rot);
    return Cesium.Matrix4.fromRotationTranslation(
      rot, new Cesium.Cartesian3(state.east, state.north, state.up));
  }

  function apply() {
    const m = Cesium.Matrix4.multiply(enuToFixed, mapToEnu(), new Cesium.Matrix4());
    points.modelMatrix = m;
    far.modelMatrix = m;
    el('yawVal').textContent = `${state.yaw.toFixed(1)}°`;
    el('eastVal').textContent = `${state.east.toFixed(1)} m`;
    el('northVal').textContent = `${state.north.toFixed(1)} m`;
    el('upVal').textContent = `${state.up.toFixed(1)} m`;
    el('scaleVal').textContent = `${state.scale.toFixed(3)}x`;
    el('out').textContent = JSON.stringify(transform(), null, 1);
    status.textContent = describe();
  }

  // Left click on the tiles moves the anchor there. Cesium reports a drag as a
  // separate event, so this does not fire while orbiting.
  const picker = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
  picker.setInputAction((click: { position: Cesium.Cartesian2 }) => {
    const hit = viewer.scene.pickPosition(click.position);
    if (!Cesium.defined(hit)) return;
    if (measuring) {
      measured.push(hit);
      if (measured.length === 1) {
        el('measureOut').textContent = 'click the second point';
      } else {
        ruler.show = true;
        const d = Cesium.Cartesian3.distance(measured[0], measured[1]);
        el('measureOut').textContent = `${d.toFixed(2)} m between the two picks`;
        measured.length = 0;
        measuring = false;
        el('measure').textContent = 'Measure';
      }
      return;
    }
    const carto = Cesium.Cartographic.fromCartesian(hit);
    anchor.lat = Cesium.Math.toDegrees(carto.latitude);
    anchor.lon = Cesium.Math.toDegrees(carto.longitude);
    anchor.height = carto.height;
    anchorCartesian = Cesium.Cartesian3.fromDegrees(anchor.lon, anchor.lat, anchor.height);
    enuToFixed = Cesium.Transforms.eastNorthUpToFixedFrame(anchorCartesian);
    apply();
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

  /**
   * Exactly the shape OpenVPS stores in transform.json.
   *
   * Note what it does with it. `MapTransformInfo` premultiplies a +90 degree
   * rotation about X — its `graphics_to_robotics_transform` — on the way in,
   * because the matrix MapAligner writes is map-to-*graphics* (X right, Y up,
   * Z backwards), not map-to-ENU. This tool works in ENU, so it has to undo
   * that rotation before writing, or the localizer applies it a second time
   * and every GeoPose comes out rotated 90 degrees about east, with nothing
   * failing loudly enough to notice.
   */
  function transform() {
    // Inverse of the localizer's graphics->robotics rotation.
    const roboticsToGraphics = new Cesium.Matrix4(
      1, 0, 0, 0,
      0, 0, 1, 0,
      0, -1, 0, 0,
      0, 0, 0, 1
    );
    const stored = Cesium.Matrix4.multiply(
      roboticsToGraphics, mapToEnu(), new Cesium.Matrix4());
    // Cesium stores Matrix4 column-major; transform.json wants rows.
    const flat = Cesium.Matrix4.toArray(stored);
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
  bind('scale', 'scale');

  /**
   * A ruler. Click two points and it reports the distance between them, on
   * whatever was picked — the cloud or the tiles. Measuring the same feature
   * in both is how you settle whether the map's scale is right, rather than
   * judging it by eye, where perspective makes a cloud that sits behind
   * something look smaller than it is.
   */
  const measured: Cesium.Cartesian3[] = [];
  const ruler = viewer.entities.add({
    polyline: {
      positions: new Cesium.CallbackProperty(() => measured.slice(0, 2), false),
      width: 3, material: Cesium.Color.YELLOW,
      clampToGround: false, arcType: Cesium.ArcType.NONE
    },
    show: false
  });
  let measuring = false;
  el('measure').addEventListener('click', (e) => {
    measuring = !measuring;
    measured.length = 0;
    ruler.show = false;
    (e.target as HTMLButtonElement).textContent = measuring ? 'Measuring… (click 2)' : 'Measure';
    el('measureOut').textContent = measuring ? 'click two points' : '';
  });

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
    for (let i = 0; i < far.length; i += 1) far.get(i).pixelSize = px + 2;
  });

  el('onTop').addEventListener('click', (e) => {
    const b = e.target as HTMLButtonElement;
    const on = b.dataset.on !== 'false';
    b.dataset.on = on ? 'false' : 'true';
    b.textContent = on ? 'Points: depth-tested' : 'Points: on top';
    const v = on ? 0 : Number.POSITIVE_INFINITY;
    for (let i = 0; i < points.length; i += 1) points.get(i).disableDepthTestDistance = v;
    for (let i = 0; i < far.length; i += 1) far.get(i).disableDepthTestDistance = v;
  });

  el('layerNear').addEventListener('click', (e) => {
    points.show = !points.show;
    (e.target as HTMLButtonElement).textContent =
      points.show ? 'Near (LiDAR): on' : 'Near (LiDAR): off';
  });
  el('layerFar').addEventListener('click', (e) => {
    far.show = !far.show;
    (e.target as HTMLButtonElement).textContent =
      far.show ? 'Sparse (all range): on' : 'Sparse (all range): off';
  });

  el('toggleTiles').addEventListener('click', (e) => {
    if (!tileset) return;
    tileset.show = !tileset.show;
    (e.target as HTMLButtonElement).textContent =
      tileset.show ? 'Hide 3D tiles' : 'Show 3D tiles';
  });

  el('view').addEventListener('click', (e) => {
    const b = e.target as HTMLButtonElement;
    const next = b.dataset.mode === 'street' ? 'overhead' : 'street';
    b.dataset.mode = next;
    b.textContent = next === 'street' ? 'Overhead view' : 'Street view';
    viewFrom(next as 'street' | 'overhead');
  });

  el('recentre').addEventListener('click', () => {
    viewFrom((el<HTMLButtonElement>('view').dataset.mode as 'street' | 'overhead') ?? 'street');
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
