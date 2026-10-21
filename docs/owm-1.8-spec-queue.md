# For the spec queue: what the 1.8 migrations surfaced

The Open World Model demo is the first consumer of 1.8's provisional
`spatial.owm` module outside the SceneScape sidecar, and migrating onto it put
every member of every type through a real publisher, a real bus, a typed
recording and an independent web-binding reader. Three observations came out
of that which belong in front of somebody other than this repository's
backlog -- two about the types, one about the bindings `idlc` generates from
them. None is a defect in 1.8 and none blocked the migration.

Two more filings came out of the same batch and are separate because their
subjects are:

- the `Relationship` GAP, as a 1.9 candidate —
  [`relationship-candidate-1.9.md`](relationship-candidate-1.9.md);
- a defect in the **conformance suite** rather than in the binding or the
  types — [`conformance-volatile-lane-finding.md`](conformance-volatile-lane-finding.md),
  which is how this migration discovered that 88% of its own traffic went
  unvalidated by the suite that passed it.

---

## 1. `ModelPose` carries no frame, and the web binding makes that reachable

**What the types say.** `ModelPose` is four members: `@key entity_id`, `pose`,
`source_id`, `stamp`. The frame the pose is expressed in is not among them.
`Entity` carries `frame_ref`, so the frame is learned by joining a pose to the
latched entity with the same `entity_id`.

**Why that is sound on DDS.** The entity topic is `TRANSIENT_LOCAL` and the
pose topic is `VOLATILE`. A subscriber that joins the pose lane is a
subscriber of the same bus, and the middleware hands it the latched entity set
whether it asked or not. The join is available by construction, which is
presumably why the fast lane was allowed to be lean — and lean it should be,
since it is the one lane that carries traffic at motion rates.

**Why the web binding changes the situation.** Under Appendix N the two lanes
are two independent HTTP resources with independent URLs and independent
access classes. This demo's recording serves them as:

```
operator-scoped   /ut-austin-littlefield/world/entity/v1        latched
operator-scoped   /ut-austin-littlefield/world/model_pose/v1    live only
```

A client may subscribe to the second and never fetch the first. Nothing in the
binding requires it to, nothing in the pose payload points at the entity
resource, and the pose validates against its published schema either way — we
checked all 88 of them. What that client receives is a stream of positions in
an unnamed frame, with no error anywhere: 11.79, -7.95 in *what*.

The DDS shape made the join free, so the type never had to state the
dependency. The web shape makes the join a separate request a client can
simply not make, and the dependency is now stated nowhere a machine can read.

**What the fix might be, in rough order of cost.** Normative text in the OWM
module saying a `ModelPose` consumer MUST resolve `frame_ref` through the
entity of the same key, and that a binding serving the pose lane MUST make the
entity lane reachable (which would also mean its access class cannot be
stricter). Or a `frame_ref` on `ModelPose` itself, which costs bytes on the
busiest lane and introduces a second place for the frame to be wrong. The
first looks right: the dependency is real and it is the *statement* of it that
is missing, not the data.

**Not a defect, and why it is here anyway.** 1.8's OWM module is provisional
and independently versioned, which is exactly the window for this. And the
failure mode is the quiet kind — a consumer that is wrong rather than one that
errors — which is the kind worth catching before there are two of them.

---

## 2. `subject_id` carries its subject's *kind* in a string prefix

**What the types say.** `ModelCommand.subject_id` is one `string`, and the
module's comment explains the encoding:

> ids are prefixed (`"ent:"`) so the kind is explicit, leaving room for other
> subject kinds when relationships land, but 0.1 defines verbs over entities
> only

So "what kind of thing this command is about" is carried by a substring of an
identifier, and the rule lives in a comment.

**Why the demo noticed.** Its command handler branched on exactly that
prefix. Pre-migration it read `subject_id.startswith("ent:")` for entity
verbs and `startswith("rel:")` for the edge verb, and chose which of two
tables to look the subject up in. That is a type decision taken by string
inspection. The migration deleted the `rel:` branch with the type, so today
only one prefix is live and the code is honest by accident — but the next
subject kind restores the problem exactly as it was.

The specific hazard is not parsing cost. It is that an id is author-supplied
data and a kind is a contract. A publisher that mints `entity:duck:east`
instead of `ent:duck:east` has produced a well-formed `ModelCommand` that
validates against its schema, carries a verb the module defines, names a
subject that exists, and is silently ignored by every consumer. There is
nothing for a conformance check to catch, because nothing is violated.

**What the fix might be.** When relationships land and there is a second
subject kind, an explicit discriminator — an enum `subject_kind`, or a
`@key`-style union — rather than a widened prefix vocabulary. Until then the
asking price is one sentence of normative text: either the prefix grammar is
normative and conformance can check it, or the field is opaque and consumers
MUST NOT branch on its shape. Right now it is a convention doing a type's job,
which is the thing that reads as working until two implementations disagree
about it.

---

## 3. Generated bindings annotate members with a name they never bind

