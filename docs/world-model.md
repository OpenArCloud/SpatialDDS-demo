# Open World Model (`spatial.owm/0.1`, SpatialDDS 1.8)

Moved out of the landing README so that page can stay short. This is the
detail behind the **Open World Model** row in the demo table.

**This layer has migrated.** It was prototyped here as `oarc_model`, a
demo-local and non-normative layer, so the shape could be argued against a
running demo before anything was proposed. SpatialDDS 1.8 carries the result
as `spatial.owm/0.1` -- provisional and independently versioned -- and this
demo now publishes those types, generated from the spec at commit `9c3e1f2`.
What moved, member by member, is
[`owm-1.8-census.md`](owm-1.8-census.md); what the migration cost is the
`Relationship` type, which 1.8 does not carry.

Read the census before this page if you are here to judge the proposal: this
page describes a working demo, and the census is the only place that says what
the demo can no longer express. See also
[`ar_demo/SPEC_COMPLIANCE.md`](../ar_demo/SPEC_COMPLIANCE.md) for the gaps the
prototype surfaced on the way here.

The catalogue says what content *exists* — one `duck.glb`, one checksum, one
URI. The model says what is *there*: entities with identity, pose, type and
relationships, pointing back at catalogue content when they have an asset. One
asset, three ducks. A catalogue row carrying its own pose can place a duck
exactly once, which is the limitation this removes.

```bash
python3 scripts/move_duck.py ent:duck:west 9.0 -12.0     # and watch it move
python3 scripts/retire_entity.py ent:duck:east "winter"   # tombstone, then gone
```

**The browser view is dark until the bridge migrates.**
`SPATIALDDS_MODEL_LAYER=1` plus `curl localhost:8088/v1/model` used to serve
the whole model to clients with no DDS participant. The web bridge is a later
batch: it still reads the pre-1.8 topics with the demo-local types, so it sees
an empty bus. Until then the model is read over the web binding instead, which
is a better demonstration anyway because nothing in it is this repository's
code:

```bash
docker run --rm -v "$PWD:/app" -w /app cyclonedds-python \\
    bash tools/capture_owm_session.sh                     # capture a session
python3 ../spatialdds-web/tools/run_gateway.py \
    --mcap recordings/owm-littlefield.mcap --port 8831    # serve it
python3 examples/list_owm_entities.py                     # read it
```

The evidence from exactly that sequence, run against the unmodified gateway
and the spec's own conformance suite, is in
[`recordings/owm-littlefield.evidence.md`](../recordings/owm-littlefield.evidence.md).

One latched topic, `spatialdds/ut-austin-littlefield/world/entity/v1`:
TRANSIENT_LOCAL, KEEP_LAST(1) per key. That is what makes late join work: a
client opening a tab is handed the whole model by the middleware, unrequested,
with no replay code anywhere. Measured across processes at 0.14 s.

The names are 1.8's scene-scoped convention, §3.3.1's
`spatialdds/<domain>/<stream>/<type>/<version>`, with the scene as the domain.
There was a second latched topic, `spatialdds/model/relationship/v1`, carrying
the venue's containment hierarchy. It is retired with its type.

`GET /v1/model` serves the same view to clients that have no DDS participant.
**The bridge's cache is a mirror, not a store — it holds nothing the bus does
not, because an HTTP client is not a reader.** Anything that would make it the
source of truth for state the bus does not carry is the wrong change.

Entities name their frame by the **UUIDv5 of its fqn**, the same derivation
the catalogue row and the announced frame transform use, so all three name one
frame rather than three that merely look alike.

### Two publishers, one model

`scripts/gnome_publisher.py` is a second, independent process writing to the
same latched topics under its own `source_id`. A consumer sees one world of
five things, not two feeds to reconcile — which is the situation the layer has
to survive in the field, where you do not get one authority, one vocabulary or
one deployment.

It carries a type nothing here can resolve (`https://example.org/vocab/garden-gnome`,
deliberately fictional) and no `demo.label`, because a stranger has no reason
to speak this demo's property conventions. **An unknown type must still
render.** A client that draws only what it recognises hides the world from its
user; the honest behaviour is to place it correctly, name it from what little
was published, and say plainly that the vocabulary is unfamiliar. The name
falls back through `demo.label` → a known type label → the type URI's tail →
the id's tail, each step saying less and none inventing anything.

