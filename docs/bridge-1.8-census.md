# The web bridge against the 1.8 web binding: a census before changing anything

`bridges/web_bridge` is the last `_PRE18` consumer. This is the enumeration
the directive asks for before any of it moves: what it serves, who consumes
that, and what the browser view — the compatibility bar — actually depends on.

Three findings here change the shape of the batch, and one of them is a
program-level finding rather than a bridge one. They are up front rather than
buried.

## Finding 1: the bridge is mostly not an OWM bridge

It serves **16 routes**. The *app* consumes **8**. Only **3** carry the
lanes this batch is about.

**Amended 2026-10-18, and the amendment is the interesting part.** The first
version of this table had one column, "browser", and counted every `web/`
match into it --- so `web/tests/` arrived as browser evidence. That inflated
every row and got three things wrong: the AR demo's routes are **six**, not
seven; `GET /v1/keepout` had **no app consumer at all**; and `WS /v1/stream`
has **none anywhere** --- its one "browser site" was a sentence in a comment
mentioning the route, not a call to it. The counts below are call sites, found
by reading what `fetchJson` and `new WebSocket` are actually handed, which is
the only thing that makes a route consumed. The ratification of finding 1
stood on the old numbers; better evidence wins.

| route | app | tests | lane | after part 2 |
| --- | --- | --- | --- | --- |
| `GET /v1/model` | 1 | 12 | **OWM downstream** | **retired** |
| `POST /v1/model/command` | 1 | 1 | **OWM upstream** | **retired** |
| `WS /ws` | 2 | yes | **OWM live** (and generic) | kept (generic) |
| `GET /health` | 1 | yes | AR demo | kept |
| `POST /v1/localize` | 1 | yes | AR demo — VPS | kept |
| `POST /v1/catalog/query` | 2 | yes | AR demo — catalogue | kept; **since retired** — the final migration moved the catalogue onto `ContentAnnounce` → manifest (`POST /v1/content/query` + the §7.5 resolver; `final-migration-census.md` §1) |
| `GET /v1/frames` | 2 | yes | AR demo | kept |
| `GET /v1/keepout` | **0** | 1 | robot tier view | **retired** |
| `GET /.well-known/spatialdds/search` (GET+POST) | 1 | yes | discovery | kept |
| `GET /.well-known/spatialdds/bootstrap` | 0 | yes | discovery | kept |
| `WS /v1/stream` | **0** | 0 | raw envelope firehose | kept, unconsumed |
| `GET /`, `GET /debug` | 0 | tests / none | operator | kept |
| `GET /api/topics`, `GET /api/stats` | **0** | 0 | dead? | kept, unconsumed |

`/v1/keepout` is worth a sentence of its own, because its docstring argued for
it: it described itself as the §6.1 door for a ROS process that runs a
different CycloneDDS major and so cannot join the bus in-process. That was
true when it was written and false by the time it was retired ---
`robot_tier/keepout_node.py` subscribes to `TOPIC_MODEL_ENTITY_V1` directly
and calls `build_mask` itself, which the robot batch's migration is what made
possible. **A route's own account of who needs it ages**, and this one aged
into the only argument for keeping a route nothing called.

So "the bridge moves to the web binding" cannot mean *replace this surface*.
Six app-consumed routes are the AR demo's — one-shot RPC for localize and
catalogue queries, frame lookups, health, discovery search, and the generic
`/ws` — and Appendix N has no RPC surface to move them to: N.3 is resources, N.5 is subscriptions, N.6 is commands.
Rewriting them would be migrating the AR demo, which no directive has scoped
and which this one explicitly is not.

**Reading the directive against that:** §3's lanes are the OWM ones —
"model entities, robot pose" down, "a `goto` command" up. So this batch adds
the binding for those three routes' worth of function and retires the three
custom routes, while the AR demo's six keep working unchanged. The bridge
ends up serving its own surface *and* the binding, which is a hybrid worth
naming rather than discovering. If the intent was the whole surface, that is a
much larger batch and the AR demo needs its own census first.

`/api/topics` and `/api/stats` have no consumer anywhere in the tree —
flagged, not touched; deleting them is not this batch's business.

### Those six routes are 1.9 entry 5's concrete input

The AR demo's six are not homeless, they are early. `localize`,
`catalog/query` and `frames` are request/response over a spatial service
(`catalog/query` has since dissolved into 1.8's own content chain; the paste
below is as it was filed),
which is exactly the shape 1.9 candidate entry 5 — the GeoPose Protocol
successor as a service profile — exists to specify. This bridge is the
standing implementation of that shape, so the routes are evidence rather than
debt.