**Not a spec issue — a CycloneDDS codegen one**, recorded here because this is
where the batch's findings live and because the next person to regenerate the
shared package is the one who can fix it.

**What it is.** `idlc -l py` emits nested members as dotted string
annotations, and this repository's generator patches discriminator-bearing
ones to be fully qualified:

```python
from spatialdds_idl18 import spatial          # the only root-package import
...
cov_pos: 'spatialdds_idl18.spatial.common.Mat3x3'
```

The module binds `spatial`, never `spatialdds_idl18`, so the dotted name in
the annotation is unbound. Measured on both generated trees:

```
spatialdds_idl    NameError: name 'spatialdds_idl' is not defined
spatialdds_idl    cyclonedds serializer built OK
spatialdds_idl18  NameError: name 'spatialdds_idl18' is not defined
spatialdds_idl18  cyclonedds serializer built OK
```

**Why nothing has broken.** CycloneDDS does not call
`typing.get_type_hints`; it resolves these with its own machinery, so every
serializer builds and every demo in this repository works, as they have since
the 1.7 cutover.

**Why it is still worth fixing.** `typing.get_type_hints()` is the standard
way to introspect a dataclass, and it is what dataclass-based tooling reaches
for -- schema generators, validators, API frameworks. Anything that calls it
on a generated SpatialDDS type raises `NameError` rather than returning
hints, which makes these bindings quietly unusable by that whole category of
consumer. A SpatialDDS type system whose Python bindings cannot be
introspected by standard means is a smaller type system than it looks.

**The fix, and when it happened.** One step in the generator's patcher:
`bind_root_package` emits `import <package>` in any generated file carrying a
dotted annotation that names the root, which is the name those annotations
were already using. **Fixed on 2026-10-18**, after the robot and bridge
batches had made regenerating the shared 1.7 tree safe — 35 files changed
across both trees, every one exactly +3/−0 (two comment lines and the import),
and `typing.get_type_hints()` now resolves on all 229 generated dataclasses.

`tests/test_static_checks.py` carried a paragraph at its exclusion explaining
what the skip hid; that paragraph is gone, replaced by
`GeneratedTypesIntrospect`, which walks both trees and calls
`get_type_hints()` on every generated dataclass. Shown failable: with the
imports stripped from one tree it reports 31 unresolvable classes. The
exclusion is back to meaning only what it says — a style checker is the wrong
tool for generated code — and the property it was covering has a test instead
of an apology.

The tree count is derived from what is on disk rather than listed, because
the second generated tree appeared mid-programme and a hand-kept list would
have checked only the one somebody remembered.

**How it was found.** By that gate. Adding a second generated tree put
`spatialdds_idl18/` in front of a check whose skip list knew only about
`spatialdds_idl/` — the exclusion was per-path, so a new path got the scrutiny
the old one had been exempted from, and the exemption turned out to be
covering something real.

---

# Surfaced by the last migration (2026-10-20)

The final migration put the rest of the repository onto 1.8 and, in doing
so, compared this repo's topic registry against the 1.8 spec's §3.3.2 table
for the first time. That comparison found two registered types the validator
did not know (`crossing_line`, `model_entity`, both now added) and the two
observations below, both about `model_entity`'s row.

## 4. `model_entity`'s registered topic disagrees with its reference gateway

At spec commit `9c3e1f2`, the §3.3.2 row reads:

> `model_entity` — World-model entity, lifecycle-shaped (provisional) —
> `spatial::owm::Entity` on `spatialdds/<scene>/model/entity/v0`.

Every implementation this programme has — this demo's model layer,
`spatialdds_ros`, and the reference gateway in `spatialdds-web`, which pins
the **same** spec commit — uses `spatialdds/<scene>/world/entity/v1`. The
gateway serves it at `/{scene}/world/entity/v1`. So the registry row and the
gateway built against it disagree on two of the five §3.3.1 segments: the
group (`model` vs `world`) and the version (`v0` vs `v1`).

Not changed here. Renaming the topic would break the library, the gateway
and every recorded acceptance chain, to match a row the reference
implementation does not follow. Which one is right is the spec's call.

**Origin:** measured during the migration — the §3.3.2 table read out of `SpatialDDS-1.8-full.md` at `9c3e1f2`, set
against `spatialdds_demo/topics.py` and the gateway's served paths.

## 5. `model_entity` has no named QoS profile

The same row spells its QoS out — "RELIABLE + TRANSIENT_LOCAL, KEEP_LAST(1)
per key" — rather than naming a §3.3.3 profile. Implementations have filled
the gap with a name of their own: this demo's model layer and
`spatialdds_ros` announce the lane as `MODEL_LATCHED`, which no §3.3.3 table
contains, so a strict validator (this repo's included) rejects the announce
that the rest of the stack produces.

`MODEL_LATCHED` is QoS-identical to the registered `MAP_META`. Either the
row could name `MAP_META`, or §3.3.3 could register a model profile. This
repo's type registry now defaults `model_entity` to `MAP_META`, because a
default should validate; what the model layer announces is unchanged pending
the spec's answer.
