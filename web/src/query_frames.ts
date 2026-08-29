/**
 * Real query imagery, for localizing against a real VPS.
 *
 * The Localize button sends the rendered Cesium view (see `captureQueryImage`
 * in app.ts). That is honest about being synthetic: real bytes of a realistic
 * size, which exercises the blob lane, but no real VPS could match it against
 * a map. This is the other half — actual photographs from the scan a VPS map
 * was built from, so the localizer has something it can genuinely register.
 *
 * **Nothing ships in the repository.** Query frames are photographs of a real
 * place and the manifest carries that place's coordinates, so both are
 * installed locally and git-ignored rather than committed. Drop a bundle into
 * `web/public/query-frames/` and the button turns itself on; with no bundle
 * present the app says so and the button stays disabled. See ar_demo/README.md.
 *
 * **Why frames from the map, rather than an upload button.** A SpatialDDS 1.7
 * `VpsRequest` has nowhere to carry camera intrinsics. OpenVPS's DDS binding
 * says so in `maplocalizer/server/main.py` and falls back to the map's own
 * camera model, which is right for query images drawn from the map and wrong
 * for a foreign camera. Their notes record that failing at 9.6 m off while
 * still returning VPS_SUCCESS — plausible, wrong, and silent. An upload
 * control would invite exactly that, so the frames are ones whose intrinsics
 * the map already knows. The missing intrinsics field is a 1.8 gap, not
 * something to paper over in the client.
 */

/** Where the installed bundle lives, relative to the app's base path. */
const FRAMES_DIR = 'query-frames';

/** `query-frames/manifest.json`, describing the installed bundle. */
export interface QueryFrameManifest {
  /** Geodetic anchor from the map's `transform.json`. */
  anchor: { lat_deg: number; lon_deg: number; alt_m: number };
  /** Frame file names, in the order the button cycles them. */
  frames: string[];
  /** Optional provenance, shown in the log so a viewer knows what answered. */
  label?: string;
  mapId?: string;
  datasetId?: string;
}

function manifestUrl(): string {
  return `${import.meta.env.BASE_URL}${FRAMES_DIR}/manifest.json`;
}

function frameUrl(file: string): string {
  return `${import.meta.env.BASE_URL}${FRAMES_DIR}/${file}`;
}

/**
 * The installed bundle, or null when none is installed.
 *
 * Null rather than throwing: no bundle is the normal state of a fresh clone,
 * not an error. Anything malformed *is* an error and throws, so a broken
 * manifest is not silently indistinguishable from an absent one.
 */
export async function loadQueryFrameManifest(): Promise<QueryFrameManifest | null> {
  let response: Response;
  try {
    response = await fetch(manifestUrl());
  } catch {
    return null;
  }
  if (!response.ok) {
    return null;
  }
  // A dev server answers a missing file with index.html and HTTP 200, so
  // `response.ok` is not evidence that JSON came back.
  let parsed: unknown;
  try {
    parsed = await response.json();
  } catch {
    return null;
  }
  const manifest = parsed as QueryFrameManifest;
  if (!manifest || !Array.isArray(manifest.frames) || manifest.frames.length === 0) {
    throw new Error('query-frames/manifest.json: no frames listed');
  }
  const a = manifest.anchor;
  if (!a || typeof a.lat_deg !== 'number' || typeof a.lon_deg !== 'number') {
    throw new Error('query-frames/manifest.json: anchor needs lat_deg and lon_deg');
  }
  return manifest;
}

/**
 * Fetch one frame and return it base64-encoded, matching what
 * `captureQueryImage` produces so both paths hand `bridgeLocalize` the same
 * kind of value.
 */
export async function loadQueryFrame(file: string): Promise<string> {
  const response = await fetch(frameUrl(file));
  if (!response.ok) {
    throw new Error(`${file}: HTTP ${response.status}`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  // Same trap as the manifest: a dev server's SPA fallback returns index.html
  // with HTTP 200. Without this the app base64s an HTML page and sends it as
  // query imagery, which the VPS reassembles, fails to decode, and never
  // explains. Checking the SOI marker fails where the mistake is.
  if (bytes.length < 3 || bytes[0] !== 0xff || bytes[1] !== 0xd8 || bytes[2] !== 0xff) {
    throw new Error(
      `${file}: not a JPEG (${bytes.length} bytes, ` +
      `content-type ${response.headers.get('content-type') ?? 'unknown'})`
    );
  }
  let binary = '';
  // Chunked because `String.fromCharCode(...bytes)` overflows the argument
  // limit on a frame of this size.
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}
