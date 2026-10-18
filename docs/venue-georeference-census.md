# The venue's own georeference: census before the batch

**The question.** The Open World Model's data plane is fully 1.8 — zero 1.7
imports across the fifteen files in the lane, no announces, nothing 1.7
published. But the demo is not *standalone*: every entity's placement on the
globe depends on a transform published by `ar_demo/spatialdds_catalog_server.py`
inside a 1.7 `Announce`, read by the page through the 1.7 bridge's
`GET /v1/frames`. With the AR stack down, the page logs `model:unresolved` for
every entity and nothing lands.

**The ruling, 2026-10-18.** The venue publishes its own georeference. The party
that declares a frame is the natural authority for where that frame sits:
`model_service` names `map/ut-littlefield-fountain`, so the claim *where
ut-littlefield-fountain is on the earth* is the scene's to make, on the scene's
own frames lane. The catalogue server's transform is a **different claim** —
where its *map* sits — which coincides today and keeps publishing. Nothing
retires on the 1.7 side: that announce is not wrong, it is just not the venue's
voice. Two services disagreeing about where the venue is becomes
*representable* rather than forbidden — the two-ponds philosophy extended to
georeferencing — with one discipline on the consumer: **the page states its
precedence rather than silently picking or blending.**

---

## 1 · Every site that resolves a frame

A frame is *resolved* when an `fqn` is turned into a transform and used to move
a pose between the venue frame and earth-fixed. Constructing a `FrameRef` is
not resolving one, so `venue_frame()` and its callers are excluded.

**The browser is the only resolver in the repository.** That is the census's
most load-bearing finding, and it makes the blast radius small.

| site | what it does | reads |
| --- | --- | --- |
| `web/src/spatialdds_bridge.ts` `bridgeFrames()` | fetches `/v1/frames` from the **1.7 bridge** | the whole map |
| `web/src/spatialdds_bridge.ts` `localInFrame()` | earth-fixed → venue-local (the tap-to-send path) | `frame.pose.t`, `frame.pose.q` |
| `web/src/spatialdds_bridge.ts` `resolveInFrame()` | venue-local → geodetic (placement) | `frame.pose.t`, `frame.pose.q` |
| `web/src/spatialdds_bridge.ts` `modelEntityToItem()` (×1) + catalogue paths (×2) | pass a frame map into `resolveInFrame` | — |
| `web/src/app.ts` `bootstrapModel()` | `lastFrames = frames`, wholesale | — |
| `web/src/app.ts` `handleDiscover()` | `lastFrames = { ...response.frames, ...lastFrames }` — **already a precedence**, existing wins | — |
| `web/src/app.ts` `sendRobotTo()` | `localInFrame(lastFrames[fqn], …)` | one frame by fqn |
| `web/src/app.ts` pose-update path | `resolveInFrame(lastFrames[fqn], pose)` | one frame by fqn |

**Both resolvers read only `frame.pose.t` and `frame.pose.q`.** Neither reads
`from` or `to`. That is why the read-path cost below is one field rename rather
than a rewrite.

**Not resolvers, checked and excluded:** `robot_tier/keepout_node.py` and
`plaza_sim_node.py` work entirely in the local map frame and never touch
earth-fixed; `spatialdds_demo/discovery_http.py` passes `transforms` through
into a 1.7 manifest without resolving them; the Python tests construct
`transforms=[]`. No tool under `tools/` resolves a frame.

## 2 · What the venue publishing `FrameTransform` costs

### 2.1 The publisher — `spatialdds_demo/model_service.py`

One latched writer on a new topic. **Measured, not assumed:** the record
builds, serialises and round-trips today.

```
FrameTransform(earth-fixed->map/ut-littlefield-fountain)  ->  349 bytes
round-trip: child = map/ut-littlefield-fountain, scale = 1.0 m
cov discriminator preserved -> CovarianceType.COV_NONE
```

Three details that would otherwise cost an afternoon each:

