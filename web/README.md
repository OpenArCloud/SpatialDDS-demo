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
| `VITE_SPATIALDDS_BINDING_TOKEN` | Needed **only to send a command**, and set it only for that: reading the venue needs nothing, and a credential sent to a lane that does not want one makes the venue come up *empty* rather than merely being ignored (see below). The bridge classifies its model lanes `public-cacheable` per N.4, because what they describe is configured geometry plus a simulated robot and three ducks. Driving the robot is operator-scoped, so Send Robot needs the token the bridge prints in its banner. |
| `VITE_CESIUM_ION_TOKEN` | Enables OSM Buildings and World Terrain. |
| `VITE_CESIUM_ION_ASSET_ID` | Enables the **Photorealistic 3D Tiles** toggle. |

### Running it so that Send Robot works

`Authorization` is not a CORS-safelisted header, so any **cross-origin**
request carrying it preflights with `OPTIONS` — and the binding routes no
`OPTIONS`, answering `405`. A page on `:5173` talking straight to `:8089` is
cross-origin, so its command never leaves the browser. Reading is unaffected:
those lanes are public and the client deliberately sends no credential on
them, which is also why a token on a *read* would break it.

Same-origin requests are not preflighted at all, so the dev server proxies the
scene prefix to the binding (`server.proxy` in `vite.config.ts`, `ws: true`
for N.5's sockets). Point the client at this origin and the whole lane works,
tap-to-send included:

```bash
VITE_SPATIALDDS_BINDING_URL=http://localhost:5173 VITE_SPATIALDDS_BINDING_TOKEN=bridge-session npm run dev
```

Then: click **Send Robot** to arm it — the label flips to `Send Robot: On` —
and *click the ground*. The button is a mode, not a send; arming it alone does
nothing by design. `?debug=1` shows the outcome, `202` or a `409` with its
reason.

Without the proxy (plain `npm run dev`, client pointed at `:8089`) the venue
still loads and still moves — only commanding is blocked. Drive the robot with
`tools/post_goto.py` in that case, keeping targets clear of the declared pond
or the robot correctly refuses.

**The proxy fixes the dev loop and not the gap.** Any build served from an
origin other than the binding's is still blocked, which is a fair guess at why
a missing preflight survives so long in the wild: the dev server hides it.
Measured and filed in
[`docs/browser-credential-finding.md`](../docs/browser-credential-finding.md),
blocker B, and queued for `spatialdds-web`.

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
