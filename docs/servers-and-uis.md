# The two HTTP servers, and the browser UIs

Moved out of the landing README. Referenced from the **Bridges** and
**Demos** tables there.

## Two HTTP servers — which one do I want?

Both serve the spec's HTTP discovery binding, and both answer it from the same
module (`spatialdds_demo/discovery_http.py`), so they cannot drift. They differ
in where their services come from:

| | `bridges/web_bridge/server.py` | `ar_demo/http_binding.py` |
|---|---|---|
| Role | Gateway: the per-deployment process | Conformance harness and reference implementation |
| Services from | The live DDS bus, via a cached announce feed | An in-memory registry fed by `register` |
| Also serves | `/ws` live streams, `/v1/*`, the canvas dashboard | `register` / `list` |
| Port (default) | 8088 | 8080 |
| Needs DDS? | Yes | No |

Use the bridge when you want discovery over a real bus alongside live data. Use
the HTTP binding to exercise the binding's payload shapes on their own, with a
registry you control.

[`scripts/cold_start.sh`](../scripts/cold_start.sh) walks the whole path against a
running bridge — bootstrap, search a geohash cell, read the topics off a
returned manifest, subscribe on `/ws` — in curl and one `websocket-client`
call, importing nothing from this repo. Transcript in the
[bridge README](../bridges/web_bridge/README.md#cold-start-with-no-spatialdds-client-code).

## Browser UIs

- **AR demo** — 3D Cesium view of VPS coverage, catalog, localisation and anchor
  publication. Served by Vite from [`web/`](../web/README.md); it calls the web
  bridge for data. Setup in [`ar_demo/README.md`](../ar_demo/README.md#cesium-web-ui).
- **Multi-operator fusion** — 2D top-down canvas served by the web bridge itself
  at `http://localhost:8088/`, with a topic-list debug page at `/debug`. Operator
  egos and trails, detection wireframes, planned trajectories, fused tracks,
  conflict markers, live metrics. Setup in
  [`multi_operator_fusion/README.md`](../multi_operator_fusion/README.md#browser-canvas-dashboard).