- **`FrameRef` lives in `spatial.common`, not `spatial.core`.** `spatial.core`
  re-exports it as an annotated alias, and the failure mode is nastier than a
  missing name: `callable()` reports **True**, and *calling* it raises
  `TypeError: 'ForwardRef' object is not callable` from inside `typing.py`.
  So the import succeeds, a guard that checks callability passes, and the
  error names neither the cause nor the fix. Import it from
  `spatial.common`, as `model_service.py` already does.
- **`cov` is a union** and must be constructed explicitly as
  `CovMatrix(none=0)` — the `COV_NONE` arm. There is no implicit absent value.
- **The direction is not the announce's direction.** The 1.7 announce says
  `from = map`, `to = earth-fixed`, with the pose giving the map origin *in*
  earth-fixed. `core::FrameTransform` says `parent_ref` = the global frame and
  `child_ref` = the local one, with `T_parent_child` being parent→child. So
  **`parent_ref` = earth-fixed, `child_ref` = the venue frame**, and the pose
  numbers carry over unchanged. A naive `from→parent, to→child` mapping
  inverts the transform and places the venue at the centre of the earth.

The `transform_id` is the key, and `earth-fixed->map/ut-littlefield-fountain`
is the natural spelling — the IDL's own comment suggests exactly this form.

QoS is `MODEL_LATCHED` (RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1) per key): a
georeference is state, and a tab opened five minutes from now must be handed it
without asking. Reusing the profile rather than adding one keeps the registry
honest; the name is about the lane's shape, not its subject.

**A gain worth naming.** The venue's `child_ref` is a 1.8 eight-member
`FrameRef`, so it carries `has_scale / scale_status / meters_per_unit /
display_unit` — the venue frame says *on the wire* that it is metric and that
the scale is `SCALE_DECLARED`. The 1.7 announce's four-member `FrameRef`
structurally cannot say this. So the scene's voice is not merely a second copy;
it carries a claim the old route has no room for.

### 2.2 The binding lane — `bridges/web_bridge/binding.py`

**One row in `LANES`, and nothing else.**

```python
(TOPIC_FRAME_TRANSFORM_V1, FrameTransform, MODEL_LATCHED,
 "spatial::core::FrameTransform", "spatial.core.FrameTransform"),
```

Everything else is already in place, which is the point of having censused it:

- **No `ACCESS` override.** The package's `gateway.DURABILITY` already carries
  `spatial.core.FrameTransform -> (True, 'public-cacheable')`. Latched and
  public by default — correct under N.4, since a georeference is configured
  geometry, and it means the page reads it with **no credential**, which is the
  only way a cross-origin page can read it at all (blocker B).
- **No closure change.** `CLOSURE` is already
  `["types.idl", "core.idl", "provisional/owm.idl"]`, and `FrameTransform`
  lives in `core.idl`.
- **No path work.** The topic
  `spatialdds/ut-austin-littlefield/frames/frame_transform/v1` maps to
  `/ut-austin-littlefield/frames/frame_transform/v1` by the pump's existing
  `topic.split("/")[2:]`, and `frames` is already the stream segment the
  reference gateway uses for this kind of resource (its own fixture serves
  `/…/frames/geo_anchor/v1`). The ruling's "exactly where the path layout
  already expects it" is confirmed.
- **No package release.** Nothing in `spatialdds-web` changes.

`topics.py` gains `TOPIC_FRAME_TRANSFORM_V1 = f"spatialdds/{SCENE}/frames/frame_transform/v1"`.

### 2.3 The page read-path — `web/src/spatialdds_binding.ts`, `web/src/app.ts`

The served JSON, taken through the binding's own decoder and projector rather
than predicted:

```json
{ "transform_id": "earth-fixed->map/ut-littlefield-fountain",
  "parent_ref": { "fqn": "earth-fixed", … },
  "child_ref":  { "fqn": "map/ut-littlefield-fountain",
                  "has_scale": true, "scale_status": "SCALE_DECLARED",
                  "meters_per_unit": 1.0, "display_unit": "m" },
  "T_parent_child": { "t": [-742398.49, -5462355.28, 3197669.65],
                      "q": [0.4967, -0.0336, -0.0585, 0.8653] },
  "stamp": { "sec": "1792334700", "nanosec": 0 },
  "cov": { "type": "COV_NONE" } }
```