Ready to paste into `spatialdds-web/directions/spatialdds-1.9-candidates.md`
under entry 5, since that repository is read-only here:

```markdown
**Concrete input, 2026-10-17.** `SpatialDDS-demo`'s web bridge already serves
this shape over six routes the browser app actually calls:
`POST /v1/localize`, `POST /v1/catalog/query`, `GET /v1/frames`,
`GET /health`, discovery `search`, and the generic `WS /ws`. (An earlier
draft of this paste listed `GET /v1/keepout` among them; it had no app
consumer and was retired on 2026-10-18.) The bridge's 1.8 census
(`docs/bridge-1.8-census.md`, finding 1) records why they could not migrate to
Appendix N: N.3 is resources, N.5 is subscriptions, N.6 is commands, and none
of them is request/response over a service. They are entry 5's use case with a
running implementation attached.
```

## Finding 2: N.5 is per-resource, the browser subscribes by pattern

The browser opens one socket and asks for a wildcard:

```ts
modelSocket.send(JSON.stringify({
  type: 'subscribe', id: 'model', pattern: 'spatialdds/model/*'
}));
```

Appendix N.5 has no such thing. `subscribe` in the gateway is reached at a
resource path — one socket per resource, each replaying that resource's
latched set and sending exactly one `end_of_replay` marker. There is no
pattern, no multiplexing, and no cross-resource ordering guarantee.

So the browser rewiring is not a URL change. It is either two sockets (entity,
pose) with their own replay markers, or a multiplexer in the client that
re-creates what `/ws` gave it. Two sockets is the honest shape — it is what
the binding models, and the markers then mean something per resource — but it
changes the client's connection lifecycle, not just its endpoint. Stated here
because "point the Vite tab at the binding" sounds like configuration and is
not.

The wildcard also names the **pre-1.8 topics**, so the browser is asking for a
namespace nothing publishes any more. That is the severance seen from the
client side, and the same one line is both the bug and the migration.

## Finding 3 (program-level): four mechanisms for one missing type

The bridge invents its own removal signal, and its reasoning is sound:

```python
# A dispose carries no sample, so there is nothing to forward as an Entity.
# It goes out under its own type rather than as a half-filled one: a client
# that receives `{"entity_id": ...}` shaped like an Entity has to guess
# whether the missing fields mean "gone" or "not sent".
MODEL_ENTITY_DISPOSED_TYPE = "oarc.model_entity_disposed"
MODEL_RELATIONSHIP_DISPOSED_TYPE = "oarc.model_relationship_disposed"
```

That makes **four** removal mechanisms in this program for one absent type,
three of them live:

| # | mechanism | where | reads the others? |
| --- | --- | --- | --- |
| 1 | infer from `state == "RETIRED"` on the terminal sample | `spatialdds-web` gateway recording loader, `gateway.py:338` | no |
| 2 | `spatialdds-web:dispose` MCAP metadata record | `spatialdds-web` journal path, `publish.py:62/269/358` | no |
| 3 | `oarc.model_entity_disposed` WS message type | this bridge, `server.py:824` | no |
| 4 | `oarc.model_relationship_disposed` | same, dead with the type | — |

Four independent inventions, each locally reasonable, none interoperable, in
two repositories by the same program. This is no longer "one implementation
learns removal two ways depending on which door" — it is *every consumer
invents one*, which is what a missing type looks like after enough consumers.
It belongs in
[`removal-two-doors-finding.md`](removal-two-doors-finding.md) as the
strengthening it is, and it is the strongest evidence yet for finding 25's
direction: a typed removal record carrying intent and reason, so there is one
mechanism to implement rather than one per consumer to reconcile.

Filed, not fixed, and `spatialdds-web` stays read-only.

## The option-1 blocker, measured

Carried from the pre-flight report and restated with the evidence, because the
ruling to proceed under option 2 rests on it.

```
allowlist: ['announce', 'content_announce', 'geo_anchor']
    entity         publishable: False
    model_pose     publishable: False
    model_command  publishable: False
```

`publish.resource_for_put` gates every PUT on `PUBLISHABLE` and refuses
anything else:

> This endpoint publishes exactly the two SSD/SCD successors: announce,
> content_announce, geo_anchor. Everything else it serves is read-only.

