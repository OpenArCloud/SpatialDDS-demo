# For the spec queue: the conformance suite validates no streamed sample

**Where this belongs.** The web-binding working board in `SpatialDDS-spec`,
beside finding 39, as a defect in `web-binding/conformance/run.py` rather than
in the binding it checks. It is written here because the spec repository was
read-only for this batch. **Origin:** the Open World Model demo's 1.8
migration, 2026-10-17, which is the first thing to run the suite against an
endpoint whose traffic is mostly VOLATILE.

---

## The claim

`every resource validates against its published schema` can report PASS
having validated **nothing** on a resource, and the pass is indistinguishable
from one that checked every sample. On the migrated OWM demo it reported
`7 instance(s)` against a recording carrying **581**.

## Why, exactly

`check_schema_validity` discovers every advertised resource, GETs each one,
and validates what comes back:

```python
assert code == 200, f"GET {t['path']} returned {code}"
schema = schema_for(schemas, t, index)
assert schema is not None, f"no published schema for type {t['type']!r}"
for doc in json.loads(body):
    _validate(doc, schema)
    n += 1
```

The GET on a resource returns its **latched collection**. A VOLATILE resource
has no latched instances, so the body is `[]`, the loop runs zero times, and
the resource contributes nothing to `n` — while still satisfying both asserts
above, because a 200 with an empty collection is the correct response for a
lane that holds no state. Everything that lane carries lives on the N.5
subscription, which this check never opens.

The suite already understood the hazard. There is a guard:

```python
if n == 0:
    raise Skip("every advertised resource was gated (401, no token) or empty, so "
               "no instance was validated — UNEXERCISED (zero observations) ...")
```

**But the guard is global, not per-resource.** One latched lane with one
instance is enough to carry `n` above zero and report PASS for the whole
endpoint. Every volatile lane beside it is then silently unexercised with no
UNEXERCISED note, which is the one outcome the finding-39 vocabulary exists to
prevent.

## Measured on a real endpoint

The migrated OWM demo's recording, served by the unmodified gateway:

| resource | durability | latched collection | samples on the stream |
| --- | --- | --- | --- |
| `/ut-austin-littlefield/world/entity/v1` | TRANSIENT_LOCAL | 7 | 77 |
| `/ut-austin-littlefield/world/model_pose/v1` | VOLATILE | **0** | **243** |
| `/ut-austin-littlefield/world/model_command/v1` | VOLATILE | **0** | **261** |

The suite reported `13 passed, 0 failed, 1 skipped` and, for this check,
`7 instance(s)`. **504 of 581 payloads — 87% of the traffic, and two of
the three types — were never validated against anything.** Both types are in the
binding's own durability table by omission (they default to not-latched), so
nothing here is a misconfiguration on the endpoint's side.

This is the same class as finding 36, "an empty expected value is a check that
cannot fail", one level up: the expected value is not empty, the *subject* is,
and the check reports on the subjects it found rather than the ones it was
told about.

## The machinery already exists in the suite

`§8.1 hello spatial in ten lines` opens the N.5 socket and reads it — our run
reported `10 lines, 1 replayed sample(s), marker then live`. So the suite can
already subscribe, count a replay, and recognise the `end_of_replay` marker.
What it does not do anywhere is validate a streamed payload against a
published schema. Closing this is a reuse, not a new capability.

## Prototype

`tools/validate_served_owm.py` in this repository is a working one, and it is
written to be read as a proposal rather than kept:

- Resolves each resource's type from `X-SpatialDDS-Type` on the resource
  itself, so nothing derives an FQN by string arithmetic on a path segment
  (the advertised `type` is `"model_pose"`, not a type name).
- Fetches schemas from `/.well-known/spatialdds/schemas` over HTTP rather than
  from a checkout, so what passes is what a consumer holding only the URL can
  check.
- Subscribes to every advertised resource, validates every event carrying
  `data`, and reports replay and live counts separately.
- Asserts exactly one `end_of_replay` per subscription.
- Exits non-zero on zero payloads: *an empty pass is not a pass.*

Result on the same endpoint: `581 payload(s) across 3 type(s), 0 failures`,
reconciling exactly with the recorder's own provenance — which is how the
count was confirmed to be every sample in the file rather than merely a large
number.

## Two things to settle before it becomes a check

**Where the window ends.** A recording's stream goes quiet and the prototype
stops on a silence. A live endpoint's does not, so the check needs a bounded
read — a sample cap, a time budget, or both — and it must report which bound
it hit. Against a live backend the count is a **floor, not a total**, and
saying so is the difference between this check and the one it replaces.

**Per-resource UNEXERCISED.** The fix is not only to validate the stream; it
is to move the zero-observation verdict from the endpoint to the resource. A
lane that genuinely carried nothing in the window is UNEXERCISED *for that
lane*, and should say so by name, so a reader can tell "quiet" from
"unchecked" — which today they cannot, for either reason.
