# SpatialDDS web demo (Cesium)

3D Cesium view of the AR demo: VPS coverage, catalog content, localisation and
anchor publication. It talks to the [web bridge](../bridges/web_bridge/README.md)
over HTTP and falls back to in-page mocks when no bridge answers.

## Setup

```bash
cd web
npm install
npx playwright install --with-deps   # only needed for the tests
npm run dev
```

## Configuration

All optional. Put them in `web/.env.local`:

| Variable | Effect |
|---|---|
| `VITE_SPATIALDDS_BRIDGE_URL` | Bridge to talk to for the AR demo's routes — localize, catalogue, frames, health, discovery. Defaults to `http://localhost:8088`. |
| `VITE_SPATIALDDS_BINDING_URL` | The 1.8 web binding, which serves the model lanes. Defaults to `http://localhost:8089`. Two variables because they are two processes: `bridges/web_bridge/server.py` and `bridges/web_bridge/binding.py`. |
| `VITE_SPATIALDDS_BINDING_TOKEN` | Needed **only to send a command**. Reading the venue needs nothing: the bridge classifies its model lanes `public-cacheable` per N.4, because what they describe is configured geometry plus a simulated robot and three ducks. Driving the robot is operator-scoped, so Send Robot needs the token the bridge prints in its banner. |
| `VITE_CESIUM_ION_TOKEN` | Enables OSM Buildings and World Terrain. |
| `VITE_CESIUM_ION_ASSET_ID` | Enables the **Photorealistic 3D Tiles** toggle. |

Restart the dev server after changing them.

## Running against a real DDS bus

The bridge stack runs in Docker, so no host Cyclone DDS bindings are needed:

```bash
cd .. && ./run_bridge_server_docker.sh   # VPS + catalog + bridge on :8088
cd web && npm run dev
```

The badge in the top-left should read **DDS Bridge**; if it says **Mock Mode**,
the app could not reach the bridge. Stop the stack with
`./stop_bridge_server_docker.sh`; logs land in `bridges/web_bridge/logs/`.

The launcher seeds the VPS and catalog for Austin, matching the app's default
view. Pointing it at the San Francisco seed (`ar_demo/catalog_seed.json`)
instead will return zero catalog items here, which is correct behaviour rather
than a fault.

## Build

```bash
npm run build
npm run preview
```

## Test

```bash
npm test
```

Two specs, and exactly one runs in any given environment:

- `tests/smoke.spec.ts` drives the app in mock mode, and skips if a bridge is up.
- `tests/live-bridge.spec.ts` drives it against a real DDS bridge, and skips if
  none answers. Start the stack as above, then
  `npx playwright test tests/live-bridge.spec.ts`.

On Docker Desktop for macOS, publish the bridge port rather than relying on
`--network host`, which does not reach host loopback. The launcher already does
this.
