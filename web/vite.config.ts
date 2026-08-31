import fs from 'node:fs';
import path from 'node:path';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

const cesiumSource = path.resolve(__dirname, 'node_modules/cesium/Build/Cesium');
const cesiumPublic = path.resolve(__dirname, 'public/cesium');
const cesiumMarker = path.resolve(cesiumPublic, 'Assets');

function ensureCesiumAssets() {
  if (!fs.existsSync(cesiumSource)) {
    return;
  }

  if (!fs.existsSync(cesiumMarker)) {
    fs.mkdirSync(cesiumPublic, { recursive: true });
    fs.cpSync(cesiumSource, cesiumPublic, { recursive: true });
  }
}

ensureCesiumAssets();

/**
 * Where this bundle will be served from.
 *
 * `/` for local development and the root-mounted case; `/ar/` for the
 * deployment, where the bridge serves the fusion dashboard at `/` and mounts
 * this app beside it.
 *
 * It has to be one value feeding both `base` and the Cesium define, because
 * they are two different mechanisms pointing at the same files and nothing
 * makes them agree:
 *
 *  * `base` rewrites the bundle's own asset URLs, and Vite applies it to
 *    `import.meta.env.BASE_URL`.
 *  * `define` textually substitutes the bare identifier `CESIUM_BASE_URL` at
 *    compile time — including inside Cesium's own source, which reads it as a
 *    global to find its workers, web assets and widget images.
 *
 * Setting only `base` left the define at `/cesium/`, so the app's scripts
 * loaded from `/ar/` while Cesium fetched its assets from the site root and
 * 404'd on every one. The globe rendered as a single flat colour with no
 * error that named the cause.
 */
const base = process.env.VITE_BASE_PATH || '/';

export default defineConfig({
  base,
  define: {
    CESIUM_BASE_URL: JSON.stringify(`${base}cesium/`)
  },
  build: {
    rollupOptions: {
      // Two pages: the demo, and the map aligner. Named explicitly because
      // Vite only picks up index.html on its own.
      input: {
        main: resolve(__dirname, 'index.html'),
        aligner: resolve(__dirname, 'aligner.html')
      }
    }
  }
});