Every type this bridge would push downstream is outside that allowlist, so
option 1 fails before rate is even a question. **This is the design working,
not a defect** — the publish surface is the SSD/SCD successor, scoped to
discovery and anchors, and its own refusal text says so. It is therefore
evidence for the 1.9 write-path candidate (entry 4), whose deferred scope —
upstream streaming, leases, provider scopes — is exactly this use case, with
this bridge as its first real demander. The rate question is separate and
unmeasured; conflating the two would make one finding look like two.

## Finding 4: there is no N.6 surface to use, and the batch stops here

The directive predicted this as the likeliest hole and instructed a stop
rather than a workaround. It is the hole, and it is wider than "dispatch to a
downstream consumer": **the reference implementation has no command surface at
all.**

The gateway's complete route table:

```python
web.get("/", root)
web.get(f"{WELL_KNOWN}/schemas", schemas_index)
web.get(f"{WELL_KNOWN}/schemas/{{type}}", schema_one)
web.get(f"{WELL_KNOWN}/search", search)
web.post(f"{WELL_KNOWN}/search", search)
web.get(f"{WELL_KNOWN}/bootstrap", no_dds)
web.get(f"{WELL_KNOWN}/resolver", resolver)
web.get(gateway.RESOLVE_PATH, resolve)
web.get(f"{ASSET_PATH}/{{name}}", asset)
web.get("/{scene}/{stream}/{type}/{version}", collection)
web.get("/{scene}/{stream}/{type}/{version}/{key}", instance)
web.put("/{scene}/{stream}/{type}/{version}/{key}", put_instance)
web.delete("/{scene}/{stream}/{type}/{version}/{key}", delete_instance)
web.put(gateway.RESOLVE_PATH, put_manifest)
```

The only POST is on `search`. The write surface is **PUT and DELETE on
instances** — that is state, not verbs. The word `command` appears nowhere in
the server package except one docstring about the CLI being "the command that
stands it up".

**And the conformance suite codifies the absence as conformant**, which is why
no green run ever flagged it:

```python
def check_commands_post(ep, schemas):
    """Commands are POST, and an endpoint with no command lane says so.

    Northbound-only endpoints have no verb to accept. The check is that a
    command POST is either served with decline semantics or refused cleanly
    -- never a 500, and never silently accepted by something that cannot act.
    """
```

It asserts the POST returns one of `200, 202, 400, 401, 403, 404, 405, 409`.
Every run in this program reported `POST -> 405 (no command lane advertised)`
and passed. The check is correct — the binding permits northbound-only
endpoints — but it means "commands are POST, or cleanly absent" has only ever
exercised the second clause.

### Why this stops the batch rather than shaping it

§3 requires the command lane in this batch: "a bridge that only streams down
is half a bridge." All three ways to get there are closed:

| route to an upstream lane | why it is closed |
| --- | --- |
| hand-roll N.6 in the demo repo | §2: "Hand-rolling N.3/N.5/N.6 endpoints in the demo repo is not on the list" |
| add N.6 to `spatialdds-web` | the composability condition — a gap in the reference implementation gets its own directive on its own review |
| keep `POST /v1/model/command` as-is | the workaround that hides exactly what this batch exists to surface |

So the downstream lane is buildable under the option-2 ruling and the upstream
lane is not, and splitting them would deliver the half-bridge §3 rules out.
**Reported rather than worked around, per §2.**

What the `spatialdds-web` directive would need to settle, since the measurement
cannot: whether N.6 is a gateway surface at all, or whether a command lane is
by definition a backend's own concern that the binding only describes. The
`put_instance`/`delete_instance` pair suggests the latter was the design
intent — state in, no verbs — in which case the finding is not "N.6 is
missing" but "the binding's command lane has no reference implementation and
its conformance check cannot tell that from an endpoint that legitimately has
none."

## What the browser depends on, concretely

The compatibility bar, itemised:

| the browser needs | today | after |
| --- | --- | --- |
| the latched venue as a snapshot | `GET /v1/model` → `{entities, relationships}` | N.3 collection GET per resource |
| live entity updates | `/ws` pattern subscribe | N.5 socket per resource |
| removal signalled | `oarc.model_entity_disposed` | whatever the binding emits (see Finding 3) |
| live robot/duck poses | `/ws`, `oarc.model_pose` | N.5 on the pose resource |
| sending a `goto` | `POST /v1/model/command` | N.6 |
| relationships in the snapshot | `{relationships: [...]}`, 8 client sites | **gone** |

