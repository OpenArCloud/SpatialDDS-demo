# The OWM 1.8 migration, proven by the web binding

Acceptance evidence for the Open World Model demo's migration from its
demo-local `oarc_model` IDL to SpatialDDS 1.8's `spatial.owm/0.1`. The claim
under test is not that the demo looks right; it is that a toolchain written
without knowledge of this demo accepts what the demo produces.

Every output below is verbatim, and every figure is derived from the artifact
in this directory rather than carried over from an earlier one.

## What was run, and against what

| | |
| --- | --- |
| recording | `recordings/owm-littlefield.mcap` |
| sha256 | `84c3651b397c7638f5e8e0bae4a0d027e6b55c93b1613c54eb29027053bb5698` |
| captured | 2026-10-17T05:29:54Z, 44.02s of live DDS |
| driven by | tools/capture_owm_session.sh (scripted operator session) |
| types | spatial.owm/0.1 (provisional), from `idl/v1.8 (vendored from SpatialDDS-spec 9c3e1f2)` |
| encodings | schemas `omgidl`, messages `cdr (XCDR2, serialized by CycloneDDS)` |
| samples | 581 across 3 topics (77 entity, 504 on the two VOLATILE lanes) |
| removals | 2 dispose record(s): `ent:duck:east`, `ent:duck:catalog-pose` |
| gateway | `spatialdds-web` at `c6e147a`, **unmodified** (clean tree) |
| conformance suite | `SpatialDDS-spec` at `5dc27b0`, `web-binding/conformance/run.py` |

**This is the demo as it ships, not a harness.** The capture runs the real
entry points inside the demo image, under the env contract `require_dds_env`
insists on, with the recorder joined to the same bus by `--domain`:

```sh
docker run --rm -v "$PWD:/app" -w /app cyclonedds-python \
    bash tools/capture_owm_session.sh
```

That script's ordering is its contract rather than a detail: the recorder runs
in the foreground and every publisher is killed only after it returns.
`ModelPublisher.close` disposes every entity on purpose, so a window that
outlived its publishers would capture a venue nobody dismantled --- and those
disposes carry no tombstone, which the gateway's loader drops, so the artifact
would serve a venue its own metadata says was removed. The earlier harness
capture, `tools/run_owm_session.py`, is kept for putting traffic on a bus
quickly and is no longer what the committed recording comes from.

The bytes in the file are CycloneDDS's own XCDR2; everything that reads them
below is a different implementation.

### The session, and the two removal exhibits

One entity cannot be both exhibits, so there are two:

- **`ent:duck:east`** --- retired (tombstone, then dispose), then restored by
  `--restore`. The id-is-not-burned exhibit.
- **`ent:duck:catalog-pose`** --- retired, with no restore after it. The
  persistent-removal exhibit.

Both removals are retires, which is the one shape the recording loader
honours. Also in the window: the ducks moving under `duck_mover`, a
`set_extent` on the shallows through the real command lane, the gnome placed
and still placed when the capture closed, and `pond_watch`'s deliberate 0.9 m
disagreement with the venue.

2 instance(s) ended on a VOLATILE lane during the window and were
counted rather than recorded: an instance ending there had no latched state to
stop being the answer for, and DDS disposes a writer's instances when the
writer is destroyed, so every short-lived operator tool ends one on its way
out.

## 1. Served by the unmodified gateway

```sh
python3 tools/run_gateway.py \
    --mcap ../SpatialDDS-demo/recordings/owm-littlefield.mcap --port 8841
```

```
recording     /Users/fiberhog/AGENT_DEV/OARC/SpatialDDS-demo/recordings/owm-littlefield.mcap
              sha256 84c3651b397c7638f5e8e0bae4a0d027e6b55c93b1613c54eb29027053bb5698
types         the recording's own embedded IDL (owm-littlefield.mcap)
              SpatialDDS-spec 9c3e1f2
serving       http://127.0.0.1:8841
bearer token  owm-accept

  3 resource(s) in 1 domain(s), 7 latched instance(s), 582 live event(s)

  operator-scoped   /ut-austin-littlefield/world/entity/v1
                         1 replayed,    7 current,     78 live
  operator-scoped   /ut-austin-littlefield/world/model_command/v1
                         0 replayed,    0 current,    261 live
  operator-scoped   /ut-austin-littlefield/world/model_pose/v1
                         0 replayed,    0 current,    243 live
```

