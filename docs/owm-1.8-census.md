# The Open World Model demo against SpatialDDS 1.8: a field-level census

The OWM demo was built on `idl/demo/oarc_model.idl`, a demo-local layer written
so the shape could be argued against a running demo before anything was
proposed. SpatialDDS 1.8 carries `spatial.owm/0.1` as a provisional module, and
it is visibly descended from this file: the comments are the same arguments.
This census is the field-level check of that resemblance, because a near-match
is where migrations hide their losses.

Every member of every custom type gets one verdict:

| verdict | meaning |
| --- | --- |
| **HOME** | a 1.8 member carries it as-is |
| **IMPROVED** | 1.8 carries it better than the demo does |
| **ADAPTER** | expressible, with a documented mapping |
| **DROPPED** | not carried, with the reason |
| **GAP** | no 1.8 home; goes to the candidates queue, never invented locally |

**Method.** The member lists are not read off by eye. Both IDL files were
parsed with `spatialdds.server.idlmodel` — the parser the web binding's own
gateway uses — and compared member by member on name, resolved type, sequence
and array shape, `@key`, and bound. Enums were compared on members and on
`@value` ordinals. The spec side is `idl/v1.8/provisional/owm.idl` as of
SpatialDDS-spec `9c3e1f2`, read from git rather than from a working tree.

## The headline

**30 of 37 members are HOME, unchanged in name, order and type. The other 7 are
all of `Relationship`, which is a GAP.** Three enums carry across identically,
members and ordinals both.

| custom type | 1.8 home | members | verdict |
| --- | --- | --- | --- |
| `oarc_model.Entity` | `spatial.owm.Entity` | 16 of 16 | **HOME** |
| `oarc_model.ModelPose` | `spatial.owm.ModelPose` | 4 of 4 | **HOME** |
| `oarc_model.ModelCommand` | `spatial.owm.ModelCommand` | 10 of 10 | **HOME** |
| `oarc_model.Relationship` | none | 7 | **GAP** |
| `oarc_model.Basis` | `spatial.owm.Basis` | 4 enumerators | **HOME** |
| `oarc_model.LifecycleState` | `spatial.owm.LifecycleState` | 4 enumerators | **HOME** |
| `oarc_model.ModelLayer` | `spatial.owm.ModelLayer` | 3 enumerators | **HOME** |
| `MODULE_ID = "oarc.model/0.1"` | `"spatial.owm/0.1"` | — | **HOME**, renamed |

The only type-level differences the parser reports inside the HOME structs are
the module paths of the three enum members (`oarc_model.Basis` →
`spatial.owm.Basis`, and likewise for `ModelLayer` and `LifecycleState`). The
enumerators and their ordinals are identical, so nothing on the wire changes
but the name of the module the type came from.

## Entity, member by member

| member | type | verdict | note |
| --- | --- | --- | --- |
| `entity_id` | `string` `@key` | HOME | same key, same name |
| `basis` | `Basis` | HOME | module path only |
| `type_uris` | `sequence<string, 4>` | HOME | same bound; borrowed vocabularies either way |
| `layer` | `ModelLayer` | HOME | module path only |
| `frame_ref` | `spatial::common::FrameRef` | **IMPROVED** | the member is HOME; the *type* gained four members in 1.8 — see below |
| `has_pose` | `boolean` | HOME | |
| `pose` | `spatial::core::PoseSE3` | HOME | already borrowed |
| `has_extent` | `boolean` | HOME | |
| `extent` | `spatial::core::Aabb3` | HOME | see IMPROVED row below |
| `properties` | `sequence<KV, 32>` | HOME | same bound |
| `external_refs` | `sequence<KV, 8>` | HOME | same bound |
| `content_refs` | `sequence<string, 8>` | HOME member, **ADAPTER** values | see below |
| `state` | `LifecycleState` | HOME | module path only |
| `state_reason` | `string` | HOME | |
| `source_id` | `string` | HOME | |
| `stamp` | `builtin::Time` | HOME | |

### ADAPTER: `content_refs` value form

