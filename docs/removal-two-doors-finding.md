# For the spec queue: every consumer invents its own removal signal

*(Filed under `removal-two-doors-finding.md`, and the filename is kept
deliberately: it is the reference the OWM evidence, the spec-queue index and
the bridge census all cite. The thesis outgrew the name on 2026-10-17 when a
third invention turned up — see "The thesis, widened" below. A filed finding's
name is a reference, not a summary.)*

**Where this belongs.** The `spatialdds-web` queue. Written here because that
repository was the read-only instrument that validated this migration, and an
instrument edited during its own run proves less. **Origin:** the Open World
Model demo's 1.8 migration, 2026-10-17, whose recorder needed to write a
dispose and found two incompatible answers to what a dispose is. The bridge
census, the same day, found a third.
**Filing, not fixing.** Second evidence for finding 25.

Exhibit: [`recordings/dispose-without-tombstone.mcap`](../recordings/dispose-without-tombstone.mcap),
a 16-second capture made for this purpose and for nothing else.

---

## The two doors

`spatialdds-web` learns that an instance was removed in two places, and they
do not agree about what carries the fact.

| door | mechanism | source |
| --- | --- | --- |
| a recording loaded with `--mcap` | **infers** removal from a terminal sample whose `state` is `RETIRED` | `lib/spatialdds/spatialdds/server/gateway.py:338` |
| a journal replayed at startup | **reads** `spatialdds-web:dispose` metadata records | `publish.py:62` (label), `358` (written), `269–275` (read back) |

Neither is wrong on its own terms. The journal writes its own file, so it can
put a removal in a metadata record and trust it; the label's own comment
explains why it has to, and names the gap it stands in for:

> A dispose has no 1.8 record. `Depart` is a service withdrawal and nothing
> expresses a content withdrawal, so the journal carries disposes as MCAP
> metadata records under this name rather than as an invented typed message.
> Labeled, and recorded as a gap in the candidates entry.

A recording, by contrast, comes from outside and may carry no such record at
all, so inferring from `RETIRED` is the only thing the loader can rely on.

Both reasonable, and together they leave a hole.

## The hole, demonstrated against the real artifact

The exhibit carries one well-formed removal record, written under the
journal's own label with the journal's own field names, for an entity a live
DDS session genuinely disposed — `gnome_publisher` disposes its entity on
SIGTERM and never marks it `RETIRED`, which is correct: a visitor's ornament
leaving is not a venue retiring something.

Put that one file through both doors:

```
door 1  recording loader: ent:gnome:visitor latched=True, dispose events derived=0
door 2  journal path: REFUSED — This endpoint publishes exactly the two SSD/SCD
        successors: announce, content_announce, geo_anchor. Everything else it
        serves is read-only.

what the file carries, that neither door acts on:
   spatialdds-web:dispose  key='ent:gnome:visitor'
```

So the finding is not that the two doors disagree about a file — it is worse
and more specific than that:

**A tombstone-less removal of a non-publishable type is learnable by neither
door.** The recording loader sees the record and ignores it, because it reads
removal only from `RETIRED`. The journal path reads that exact label, but
refuses the type outright: its publishable set is `announce`,
`content_announce`, `geo_anchor`, and every `spatial.owm` type is read-only to
it. The record is correct, labeled, in the one format another SpatialDDS tool
already knows how to read — and inert everywhere in the system that wrote the
format.

The consequence for an evidence recording is the part that bit this batch. A
capture containing such a removal serves an entity that the session removed:
the file's own metadata says the gnome left, the served state says it is
present, and nothing reports a conflict. That is why the demo's main recording
is deliberately kept free of this shape, and why the removals in it are
retires — tombstone, then dispose — which is the one shape the loader honours.
Verified on that recording: two `RETIRED` tombstones, each immediately
followed by a dispose event to an N.5 subscriber, each carrying the reason
from its tombstone.

## The thesis, widened: a third invention, found by the bridge census

The web bridge, censused on 2026-10-17 as the last pre-1.8 consumer, turns out
to have invented a **third** mechanism — and its reasoning is as sound as the
other two:

```python
# A dispose carries no sample, so there is nothing to forward as an Entity.
# It goes out under its own type rather than as a half-filled one: a client
# that receives `{"entity_id": ...}` shaped like an Entity has to guess
# whether the missing fields mean "gone" or "not sent".
MODEL_ENTITY_DISPOSED_TYPE = "oarc.model_entity_disposed"
MODEL_RELATIONSHIP_DISPOSED_TYPE = "oarc.model_relationship_disposed"
```

That makes four in this program for one absent type, three of them live:

| # | mechanism | where | reads the others? |
| --- | --- | --- | --- |
| 1 | infer from `state == "RETIRED"` on the terminal sample | `spatialdds-web` recording loader, `gateway.py:338` | no |
| 2 | `spatialdds-web:dispose` MCAP metadata record | `spatialdds-web` journal path, `publish.py:62/269/358` | no |
| 3 | `oarc.model_entity_disposed` WebSocket message type | `SpatialDDS-demo` web bridge, `server.py:824` | no |
| 4 | `oarc.model_relationship_disposed` | same file, dead with the type | — |

**So the finding is no longer that two doors disagree about one file.** It is
that every consumer that needs to say "this is gone" invents a way to say it,
because there is nothing to reach for — and each invention is locally correct,
mutually unintelligible, and invisible to the others. Two repositories, three
live mechanisms, one program, no interoperability between any pair of them.

That is what a missing type looks like after enough consumers, and it is the
argument for closing it in the type system rather than blessing one of the
three conventions: blessing a convention makes the other two wrong
retroactively, where a typed record makes all three unnecessary.

## Why it is a filing and not a patch

The fix is not obviously "make the loader read the records". A recording is
untrusted input, and a metadata record asserting a removal is a claim the
loader cannot check against anything — which is presumably why the inference
exists. Candidate directions, none of them free:

- **Let the loader read the label.** Cheapest, and it makes a recording's
  removal claims as authoritative as a journal's, which is a trust change and
  not only a parsing change.
- **Widen what the journal accepts**, so a read-only type's removals replay.
  That reopens the question of what "publishable" means and is a bigger change
  than the one it fixes.
- **Close the hole in 1.9 instead**, with a typed removal record. This is
  finding 25's own direction: both mechanisms exist *because* a recording
  cannot carry a typed dispose. One type would delete both workarounds, and
  the label's comment already says the gap is on the candidates list.

The third is the one this finding argues for, because the other two paper over
a missing type with a convention — and there are already three conventions,
none of which meet. Adding a fourth by blessing one of them is not a fix; it
is the same problem with an official favourite.

## What the demo side does about it

Nothing that hides it. `tools/record_owm_session.py` writes removals under
`spatialdds-web:dispose` with that path's field names, so the records are
interoperable rather than private, and its docstring states plainly that no
component consumes them for an `--mcap` seed. Removals are recorded only on
latched lanes, because an instance ending on a VOLATILE lane had no
late-joiner state to stop being the answer for. And the main evidence
recording's capture window closes before any publisher exits, so the artifact
contains no removal that the loader would drop.