The `relationships` key disappears from the snapshot shape, and the client
code that reads it is removal rather than rewiring: **8 lines across two
files** (3 in `spatialdds_bridge.ts`, 5 in `app.ts`), carrying **10
occurrences** of the name (5 and 5). Both counts are given because they differ
and the smaller one is the one previously reported: "sites" meant lines, and a
figure that travels into a 1.9 entry's evidence should not need its definition
guessed. That is
[`relationship-candidate-1.9.md`](relationship-candidate-1.9.md)'s predicted
cost made concrete, and those sites going is worth one line in that entry's
evidence.

Also operator-scoped: the binding serves `spatial.owm.Entity` behind a bearer
token. The browser currently fetches `/v1/model` unauthenticated. So the
rewiring inherits a token question the custom surface never had.

## `_PRE18` after this batch

| symbol | consumer today | after |
| --- | --- | --- |
| `TOPIC_MODEL_ENTITY_V1_PRE18` | `web_bridge/server.py` | none |
| `TOPIC_MODEL_POSE_V1_PRE18` | `web_bridge/server.py` | none |
| `TOPIC_MODEL_COMMAND_V1_PRE18` | `web_bridge/server.py` | none |
| `TOPIC_MODEL_RELATIONSHIP_V1_RETIRED` | `web_bridge/server.py` | none |
| `pre18.seed_relationships` | `web_bridge/test_model_cache.py` | none |

As `60d5cc6` predicted: this is the last consumer, so the whole pre-1.8
surface falls out of use with it.

**Done 2026-10-18.** The constants, `spatialdds_demo/pre18.py`,
`model_cache.py` and `test_model_cache.py` are deleted, along with
`server.py`'s `_ModelPump`, `GET /v1/model`, `POST /v1/model/command` and
`GET /v1/keepout`. One thing came out of that which the table above did not
predict: the keyed-ness guard in `tests/test_command_lane.py` was reading
`idl/demo/oarc_model.idl` while every type in the same file had moved to
`spatial.owm` — so it was asserting a property of a file its subject no longer
came from. The property holds in both IDLs, which is exactly why nothing
noticed. Re-aimed at `idl/v1.8/provisional/owm.idl` and shown failable there.

**The 1.7 model compile lane: orphaned, and not retired.** After the above,
`idl/demo/oarc_model.idl` and the `spatialdds_idl/oarc_model/` tree it
generates have **no consumer at all**. Measured, not assumed: no Python
outside the generated tree imports `spatialdds_idl.oarc_model` (the only
remaining importer is the package's own `__init__`), and no test reads the
`.idl` as text any more now that the guard above is re-aimed. Every other
mention in the tree is prose in a docstring or a census.

So retiring it is a four-step edit with no code consequence — delete
`idl/demo/oarc_model.idl`, drop `oarc_model` from `spatialdds_idl/__init__.py`
and its `__all__`, regenerate, and correct seven prose references (three test
docstrings, `ar_demo/SPEC_COMPLIANCE.md`, and three censuses). **Not taken**,
per the batch directive: reported with the facts, left for James. Note the one
thing that makes it more than cosmetic: `spatialdds_idl/` is the 1.7 tree the
whole AR demo still compiles against, so the regeneration touches a tree six
other lanes import even though the removal itself is inert.

## The stop condition: met, by Finding 4

No verdict blocks at the **type** level — the types are the ones already
censused twice. Two of the three architectural blockers are ruled on or filed:
option 1's allowlist (entry-4 evidence, option 2 ruled) and N.5's per-resource
shape (a client change, not a gap).

The third stops the batch. There is no N.6 surface in the reference
implementation, §3 requires the upstream lane in this batch, and every route to
it is closed by the directive's own rules. So this census is the batch's
deliverable and the migration does not follow it until the N.6 question is
settled where it belongs.

The downstream lane is buildable today under option 2 and is deliberately not
built here, because shipping it alone is the half-bridge §3 names. If the
ruling after review is to split the lanes, that is a different directive and
this census still holds.

## Out of scope, stated

SceneScape — its live serving becomes nearly free after this, and becoming
free is not being done; untouched. OpenVPS, deployment, and any
`spatialdds-web` edit. The AR demo's six routes, per Finding 1. The ROS
tier, which `92d9232` left where it was.