The member is HOME; what goes in it changes. The demo publishes
`catalog:<content_id>`, which its own IDL calls a Part 1 form with
`spatialdds://` as "the eventual form". 1.8 states the canonical form outright:
a `spatialdds://` manifest URI.

Scope of the adapter in this demo: **one entity**. The venue entities publish
`content_refs=[]` (`model_service.py`), the derived pond publishes `[]`
(`pond_watch.py`), and only the robot carries a reference —
`content_refs=[f"catalog:{ROBOT_CONTENT_ID}"]` (`robot_bridge.py:159`).

The mapping is documented rather than applied in this batch, for a reason worth
stating: a `spatialdds://` URI names an authority that resolves it, and this
demo has no resolver and no manifest store. Writing a URI that resolves nowhere
would be worse than a `catalog:` ref that is honestly local. The row is
therefore ADAPTER-documented, and the trigger for applying it is a resolver in
the demo — which is what the web-binding work in `spatialdds-web` would supply.

### IMPROVED: `FrameRef` gained a scale declaration, and it changes the packaging

The member name and position are unchanged, but 1.8's `spatial.common.FrameRef`
carries **eight** members where 1.7's carries four:

| | members |
| --- | --- |
| 1.7 | `uuid`, `fqn`, `has_coord_convention`, `coord_convention` |
| 1.8 | the same four, plus `has_scale`, `scale_status`, `meters_per_unit`, `display_unit` |

That is an improvement the demo can use rather than absorb: the venue frame is
metric and says so nowhere, and `model_service.venue_frame()` is the one place
in the whole OWM demo that constructs a `FrameRef`. It gains
`has_scale=True`, `scale_status=SCALE_DECLARED`, `meters_per_unit=1.0`,
`display_unit="m"` — four facts that were previously an assumption every
consumer had to share silently.

**And it decides how the migration is packaged**, which is the part worth
reading twice. The demo's generated bindings live in one checked-in package,
`spatialdds_idl/`, built from `idl/v1.7/` by `scripts/generate_types.py`, and
**46 files across the four out-of-scope demos import it**. `owm.idl` borrows
`FrameRef` by typedef, so OWM's `Entity` must be generated against 1.8's
`core`/`types` or its `frame_ref` would be the four-member 1.7 struct while the
recording's embedded IDL declared eight — and every member after `frame_ref`
would be read at the wrong offset by anything consuming it.

So there are two ways to honour the directive's scope, and only one of them
honours it:

| | what it costs |
| --- | --- |
| regenerate `spatialdds_idl/` from 1.8 | every `FrameRef` construction site in the AR demo, nuScenes, DeepSense, fusion and the bridges gains four required fields: 46 importing files, four demos the directive puts in later batches |
| a second package from 1.8, used by the OWM demo alone | one more checked-in generated tree (the existing one is 64 files, 1.1 MB), and two vendored IDL versions live side by side |

The second is the only one that migrates the OWM demo without migrating four
demos nobody asked for, so that is what this batch does. It is not a
compatibility shim — nothing translates between the two, and no code reads
both — it is two vendored spec versions during a staged migration, which the
tree already holds for 1.4 and 1.6.

### IMPROVED: region footprints could be `SpatialZone`, and are not yet

1.8's `Entity` comment points region entities at `events::SpatialZone`, which
carries polygon and prism geometry as of 1.8, with `extent` as the
axis-aligned fallback. The demo's pond and shallows are regions described by
`Aabb3` extents, and the robot's keep-out costmap is built from those extents
(`robot_tier/keepout_node.py`).

This is a real improvement available: a pond is not a box. **Not taken in this
batch**, and the reason is the acceptance bar: §2 requires the demo to behave
as it did, and moving the keep-out's geometry source from an axis-aligned
extent to a polygon changes what the robot plans around. It is a follow-up with
its own before-and-after, not a line in a migration.

## ModelPose, member by member

| member | type | verdict |
| --- | --- | --- |
| `entity_id` | `string` `@key` | HOME |
| `pose` | `PoseSE3` | HOME |
| `source_id` | `string` | HOME |
| `stamp` | `Time` | HOME |

