# SpatialDDS Web Demo (Cesium Option C)

## Setup
```bash
cd web
npm install
npx playwright install --with-deps
```

## Configure Cesium ion Token (Optional, Recommended)
Create `web/.env.local` and add:
```bash
VITE_CESIUM_ION_TOKEN=your_token_here
```
Then restart the dev server. This enables OSM Buildings and World Terrain when available.

## Run
```bash
npm run dev
```

## DDS Bridge (Optional)
By default the UI will try to connect to `http://localhost:8088` and fall back
to mock mode if the bridge is offline. To override the bridge URL, add to
`web/.env.local`:
```bash
VITE_SPATIALDDS_BRIDGE_URL=http://localhost:8088
```

## Photorealistic 3D Tiles (Cesium ion)
Provide a Cesium ion asset ID to enable the toggle in the UI:
```bash
VITE_CESIUM_ION_ASSET_ID=YOUR_ASSET_ID
```
The button **Photorealistic 3D Tiles** will load the tileset on demand.

## DDS Bridge (Docker, Recommended)
Run the SpatialDDS bridge stack in Docker (no host Cyclone DDS bindings needed):
```bash
cd ..
./run_bridge_server_docker.sh
```
Then start the web app and confirm the badge shows **DDS Bridge**:
```bash
cd web
npm run dev
```

Stop the bridge when done:
```bash
cd ..
./stop_bridge_server_docker.sh
```

## Build
```bash
npm run build
npm run preview
```

## Test
```bash
npm test
```

Notes:
- No Cesium ion token is required for this baseline.
- Later we can add 3D Tiles or buildings via ion token or OSM buildings once stable.
- The orange circles are billboard markers; the orange boxes are 3D box entities for the same items.
