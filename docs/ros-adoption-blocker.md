# The adoption is blocked on one library field, and the census missed it

**Filed 2026-10-19, during the build the four-seam ruling authorised.** The
ruling was given on a census that did not cover this, so it is recorded here
rather than quietly worked around: the swap cannot proceed without a change to
`spatialdds_ros`, which this batch is not permitted to make.

## The blocker

`spatialdds_ros.RobotAdapter._record` hard-codes

```python
content_refs=[],
external_refs=[],
```

and `Config` has no field for either:

```
scene, entity_id, frame_id, anchor, source_id, domain,
ground_z, pose_source, nav2_action, type_uri, label
```

The demo's robot carries `content_refs = ["catalog:dcef7c31-1d5f-54aa-9344-899912f9d34a"]`,
and that reference is **how the page renders it as a robot rather than a pin**:

```ts
// web/src/spatialdds_bridge.ts:566
export function catalogRefId(entity: ModelEntity): string | null {
  for (const ref of entity.content_refs || []) {
    if (ref.startsWith('catalog:')) return ref.slice('catalog:'.length);
  }
  return null;
}
// :823
kind: asset?.uri ? 'model' : 'poi',
```

No `content_refs` means no `contentId`, which means no `assetFor(...)`, which
means `kind: 'poi'` and no `model_url`. The TurtleBot becomes a marker.

That fails the batch's own behaviour bar — *everything the robot lane does
today still happens* — and it fails it in the one way the bar exists to
prevent: silently, with a demo that still works and looks wrong.

**It cannot be worked around on the demo side.** The page could be repointed to
read the reference from somewhere else, but there is nowhere else: the library
publishes no reference at all, so there is nothing for any reader to find.
Inventing an entity-id-to-content-id table in the client would move a
published fact into consumer code — the opposite of the direction this
programme pushes, and it would make the demo's page diverge from what every
other consumer of the library can do.

**It blocks both lanes**, not just one. The kinematic robot and the nav2 tier
publish the same entity to the same page, so adopting the library for either
loses the model.

## A second, smaller one: the label key

`displayName` reads the property `demo.label`
(`web/src/spatialdds_bridge.ts:651`); the library publishes `label`. This one
*is* repointable on the demo side, exactly like the ruled `nav.status` change,
so it is not a blocker — recorded so it is not rediscovered, and so both
repoints happen together when the adoption lands.

## What this batch did instead

Per the brief's own clause — *"if a seam turns out to need a library change
rather than configuration, stop is not the answer: file it for spatialdds-ros,
keep robot_bridge for that lane with a dated note, and land everything else"*:

- `robot_bridge` **stays**, with a dated note at its head saying why and what
  unblocks it.
- The pin lands. `spatialdds_ros @ e8e5ec2` is a declared dependency, installed
  and importable, so the adoption is a one-commit change once the gap closes —
  and so the drift gate below has something to compare.
- The `spatialdds_idl18` drift gate lands and is **more** necessary now, not
  less: the second tree is on the system whether or not the robot lane uses the
  library's code.
- The three extraction findings this batch owns are fixed in passing, including
  the `/odom` docstring — which was going to be retired by replacement and now
  is not, so it gets corrected in place instead.

## For `spatialdds-ros`

Three filings, in the order they matter:

1. **`content_refs` must be configurable.** An adapter that cannot say what a
   robot *looks like* cannot be a consumer's only publisher of that robot. A
   `content_refs: list[str]` on `Config`, defaulting to `[]`, is the whole
   change. `external_refs` has the same shape and the same argument — a robot
   with a wikidata id has nowhere to put it either.

2. **The property key convention.** The library publishes `label`; this demo
   published `demo.label`. The library's is better (it is not demo-scoped) and
   the ruling already settled that the library's conventions win. Worth stating
   in the library's own docs as *the* convention so the next consumer does not
   have to diff two records to discover it.

3. **The anchorless startup note's wording.** Already ruled as a filing: *"no
   anchor configured … nothing earth-references them"* is true of the library
   and false wherever another participant owns the georeference, which is
   precisely the demo's case. One sentence distinguishing "no anchor and nobody
   else is publishing one" from "no anchor because somebody else is".

The first is a blocker; the second and third are polish. All three are
configuration surface rather than behaviour, which is consistent with the
census's finding that the seams were designed in the right places — the library
simply publishes a slightly narrower record than a real venue needs.