Four members, identical. The QoS story is identical too, and stated the same
way in both files: VOLATILE, `KEEP_LAST(1)` per key, a late joiner handed
nothing here and reading the latched `Entity` instead.

## ModelCommand, member by member

| member | type | verdict |
| --- | --- | --- |
| `command_id` | `string` `@key` | HOME |
| `verb` | `string` | HOME member, one **DROPPED** value |
| `subject_id` | `string` | HOME |
| `reason` | `string` | HOME |
| `requester_id` | `string` | HOME |
| `has_pose` | `boolean` | HOME |
| `pose` | `PoseSE3` | HOME |
| `has_extent` | `boolean` | HOME |
| `extent` | `Aabb3` | HOME |
| `stamp` | `Time` | HOME |

### Expectation corrected: the keying is already right

The directive expected that "ModelCommand needs proper keying under 1.8
conventions". It does not: the demo already keys on `command_id`, and 1.8's
`ModelCommand` keys on `command_id` identically.

The history is worth keeping because it is the same lesson twice. The demo's
command type was originally unkeyed, which "looked more honest -- there is no
instance here whose lifetime means anything -- and it crashed the service":
when an operator tool exits, the middleware delivers an invalid sample carrying
only a key blob, and deserialising the key of an unkeyed type reads a length
prefix out of an empty buffer and raises. The fix was at the type. 1.8 adopted
the same key and records the same reason in its own comment. **Nothing to do.**

### DROPPED: the `dispose_edge` verb

The demo's `verb` admits `"dispose_edge"`; 1.8's documented verb set is
`"retire" | "move" | "set_extent" | "restore" | "goto"`, and 0.1 "defines verbs
over entities only". With no `Relationship`, there is no edge to dispose.

`verb` is a `string`, so the type does not enforce the set — which means the
demo *could* keep publishing `dispose_edge` and 1.8 would carry it. It does not,
because a verb naming a subject kind the module does not define is exactly the
local invention this census exists to prevent. The verb goes with the edges.

## GAP: `Relationship`

No 1.8 home, and not an oversight. `spatial.owm/0.1` excludes it in as many
words: relationship extensions and the identity semantics that go with them
(per-edge basis, confidence, retraction) are "EXPLICITLY EXCLUDED and reserved
as open design — this module MUST NOT freeze them". The demo's own IDL reached
the same conclusion first: an edge that can be disposed but cannot say why, or
with what basis the relation was asserted, is "under-specified relative to
Entity, twice over; an R3 question, not a demo one".

| member | type | verdict |
| --- | --- | --- |
| `rel_id` | `string` `@key` | GAP |
| `kind` | `string` | GAP |
| `from_entity_id` | `string` | GAP |
| `to_entity_id` | `string` | GAP |
| `properties` | `sequence<KV, 16>` | GAP |
| `source_id` | `string` | GAP |
| `stamp` | `Time` | GAP |

### What the demo loses, precisely

It loses the **published containment hierarchy**: fountain contains pond, pond
contains the shallows, pond contains each duck — four to six edges seeded by
`model_service.py`, and the cascade that disposes an entity's edges when the
entity is retired.

It loses **nothing that any behaviour reads**, and that distinction is the
whole of the stop-condition question in §1 of the directive, so here is the
evidence rather than the assurance:

- **The ducks** do not read edges. `duck_mover.py` reads the pond's `extent`
  off the `Entity` (`duck_mover.py:357`), by a named entity id passed as a
  command-line flag, and its own comment says why: "there is no `on`
  relationship, and Part 3 leaves computed containment alone until
  `Relationship` can carry a basis."
- **The robot's keep-out** does not read edges. `robot_tier/keepout_node.py`
  builds its costmap from the extents on the bus.
- **The browser** does not render edges. `web/src/app.ts` carries the list and
  counts it in one log line (`model:loaded N entities, M relationships`);
  nothing navigates or draws it. The entity panel reads entity fields.
- **The web bridge** is the only subscriber (`bridges/web_bridge/server.py`),
  and it streams them to that count.

**Verdict on the directive's stop condition: a trim, not an amputation.** The
hierarchy was a demonstration of the layer's shape, and the shape it
demonstrated is the one 1.8 deliberately has not frozen. The migration
proceeds.