Two N.1 rules visible in it and worth expecting: `stamp.sec` is a decimal
**string** because int64 is, and the union is tagged on `"type"`.

So `bindingFrames()` is a `GET` plus an adapter into the shape the two
resolvers already read, keyed by the **child** frame's fqn:

```ts
frames[ft.child_ref.fqn] = { pose: ft.T_parent_child,
                             from: ft.child_ref, to: ft.parent_ref,
                             stamp: ft.stamp };
```

`from`/`to` are carried even though nothing reads them, because the map is
inspected in the debug pane and a half-filled row invites the reading that the
rest is missing rather than unused.

No change to `localInFrame`, `resolveInFrame`, `modelEntityToItem`, or any
caller: they read `.pose` and are untouched by where it came from.

## 3 · The 1.7 announce keeps publishing, and the coexistence is observable

**Nothing retires.** `ar_demo/spatialdds_catalog_server.py` is not edited, its
`Announce.transforms` continues to carry its own transform, and `server.py`'s
`GET /v1/frames` continues to serve the view over it. Verified against the
running stack at the time of writing:

```
GET :8088/v1/frames   ->  map/ut-littlefield-fountain -> earth-fixed   (1.7 announce)
GET :8089/ut-austin-littlefield/frames/frame_transform/v1  ->  404     (lane not built yet)
```

After the batch both answer, and **the two claims are comparable rather than
merely coexistent**, because both derive the frame's identity the same way —
UUIDv5 of the fqn. The live announce and the record built in §2.1 agree
exactly:

```
map frame     faab95ac-b317-5b3b-8c72-2a1191a7f7e0   (both)
earth-fixed   00cc62ae-c21e-5d3b-b5f9-2587a084ba31   (both)
```

So "two services disagree about where the venue is" is a statement about two
poses for **one identified frame**, which a consumer can detect and report. It
is not two unrelated rows that happen to share a name. That is what makes the
disagreement representable in the sense the ruling intends.

## 4 · The page's precedence rule, stated where it lives

**The rule.** For the scene's own entities, the scene's own frames lane wins.
The 1.7 announce is a fallback, not an input to a blend.

**Where it lives:** the two `lastFrames` assignments in `web/src/app.ts`.
There is already a precedence there — `handleDiscover` merges the catalogue's
frames *under* what is held, existing winning — so this is a third source
joining an existing order, not a new concept. The order becomes, highest
first:

1. the scene's frames lane, over N.3 from the binding
2. whatever is already held
3. the catalogue's announce, via `/v1/frames` on the 1.7 bridge

Written as the merge `{ ...bridgeFrames, ...bindingFrames }`, which is the
rule and not an accident of argument order — so it gets a comment saying so,
because a reader cannot tell a deliberate spread from a careless one.

**And the disagreement is logged rather than swallowed.** When both sources
offer the same fqn and the poses differ beyond a tolerance, the page says so
once — which frame, how far apart, which it used. Silence here would be the
blend the ruling forbids, wearing different clothes: a page that quietly
prefers one source looks identical to a page that got only one.

## 5 · The batch

Three pieces, in order, each landing with the one before it still working:

1. **`model_service.py` publishes `FrameTransform`** on
   `spatialdds/{scene}/frames/frame_transform/v1`, latched, parent =
   earth-fixed, child = `venue_frame()`, `cov = COV_NONE`. Numbers as the
   catalogue announces them today, so the two claims coincide on landing and
   the disagreement path is reachable only deliberately. `topics.py` gains the
   constant.
2. **`binding.py` serves the lane** — one `LANES` row, no `ACCESS` override,
   no closure change, no release.
3. **The page reads it first** — `bindingFrames()` in `spatialdds_binding.ts`,
   the precedence comment and the disagreement log in `app.ts`.