Three things there are the migration passing rather than the gateway being
generous. The scene-scoped topic names resolved into resource paths under
`/ut-austin-littlefield/world/...`, which only happens for a topic that parses
as §3.3.1's five segments. The types came from *the recording's own embedded
IDL* --- the gateway consulted no vendored `spatial.owm` of its own. And
`spatial.owm.Entity` is served `operator-scoped` and latched because the
binding's durability table already carries that type, so the access class is
the binding's ruling and not this demo's preference.

## 2. The conformance suite, verbatim

```sh
python3 ../SpatialDDS-spec/web-binding/conformance/run.py \
    --endpoint http://127.0.0.1:8841 --token <redacted>
```

```
conformance self-check OK (112 schemas load; canonical-form helpers available).
  PASS     schemas endpoint is self-describing
  PASS     every resource validates against its published schema  (7 instance(s))
  PASS     canonical form on the wire  (int64_string=7, enum_name=35, guard_literal=28, union_tagged=0; UNEXERCISED by this endpoint's data: ['union_tagged'])
  PASS     latched state is GET with ETag, Cache-Control and 304  (1 latched resource(s))
  PASS     access classes, both directions  (0 public, 3 operator-scoped; UNEXERCISED: public-cacheable (no public resource to test the cacheable path))
  PASS     DDS-join material optional-and-validated  (bootstrap=404, connection=absent, resolver=404)
  PASS     resolver is a checked surface (§7.5.2)  (resolver 404 — the endpoint is not an authority (169fabd); the resolve surface is UNEXERCISED (zero observations))
  PASS     service manifests match §8.2.3  (1 manifest(s))
  PASS     commands are POST, or cleanly absent  (POST -> 405 (no command lane advertised))
  PASS     the carried QoS subset is observable, the rest absent  (durability ['TRANSIENT_LOCAL', 'VOLATILE'] — both kinds carried)
  PASS     §8.1 hello spatial in ten lines  (10 lines, 1 replayed sample(s), marker then live)
  SKIPPED  §8.2 anchor resolve flow
           no served GeoAnchor carries a manifest_uri (the 1.8-revision field). UNEXERCISED: the anchor-resolve flow needs an anchor-referenced manifest to follow, and none is present. The reference gateway gains one when spatialdds-web re-authors the fountain with an anchor-referenced duck.
  PASS     §8.3 backend-blind, the same suite for both shapes  (no backend branch; runner inputs ['connect', 'endpoint', 'token'])
  PASS     §8.4 third-party reader, published schemas only  (7 instance(s) read from the published schemas alone)

14 checks: 13 passed, 0 failed, 1 skipped (skipped: §8.2 anchor resolve flow)
```

The one skip is honest and unavoidable: §8.2's flow needs a `GeoAnchor`
carrying a `manifest_uri` to follow, and this demo publishes no anchors. It is
UNEXERCISED, the suite's own vocabulary for a check whose subject matter is
absent.

`canonical form on the wire` reports `int64_string=7, enum_name=35,
guard_literal=28`. Those are the three lift rules exercised by this demo's
data. A migration that had broken one of them would show there as a count of
zero rather than as a failure, which is why the counts are quoted and not just
the totals.

## 3. Every served payload against the endpoint's published schemas

The suite validates *latched* instances --- here the seven the endpoint holds.
The pose and command lanes are VOLATILE, so they have no instance paths and
the suite never reaches them, which would leave 504 of 581 payloads
(87% of the traffic, two of the three types) unvalidated by the run
that passed above. That is a defect in the suite and it is filed as one:
[`docs/conformance-volatile-lane-finding.md`](../docs/conformance-volatile-lane-finding.md).
The tool below is its prototype.

```sh
python3 tools/validate_served_owm.py --endpoint http://127.0.0.1:8841 \
    --token <redacted> --out validation.txt
```

```
validated against the endpoint's own published schemas

  spatial.owm.Entity               77 payload(s) validated (1 replayed, 76 live)
  spatial.owm.ModelCommand        261 payload(s) validated (0 replayed, 261 live)
  spatial.owm.ModelPose           243 payload(s) validated (0 replayed, 243 live)
  non-sample events (dispose/marker): 12

OK: 581 payload(s) across 3 type(s), 0 failures
```

