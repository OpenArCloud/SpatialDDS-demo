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

/**
 * The scene the binding serves, and the one path prefix worth proxying.
 *
 * Mirrors SCENE in spatialdds_demo/topics.py and web/src/spatialdds_binding.ts.
 * Only this prefix goes to the binding: the AR demo's routes and
 * `/.well-known/spatialdds/*` belong to `server.py` on another port and are
 * reached by absolute URL, so proxying more than this would capture them.
 */
const SCENE = process.env.VITE_SPATIALDDS_SCENE || 'ut-austin-littlefield';
const BINDING = process.env.VITE_SPATIALDDS_BINDING_ORIGIN
  || 'http://127.0.0.1:8089';

export default defineConfig({
  base,
  define: {
    CESIUM_BASE_URL: JSON.stringify(`${base}cesium/`)
  },
  /**
   * Put the binding on the dev server's own origin.
   *
   * **Why this exists, since it looks like a convenience and is not.** A
   * command needs a credential, `Authorization` is not CORS-safelisted, so a
   * cross-origin POST carrying one preflights --- and the binding routes no
   * `OPTIONS`, answering `405 Allow: GET,HEAD,POST`. The browser refuses
   * before the request is made, so **tap-to-send cannot work from a page on
   * :5173 talking directly to :8089**, and nothing in the page can change
   * that. See docs/browser-credential-finding.md, blocker B.
   *
   * Same-origin requests are not preflighted at all, so proxying the scene
   * prefix through this server sidesteps the gap for development: the browser
   * sees one origin, and Vite forwards to the binding from Node, where CORS
   * does not apply. Point the client at this origin to use it:
   *
   *   VITE_SPATIALDDS_BINDING_URL=http://localhost:5173    *   VITE_SPATIALDDS_BINDING_TOKEN=bridge-session npm run dev
   *
   * `ws: true` matters as much as the HTTP half --- N.5 is one WebSocket per
   * resource under the same prefix, and an unproxied upgrade would leave the
   * venue static while state loaded fine, which is a confusing half-failure.
   *
   * **This does not retire the gateway finding.** It fixes the demo's dev
   * loop and leaves every other deployment shape --- a built bundle served
   * from anywhere but the binding's own origin --- still blocked. It is also
   * a fair guess at why a missing preflight survives so long in the wild: the
   * dev server hides it, and only a cross-origin deployment finds out.
   */
  server: {
    proxy: {
      [`/${SCENE}`]: { target: BINDING, changeOrigin: true, ws: true }
    }
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
