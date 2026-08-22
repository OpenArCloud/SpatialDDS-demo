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

`tests/smoke.spec.ts` runs the app in mock mode. `tests/live-bridge.spec.ts`
drives it against a **real** DDS bridge and skips itself when no bridge answers
on `VITE_SPATIALDDS_BRIDGE_URL`, so the default run stays hermetic.

To run the live 1.7 smoke, bring up a stack seeded for the app's default
location (Austin) and publish the bridge on port 8088:

```bash
docker run -d --name sdds-smoke -p 8088:8088 -v "$PWD:/app" -w /app \
  -e PYTHONPATH=/app -e SPATIALDDS_TRANSPORT=dds -e SPATIALDDS_DDS_DOMAIN=1 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  -e SPATIALDDS_CATALOG_SEED=/app/bridges/web_bridge/tests/catalog_seed_austin.json \
  -e SPATIALDDS_VPS_COVERAGE_BBOX="-97.75,30.27,-97.72,30.29" \
  -e SPATIALDDS_VPS_MAP_FQN=map/austin -e SPATIALDDS_VPS_MAP_ID=austin-map \
  cyclonedds-python bash -lc '
    python3 -m pip install -q -r requirements.txt -r bridges/web_bridge/requirements.txt
    python3 ar_demo/spatialdds_demo_server.py &
    python3 ar_demo/spatialdds_catalog_server.py &
    python3 bridges/web_bridge/server.py &
    sleep 3600'

cd web && npx playwright test tests/live-bridge.spec.ts
docker rm -f sdds-smoke
```

Two things to know about this setup:

- The default `ar_demo/catalog_seed.json` is **San Francisco** content, while
  the web app starts over Austin — pair the Austin seed with the Austin VPS
  bbox or discovery legitimately returns zero items.
- On Docker Desktop for macOS, `--network host` does not expose container
  ports to host loopback, so the browser cannot reach the bridge that way.
  Publishing `-p 8088:8088` works because all the DDS processes share one
  container and only HTTP has to cross the boundary.

Notes:
- No Cesium ion token is required for this baseline.
- Later we can add 3D Tiles or buildings via ion token or OSM buildings once stable.
- The orange circles are billboard markers; the orange boxes are 3D box entities for the same items.
