# SpatialDDS + OpenVPS

A browser localizes against a visual positioning service it **discovered on the
bus**. Nothing is configured to point at it: the VPS announces itself, discovery
finds it by geography, and the same request works whether the localizer is the
bundled stand-in or a real one on a GPU.

Two ways to run it. The first needs only Docker and Node; the second needs AWS.

---

## A · Local, with the bundled localizer

```bash
./run_bridge_server_docker.sh          # VPS + catalogue + bridge on :8088
cd web && npm install && npm run dev   # → http://localhost:5173/
```

Turn on **REST Messages** and **DDS Messages**, then click **Localize**.

That pairing is the point. A browser using the spec's HTTP binding, with DDS
behind it the whole way — and the two panels side by side show that one click
on the left is a conversation on the right.

Measured on a live stack, one **Localize** produces three REST calls:

```
GET  /.well-known/spatialdds/search?geohash=9v6kr&kind=VPS   ->  200
POST /v1/localize                                            ->  200
GET  /v1/model                                               ->  200
```

and five bus messages:

```
spatialdds/discovery/query/v1                 coverage_query
spatialdds/discovery/replies/bridge-<id>/v1   coverage_response   (the VPS)
spatialdds/discovery/replies/bridge-<id>/v1   coverage_response   (the catalogue)
spatialdds/vps/query/v1                       vps_query
spatialdds/vps/result/v1                      vps_response        VPS_SUCCESS
```

Two things worth pointing at in that list. The search carries `&kind=VPS`, so
the *client* says what it wants and discovery says who provides it — the
browser never holds an endpoint. And two services answer the coverage query;
only one of them is a localizer, which is why the filter is there rather than
the browser taking whatever came back first.

`GET /health` also appears in the REST panel, from page load rather than from
the click.

**What is real here and what is not.** The whole request path is real — a
full-size JPEG chunked onto `spatialdds/blob/chunk/v1`, reassembled and
checksum-verified at the service discovery found. The *pose* is not: the
stand-in returns the prior plus a few metres of jitter and never looks at a
pixel. Sending a different frame changes nothing.

Stop with `./stop_bridge_server_docker.sh`.

## B · Against a real OpenVPS on AWS

Now the pose comes from the pixels: NetVLAD retrieval, SuperGlue matching and
PnP on a T4. Distinct frames give distinct poses — which is the check that
pixels are being used at all, since a stand-in returning prior-plus-jitter
passes every other test. Measured over the six bundled query frames, the poses
span about 5 m of latitude and 22 m of longitude: the walk they were taken on.

A repeated frame comes back to sub-millimetre rather than bit-for-bit (~1e-9
degrees, up to 0.9 mm) — RANSAC inside PnP is not deterministic, so compare
with a tolerance.

First-time setup — deploying the localizer, building or loading a map, pointing
this repo's task at it — is in
[`deploy/aws/README.md`](../deploy/aws/README.md#running-against-a-real-openvps).

### Waking it from stopped

Once deployed, the GPU host lives stopped between demos (it idles itself off
after 30 minutes). Bringing it back is three steps, and **all three are
needed** — this has been the same sequence every time:

```bash
# 1. start the GPU host, wait for status checks + all containers healthy
aws ec2 start-instances --instance-ids <instance-id>

# 2. load the map. Nothing loads one at boot, and a localizer holding no map
#    neither localizes nor announces, so discovery reports no VPS at all.
python3 scripts/openvps_prepare_map.py --instance <instance-id> \
    --dataset <dataset-id> --map <map-id> --load --verify

# 3. redeploy the demo task. Its DDS participant will not pick up a peer that
#    was absent when it started, so a task that outlived the last shutdown
#    never finds the localizer.
aws ecs update-service --cluster spatialdds-demo-cluster \
    --service spatialdds-demo-service --force-new-deployment
```

Then confirm discovery actually returns it, from the geohash cell containing
the map's anchor:

```bash
curl "$BASE/.well-known/spatialdds/search?geohash=<cell>"
# → svc:vps:oarc/<name>;v=<map-id>
```

If that shows only the catalogue, step 3 has not taken effect yet — give the
new task ~30 s after `/health` returns 200 and query again.

### Two things that surprise people

**The page will not auto-localize.** Against the demo's own mock it localizes
on load; against a real VPS it deliberately holds and waits for a click. The
discriminator is the announced id — anything that is not `svc:vps:demo/…` is
treated as somebody else's service, and a real localizer should not receive a
request per page refresh. The app log says so:

```
autostart: held — svc:vps:oarc/… is not the demo's own; press Localize to send a real request
```

**Use "Localize with Image", not "Localize".** Plain *Localize* sends the
rendered Cesium view, which no VPS can match against a map — it exercises the
blob lane with real bytes and gets a correct `VPS_FAILED` back. *Localize with
Image* sends an actual photograph from the scan the map was built from, and is
the only button that produces a pose from pixels.

It needs the query-frame bundle, and it is disabled without one. The bundle is
gitignored, so:

- **the deployed demo has it** if whoever ran `deploy.sh` had it installed —
  the image carries `web/public/query-frames/` deliberately, see `.dockerignore`;
- **a local page** needs it in `web/public/query-frames/`; see
  [`ar_demo/README.md`](../ar_demo/README.md#localize-with-image).

Measured against the real localizer, all six bundled frames: `VPS_SUCCESS`,
4.5-5.2 s each, poses spanning ~5 m of latitude and ~22 m of longitude. Numbers
in [`deploy/aws/maps/fountain/baseline-1a608af.md`](../deploy/aws/maps/fountain/baseline-1a608af.md).

### Shutting down

```bash
aws ec2 stop-instances --instance-ids <instance-id>
```

The maps volume is retained, so the map survives; it just needs `--load` again
on the next wake. The private IP is stable across stop/start, so the demo task's
peer configuration stays valid.
