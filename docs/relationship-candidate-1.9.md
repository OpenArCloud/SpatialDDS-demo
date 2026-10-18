# 1.9 candidate: a relationship type for `spatial.owm`

**Where this belongs.** `spatialdds-web/directions/spatialdds-1.9-candidates.md`,
as entry 6, in that file's format. It is written here because
`spatialdds-web` was read-only for this batch — it was the instrument that
validated the migration, and an instrument edited during its own run proves
less. Landing it is a one-paste operation for whoever owns that repository's
next batch; nothing below depends on this file's location.

**A candidates list in two places is worse than one in the wrong place**, so
until this is pasted there, that file should carry the pointer. The line to
add, under its own heading so it is not mistaken for a drafted candidate:

```markdown
## 6. A relationship type for `spatial.owm` — drafted elsewhere

Raised by the OWM demo's 1.8 migration census and drafted in full at
`SpatialDDS-demo/docs/relationship-candidate-1.9.md`; paste it here when that
repository is not the subject under test.
```

Whichever happens first — the paste or the pointer — this file stops being the
canonical copy at that moment, and should be reduced to a link rather than
left to drift against it.

---

## 6. A relationship type for `spatial.owm`

**Status:** candidate. Raised by the Open World Model demo's 1.8 migration
census (`SpatialDDS-demo/docs/owm-1.8-census.md`), which is the first
consumer of `spatial.owm/0.1` outside the SceneScape sidecar. This is the only
GAP row in that census: 30 of 37 members are HOME, and the other 7 are all of
`Relationship`.

**What exists today.** Nothing, deliberately. `idl/v1.8/provisional/owm.idl`
states that relationships are "EXPLICITLY EXCLUDED and reserved as open
design", and the module carries `Entity`, `ModelPose` and `ModelCommand` only.
`ModelCommand`'s own comment anticipates the question — it says verbs are
defined "over entities only" until relationships land. So the exclusion is a
recorded decision rather than an oversight, and this entry is the evidence a
reopening would need rather than a report of a bug.

**What the demo carried, and lost.** The demo-local layer this migration
retired had a seven-member `Relationship`:

| member | type | what it carried |
| --- | --- | --- |
| `rel_id` | `string` `@key` | the instance, so an edge could be disposed |
| `kind` | `string` | `"contains"` was the only one used |
| `from_entity_id` | `string` | the parent |
| `to_entity_id` | `string` | the child |
| `properties` | `sequence<KV, 8>` | unused by the demo |
| `source_id` | `string` | who published the edge |
| `stamp` | `builtin::Time` | when |

It expressed one hierarchy: the fountain contains the pond, the pond contains
the shallows, the pond contains each duck. That is a claim no geometry
recovers. The ducks sit inside the shallows' bounds and the shallows sit
inside the pond's, so containment can be *computed* — and the demo's tests
still compute it — but "the pond is part of the fountain" is a statement about
the venue, not about two boxes. Wikidata agrees with the shape (Q810524 is
`part of` Q483453), which is why the hierarchy was borrowed rather than
invented.

**The sharper loss is basis, not topology.** The demo has two entities for the
same water: `ent:pond:littlefield`, declared by the venue, and the pond
`pond_watch.py` derives by measuring. Nothing in the model can say they are
the same thing. An edge with a `kind` of `"same_as"` could have said *who*
claimed it, through `source_id` — and still could not have said whether the
identity was asserted, computed or assumed. The demo's own IDL recorded that
as a gap in its own type, and the property text that survives migration says
so on the wire:

> `properties: demo.note` on the derived pond — "The same water as
> `ent:pond:littlefield`, measured rather than declared. Nothing in the model
> says the two are the same thing, and under `spatial.owm/0.1` there is no
> relationship type to say it with."

So a 1.9 relationship type that carries only endpoints and a kind would close
the topology gap and leave the basis gap open. `Entity` already carries
`Basis` (`OBSERVED` / `DECLARED` / `AUTHORED` / `INFERRED`); an edge that
carried the same enum would make "who says so, and on what evidence" uniform
across nodes and edges, which is the design question worth settling before
the field list.

**What it would cost in verbs.** `ModelCommand`'s verb vocabulary in 1.8 is
`"retire" | "move" | "set_extent" | "restore" | "goto"`, over entities. The
demo had a sixth verb, `dispose_edge`, and the census marks it DROPPED. It was
deliberately not called `retire`: `Relationship` had no `LifecycleState`, so
an edge could not say *why* it went, only stop being there. Any 1.9
relationship type inherits that choice — either it carries lifecycle state
and gets `retire`, or it does not and needs a verb that admits the difference.

**Evidence this is real and not theoretical.**

- The migration ran. 581 samples of the migrated demo serve through the
  unmodified web-binding gateway and pass 13 of 14 conformance checks
  (`recordings/owm-littlefield.evidence.md`).
- The loss is measured, not asserted. Five tests were deleted with the type,
  not weakened: two asserting the hierarchy was two levels and named both
  ends of every edge, three covering edge disposal and the reason that
  survives only in a log. The retirement cascade — edges disposed in front of
  the tombstone so nothing pointed at it — is now empty by construction, and
  `tests/test_model_layer.py` asserts the empty list rather than dropping the
  assertion.
- One consumer was left holding a dangling reference, and on 2026-10-18 it
  stopped. The browser view is now wired to the 1.8 binding, and the eight
  client lines that read a `relationships` array are gone with the key:
  five in `web/src/app.ts` (the snapshot's type, two empty-model literals,
  the loaded-count log line) and three in `web/src/spatialdds_bridge.ts`
  (the `ModelSnapshot` type and the snapshot fetch). The four mentions that
  remain in those files are prose saying why the key is absent.
  **That is this entry's predicted cost, paid and countable.** The hierarchy
  the venue used to publish is now claimed by nothing: a client that wants
  containment computes it from geometry or does without.

**What would make this a 1.9 row rather than a backlog note.** A second
independent consumer that needs edges. One demo losing a hierarchy it authored
is a demo's problem; two implementations reaching for the same missing type is
a specification's. The SceneScape sidecar is the obvious place to look, since
scene graphs are its native shape.
