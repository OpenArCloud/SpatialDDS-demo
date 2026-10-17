# For the spec queue: two things in `spatial.owm/0.1` the migration surfaced

The Open World Model demo is the first consumer of 1.8's provisional
`spatial.owm` module outside the SceneScape sidecar, and migrating onto it put
every member of every type through a real publisher, a real bus, a typed
recording and an independent web-binding reader. Two observations came out of
that which belong in front of the spec rather than in this repository's
backlog. Neither is a defect in 1.8, and neither blocked the migration.

The `Relationship` GAP is filed separately, as a 1.9 candidate, in
[`relationship-candidate-1.9.md`](relationship-candidate-1.9.md).

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