The client's type-URI → label table is `TYPE_LABELS` in
[`web/src/spatialdds_bridge.ts`](../web/src/spatialdds_bridge.ts) — an explicit
exported map, not incidental code, because its *miss* path is a behaviour the
demo depends on. A Python test reads it back and fails if the seeder publishes
a type the client cannot label, which is unusual and deliberate: the
alternative is two lists that drift while nothing fails.

Nothing switches the client between paths: if `/v1/model` returns entities it
renders from them and the catalogue contributes only the asset each one points
at, suppressed per `content_id` so the two cannot both draw the same duck.
`?catalogpose=1` forces the legacy path for comparison, and
`?basis=observed` (or `declared`, `authored`, `derived`; comma-lists accepted)
filters the view to how each claim was arrived at — a view, not a
subscription: the client still receives the whole model and says in the
readout how much it is hiding.

Anything that declares an extent draws it as a wireframe, **coloured by how
the claim was arrived at** rather than by which entity it is:

| | Basis | Means |
|---|---|---|
| 🟦 cyan | `OBSERVED` | something measured it — the fountain is in the LiDAR capture the map was built from |
| 🟨 amber | `DECLARED` | the venue asserts it — the pond's bounds are stated, not surveyed |
| 🟪 violet | `DERIVED` | computed by a service from something else |
| ⬜ grey | `AUTHORED` | somebody put it there |

The colours are keyed to basis on purpose. Two services can publish two
volumes over the same water, and what a viewer needs to see is not *which
entity is which* but *what kind of claim each one is* — a palette keyed to
entity ids would say nothing the moment a second opinion arrived.