That is every sample in the file: 77 + 243 + 261 reconciles exactly with the
recorder's own provenance, and the schemas were fetched from the endpoint over
HTTP rather than read off disk, so what passed is what a consumer holding
nothing but the URL can check.

## 4. The removals, asserted rather than assumed

Each assertion against the entity it is about.

**The persistent-removal exhibit is gone from latched state after replay.**
Seven entities remain --- the five seed entities that were never retired, the
restored `ent:duck:east`, the gnome, and `pond_watch`'s derived
`ent:pond:observed` --- and `ent:duck:catalog-pose` is not among them.

**The id-is-not-burned exhibit is present and current**, `ACTIVE` with
`state_reason` cleared, having been tombstoned and disposed earlier in the
same capture.

**What an N.5 subscriber actually observes at removal**, read off the
subscription rather than presumed:

```
events a subscriber observes: {'sample': 77, 'end_of_replay': 1, 'dispose': 2, 'liveliness': 7}

dispose events observed: 2
   key='ent:duck:east' reason='taken in for the winter'
   key='ent:duck:catalog-pose' reason='kept in for the season; not coming back'

order, as observed:
   # 28 sample         ent:duck:east            RETIRED
   # 29 dispose        ent:duck:east
   # 61 sample         ent:duck:catalog-pose    RETIRED
   # 62 dispose        ent:duck:catalog-pose
```

So replay-derived removal **does** emit a dispose event, it carries the reason
from the tombstone, and the tombstone arrives immediately in front of it in
both cases. Reported because the alternative --- a removal that changes the
latched set and tells a subscriber nothing --- was the outcome this check
existed to detect.

**The dispose records are in the MCAP, under the journal's own label**, gated:

```
  2 records under spatialdds-web:dispose, keys ['ent:duck:catalog-pose', 'ent:duck:east']
  field shape matches the journal's own: seq, topic, key, reason
```

Written under `spatialdds-web:dispose` with that path's field names, so they
are **interoperable provenance rather than mechanism**. Which path consumed
them here: **neither**. `spatialdds-web`'s journal path reads this label, but
it refuses `spatial.owm` types outright; the gateway's recording loader
derives removal from the `RETIRED` terminal sample and from nothing else. No
line here should be read as the gateway having consumed a dispose record. That
asymmetry is filed in
[`docs/removal-two-doors-finding.md`](../docs/removal-two-doors-finding.md),
with its own exhibit.

## 5. The demo-able artifact

```sh
export SPATIALDDS_ENDPOINT=http://127.0.0.1:8841
export SPATIALDDS_TOKEN=<the gateway's token>
python3 examples/list_owm_entities.py
```

Six lines of the `spatialdds` client library, no DDS, nothing imported from
this repository. Its last line is the migration's gain made visible: the venue
frame says on the wire that it is metric --- `1 m/unit, SCALE_DECLARED` ---
where before every consumer had to assume it.

## What this does not prove

- **The relationship hierarchy.** Not in the recording because
  `spatial.owm/0.1` has no relationship type. The census's GAP row is the
  whole of the loss, and five tests went with it.
- **The browser view.** The web bridge is a later batch and still reads the
  pre-1.8 topics, so nothing it subscribes to is published any more.
- **The robot lane.** Same, and pinned explicitly at `_PRE18`.
- **Anchors and content resolution.** This demo publishes neither.
- **Soak.** 44.02 seconds of one venue on loopback DDS says nothing about
  hours, many writers, or a loaded network.

## The exhibit next door

[`dispose-without-tombstone.mcap`](dispose-without-tombstone.mcap) is **not**
evidence of the demo working and must not be read as part of this chain. It is
a 16.03s capture made for
[`docs/removal-two-doors-finding.md`](../docs/removal-two-doors-finding.md):
7 samples and 1 dispose record, produced by signalling the gnome
*inside* the window so its tombstone-less dispose lands in the file. The
awkward case stays in the program's path; it lives in the exhibit whose job is
to be awkward, not in the evidence whose job is to be true.
