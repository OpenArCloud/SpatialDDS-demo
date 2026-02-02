import fs from 'node:fs';
import path from 'node:path';
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

export default defineConfig({
  define: {
    CESIUM_BASE_URL: JSON.stringify('/cesium/')
  }
});
