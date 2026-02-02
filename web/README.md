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