A `catalog:<content_id>` reference is resolved against the rows the coverage
query already returned, and by a direct `content_id_in` query when it is not
among them — which, since the model loads before any coverage query runs, is
**every** reference on every page load. Lookup-by-id is not a fallback here;
it is the only way an entity's asset is found. The switches that exist to
exercise a path deliberately are in
[CONTRIBUTING](../CONTRIBUTING.md#switches-for-exercising-a-path-deliberately).

### The ducks wander — a consumer that produces

`spatialdds_demo/duck_mover.py` is the first thing here that both reads the
model and changes it, and it is a service rather than a script because that
distinction is the point: a script run by a person is easy to special-case in
your head, and a service asking continuously is the real shape of the
single-writer rule.

```bash
SPATIALDDS_MODEL_LAYER=1 SPATIALDDS_DUCK_MOVER=1 ./run_bridge_server_docker.sh
```

It **writes nothing on the model topics**. A duck keeps its heading and
wanders off it rather than picking a fresh direction each step: a random walk
goes nowhere in particular, so three ducks stepping half a metre six times a
second would spend a minute vibrating around where they started. Holding a
course and turning gradually makes the same step size travel, and reads as
swimming rather than as a rendering fault. It reads the model like any client,
decides what it would like to be different, and asks the authority on the
command lane. Three properties worth knowing, each of them guarded by a test:

- **It selects by type**, not by an id list, so a fourth rubber duck published
  by anybody starts swimming without editing it.
- **It reads the pond's bounds from the model** and re-reads them on every
  update. Its only geometry is a clamp that knows nothing about ponds or
  water — it knows that something published a box and a thing should be in it.
  That is why `scripts/reshape_pond.py` can crowd the ducks into a smaller
  pool with no duck-to-pond code anywhere.
- **Stopping it freezes the ducks for everyone**, including a reader that
  arrives afterwards. Nothing of the mover's is latched, so there is no ghost
  writer whose last word outlives it.

### Two tiers, because a duck drifting is not news

A duck moving 0.45 m changes one field. Republishing its identity, type,
extent, references and lifecycle to say so is the wrong shape at any rate
worth calling fast, so the layer has a second topic:

| Topic | QoS | Carries |
|---|---|---|
| `spatialdds/ut-austin-littlefield/world/entity/v1` | RELIABLE + TRANSIENT_LOCAL, KEEP_LAST(1) | the whole entity — the rest state |
| `spatialdds/ut-austin-littlefield/world/model_pose/v1` | BEST_EFFORT + VOLATILE, KEEP_LAST(1) | a bare pose — where it was last seen going |

An entity's own `layer` decides which treatment it gets, which is what that
field is for. A `FAST` entity's every move goes out on the pose lane and its
entity record is refreshed every fifth move, so a client joining mid-animation
is **at most four moves stale** and converges on the next pose. Measured: 60
poses to 12 records over ten seconds, staleness at join exactly four moves.

Two rules make that safe, and the second is what makes the first
implementable:

> **The latch converges to the stream on idle.** A fast lane may lag the truth
> while things are moving. It may not leave a wrong answer lying around once
> they have stopped.

> **"Idle" is only meaningful relative to an entity's own cadence.** A fixed
> threshold shorter than the update interval makes every gap between updates
> look like a stop.

The demo learned the second one the hard way: a fixed one-second threshold
against a mover giving each duck a turn every 1.5 s produced 94 "it stopped
moving" flushes while nothing had stopped, republishing the expensive record
as often as the cheap one. The threshold is derived now, and the latch
converges **1.69 s** after motion stops.

### Two accounts of one pond

Run the stack with `SPATIALDDS_POND_WATCH=1` and a second service,
`svc:fusion:demo/pondwatch`, publishes `ent:pond:observed`: its own entity,
in the same frame, describing the same water the venue declares — and
disagreeing about where its edges are. The venue declared bounds a little
inside the waterline so anything trusting them stays wet; the observer
reports what it measures, which is slightly more pond, with a wobble on it.

**The model carries both and crowns neither.** Each says who published it and
how the claim was arrived at, and the wireframes are coloured by basis so the
difference is visible rather than a puzzle. Where two entities claim the same
name, the map qualifies them — *Pond (declared)* and *Pond (derived)* — rather
than renaming somebody else's thing or hiding the disagreement.

Choosing whom to believe is a **consumer policy**, which is why it is a flag
and not a constant:

```bash
python3 -m spatialdds_demo.duck_mover --bounds declared   # the venue
python3 -m spatialdds_demo.duck_mover --bounds derived    # the observation
```

Same mover, same ducks, different account of the water — and the ducks are
allowed in different places. Measured: trusting the venue, a duck may not go
west of x = 10.5; trusting the observation, it may stand at 9.87.

**Nothing joins the two entities**, deliberately. `Relationship` does carry
`source_id`, so an edge could say *who* published the claim. What it cannot
say is **how the claim was arrived at** — there is no `basis` on an edge — and
for an identity claim that is the part that matters: "these are the same
water" asserted by the venue, computed by a fusion service, or assumed by
whoever wrote the seeder are three different statements that would look
identical on the wire. An identity edge whose epistemic status cannot be
expressed launders an assumption into the model. **The placeholder rule until
R10 settles it: surface both, attribute both, join neither.** Recorded in
[`SPEC_COMPLIANCE.md`](../ar_demo/SPEC_COMPLIANCE.md).

### The command channel — how anything gets changed

Nothing writes to the model topics except the publisher that owns them.
`move_duck.py` and `retire_entity.py` publish a `ModelCommand` on
`spatialdds/ut-austin-littlefield/world/model_command/v1` and the model
service applies it.

| | |
|---|---|
| Topic | `spatialdds/ut-austin-littlefield/world/model_command/v1` |
| QoS | `MODEL_COMMAND` — RELIABLE, **VOLATILE**, KEEP_LAST(16), keyed on `command_id` |
| Verbs | `move` (guarded pose), `retire` (reason), `set_extent` (guarded extent), `restore` |

1.8 defines a fifth verb, `goto`, which the robot demo reads. The demo had a
sixth, `dispose_edge`, and it is gone with the relationship type -- a command
naming an edge now falls through to the unknown-verb refusal, which says so in
a sentence.

VOLATILE and unkeyed because a command is an event, not state: a client
joining tomorrow has no business replaying today's retirements. That has one
consequence worth knowing — **a writer with no matched reader drops the sample
on the floor**, so both tools wait for `publication_matched` before writing.
It is a discovery wait, not a retry.

This indirection is not ceremony. TRANSIENT_LOCAL history is scoped to the
writer that published it, so a pose or a tombstone written by a short-lived
tool dies with the tool while the service's sample stays latched: a browser
open at the time follows along, and the next one to load is handed the old
world. It was measured both ways —
[`SPEC_COMPLIANCE.md`](../ar_demo/SPEC_COMPLIANCE.md), "Two writers, one
instance" — and the counter-example is kept runnable.

Both tools therefore **report what the bus showed, not what they sent.** A
tool that prints "moved" when it means "asked" sends you debugging the wrong
process.

**Retirement** is a tombstone — the entity's last sample, carrying
`state = RETIRED` and the reason — then a dispose of the instance, cascading
to every edge touching it. The order matters: a dispose alone says a thing is
gone and nothing about why, and after it there is nothing left to ask. The
demo leaves a pause between the two so a person can read the reason; **both
samples are valid back-to-back and no consumer may rely on that gap existing.**
It is presentation pacing, not protocol semantics.