**Acceptance: the OWM view places every entity with the AR stack down.** The
standalone check is the whole point, so the proof is the stack *not* running —
`SPATIALDDS_MODEL_LAYER=1` with the VPS and catalogue servers stopped, every
entity drawn, and `model:unresolved` absent from the log. A passing run with
the AR stack up proves nothing new.

Expected casualty in that configuration, and not a regression: the camera has
no geopose, because localization is the VPS's. The check is about *placement*
given a pose, so the acceptance run sets the view directly, the way
`web/tests/model-stack.helpers.ts` `look()` already does.

## 6 · What stays gated, and on what

**Localization waits for OpenVPS to migrate to 1.8 — and that is a DDS
migration like any other, not a specification question.** OpenVPS's SpatialDDS
integration is DDS-native: the camera's localize path is browser →
`/v1/localize` → a 1.7 `VpsRequest` on the bus → the VPS → a 1.7 `VpsResult`
back. Those types move to 1.8 the way the world-model types did, in that
project's own batch. Nothing in this repository can do it and nothing in the
specification blocks it.

**Two things this census conflated in an earlier draft, kept apart here
because routing them together would send a batch to the wrong queue:**

| | what it is | where it goes |
| --- | --- | --- |
| the camera's localize path | the **DDS leg** — 1.7 `VpsRequest`/`VpsResult` between the bridge and the VPS | OpenVPS's 1.8 migration |
| the AR demo's HTTP routes | `/v1/localize` and friends on `server.py`, which have no Appendix N shape — N.3 is resources, N.5 subscriptions, N.6 commands | finding 1 of `docs/bridge-1.8-census.md` |
| 1.9 candidate entry 5 | a service profile replacing **GPP**, OpenVPS's separate HTTP face | the spec agent; changes nothing about the DDS integration |

So entry 5 is not a gate on anything here. It was cited as one in a first
draft of this section, which would have made the OWM demo's last dependency
look like it was waiting on a specification when it is waiting on a sibling
project's type migration — a much more ordinary thing, and one with a known
shape.

### What that migration will cost, since it was cheap to measure here

Not this batch's work, recorded because the question came up while censusing
and the answer is the same shape as the world model's was.

`spatial.argeo`'s module surface is **identical** between the two eras — same
eleven names, and `VpsRequest` and `VpsResponse` have the same member counts
(10 and 9). A member-level diff therefore reports "no change", and that
reading is wrong: **a borrowed type grew underneath unchanged names.**

```
spatial.common.FrameRef    4 -> 8 members
                           + has_scale, scale_status, meters_per_unit, display_unit
spatial.core.PoseSE3       unchanged
spatial.core.CovMatrix     unchanged
```

`VpsRequest.client_frame_ref` is a `FrameRef`, and `VpsResponse.node_geo`
carries a `FramedPose` whose `frame_ref` is another one. So every construction
site of either type must supply the scale quartet, exactly as the OWM
migration's did — the consequence
`docs/owm-1.8-census.md` recorded as deciding its packaging, arriving again on
a different lane. In this repository that is four files:
`bridges/web_bridge/server.py`, `spatialdds_demo/dds_transport.py`,
`spatialdds_test.py`, and whatever `ar_demo` builds through them.

**A method note, because the first attempt at that diff lied.** Reading
`__annotations__` off `spatial.argeo.FrameRef` reports **zero members** in
both eras and so prints "same" — because that name is an annotated re-export,
not the definition. The real type is `spatial.common.FrameRef`, and resolving
it there is what showed the 4→8 growth. It is the same alias trap as §2.1's
`TypeError`, in a different costume: there it broke loudly, here it returned a
confident wrong answer. Diff types at their definitions, never at a
re-export.

After this batch, the DDS leg of localization is **the only 1.7 thing the OWM
demo needs, and only when a camera wants to join.** The venue, its entities,
its georeference, the robot, the keep-out, the commands and the browser's
reading of all of it are 1.8 end to end.