### Where the GAP goes

To the 1.9 candidates queue, with this census as origin, and with the demo's
operational experience as its evidence: three things the demo found that an edge
type needs and the demo-local one lacked — a `basis`, so a computed containment
is distinguishable from a declared one; a `LifecycleState` with a reason, so an
edge can say why it went rather than only stopping; and an identity semantics
for "the same water" that the pond-watch service needed and could not express
(`pond_watch.py:110` is a property string apologising for its absence).

## Topics: the scene-scoped convention

The demo's model topics are four segments and carry no scene:

| now | after |
| --- | --- |
| `spatialdds/model/entity/v1` | `spatialdds/ut-austin-littlefield/world/entity/v1` |
| `spatialdds/model/pose/v1` | `spatialdds/ut-austin-littlefield/world/model_pose/v1` |
| `spatialdds/model/command/v1` | `spatialdds/ut-austin-littlefield/world/model_command/v1` |
| `spatialdds/model/relationship/v1` | retired with the type |

§3.3.1 is `spatialdds/<domain>/<stream>/<type>/<version>`, five segments. Two
things follow that are not cosmetic:

- **The type segment is the 1.8 type's name**, not the demo's shorthand:
  `pose` becomes `model_pose` and `command` becomes `model_command`, because
  the segment is how a reader finds the type. The web binding's client derives
  that mapping from the published schemas —
  `entity → spatial.owm.Entity`, `model_pose → spatial.owm.ModelPose`,
  `model_command → spatial.owm.ModelCommand` — and a topic whose segment does
  not match is a topic the instrument cannot name.
- **The scene is `ut-austin-littlefield`**, and it keeps it. The venue is the
  Littlefield Fountain plaza, which is what every entity id already says
  (`ent:pond:littlefield`). It is deliberately *not* `ut-austin-south-mall`,
  which is the scene `spatialdds-web`'s own recordings use for the same
  physical place: a scene names a deployment's namespace, and two independent
  demos sharing one would be claiming to be the same deployment.

## What this census does not cover

Stated because a census that quietly scopes itself is the same failure as a
migration that quietly amputates:

- **The robot demo and its command lane.** Out of scope by the directive, and
  it stayed out: `robot_bridge.py` and `robot_tier/` are still built against
  the demo-local `oarc_model` types, and they now name the pre-1.8 topic
  constants explicitly (`TOPIC_MODEL_ENTITY_V1_PRE18` and its siblings) so
  that is visible in the source rather than discovered as an empty bus. The
  `goto` verb the robot reads is in 1.8's vocabulary and will migrate
  unchanged when that batch runs.
- **The web bridge and the browser.** Out of scope by the directive. The
  consequence is stated in the migration report rather than here.
- **The bridge, live-web serving, and SceneScape.** Untouched.
- **The other demos.** AR, nuScenes, DeepSense and multi-operator fusion are
  all still 1.7 and are later batches.

## The outcome, so this file is not read without it

The migration ran on the strength of this census, and the census was right
about the shape of it: 30 members moved unchanged, `Relationship` was the only
casualty, and nothing blocked.

What the census got wrong, and it matters: it recorded `frame_ref` as plainly
HOME, because the *member* is. The **type** gained four members in 1.8, and
that is what decided the packaging -- a second generated bindings package
rather than a cutover of the shared one. The row now reads IMPROVED with the
consequence spelled out. A census that compares members and not the types
those members are made of will keep finding this class of surprise, and the
method section above has been left as it was so the gap in it is legible.

Two numbers for anyone reading this as a precedent:

- **One** `FrameRef` construction site in the whole OWM demo, which is why a
  four-member type change cost one function and one test fixture.
- **46** files across the four unmigrated demos import the shared 1.7
  bindings, which is what a single-package cutover would have cost instead.

The acceptance evidence is `recordings/owm-littlefield.evidence.md`: a live
capture served by the unmodified web-binding gateway, 13 of 14 conformance
checks passed with 0 failed, and all 202 served payloads validated against the
endpoint's own published schemas.
