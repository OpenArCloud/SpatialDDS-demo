# The OWM 1.8 migration, proven by the web binding

Acceptance evidence for the Open World Model demo's migration from its
demo-local `oarc_model` IDL to SpatialDDS 1.8's `spatial.owm/0.1`. The claim
under test is not that the demo looks right; it is that a toolchain written
without knowledge of this demo accepts what the demo produces.

Every output below is verbatim. The commands are in this file so a reader can
run them again rather than take the numbers on trust.

## What was run, and against what

| | |
| --- | --- |
| recording | `recordings/owm-littlefield.mcap` |
| sha256 | `edb365e39e1cfcbd58e8dbd58c0572e59095ffd6b263f4ac22502a32bab13e12` |
| captured | 2026-10-17T03:23:34Z, 18.03s of live DDS |
| types | spatial.owm/0.1 (provisional), from `idl/v1.8 (vendored from SpatialDDS-spec 9c3e1f2)` |
| encodings | schemas `omgidl`, messages `cdr (XCDR2, serialized by CycloneDDS)` |
| samples | 202 across 3 topics |
| gateway | `spatialdds-web` at `c6e147a`, **unmodified** (clean tree) |
| conformance suite | `SpatialDDS-spec` at `5dc27b0`, `web-binding/conformance/run.py` |

The recording is a **capture**, not an authoring: three processes on one DDS
domain, with the recorder subscribing like any other consumer. Reproduce it
with

```sh
python3 tools/run_owm_session.py --seconds 18 --domain 47
```

which starts the model service and the duck mover, sends one `set_extent`
through the real command lane, and runs `tools/record_owm_session.py` against
the bus. The bytes in the file are CycloneDDS's own XCDR2; everything that
reads them below is a different implementation.

## 1. Served by the unmodified gateway

```sh
python3 tools/run_gateway.py \
    --mcap ../SpatialDDS-demo/recordings/owm-littlefield.mcap --port 8831
```

```
recording     /Users/fiberhog/AGENT_DEV/OARC/SpatialDDS-demo/recordings/owm-littlefield.mcap
              sha256 edb365e39e1cfcbd58e8dbd58c0572e59095ffd6b263f4ac22502a32bab13e12
types         the recording's own embedded IDL (owm-littlefield.mcap)
              SpatialDDS-spec 9c3e1f2
serving       http://127.0.0.1:8831
bearer token  <redacted>

  3 resource(s) in 1 domain(s), 6 latched instance(s), 201 live event(s)

  operator-scoped   /ut-austin-littlefield/world/entity/v1
                         1 replayed,    6 current,     24 live
  operator-scoped   /ut-austin-littlefield/world/model_command/v1
                         0 replayed,    0 current,     89 live
  operator-scoped   /ut-austin-littlefield/world/model_pose/v1
                         0 replayed,    0 current,     88 live

  authority   none: 2 manifest(s) held for demo.spatialdds.org, but this endpoint answers as '127.0.0.1:8831'; pass --authority to serve resolution

  paste this into the second terminal:

    export BASE=http://127.0.0.1:8831
    export SPATIALDDS_ENDPOINT=http://127.0.0.1:8831

  discovery   http://127.0.0.1:8831/.well-known/spatialdds/search
  schemas     http://127.0.0.1:8831/.well-known/spatialdds/schemas
  publish     off (read-only; --publish enables it)
```

Three things in that banner are the migration passing rather than the gateway
being generous. The scene-scoped topic names resolved into resource paths
under `/ut-austin-littlefield/world/...`, which only happens for a topic that
parses as §3.3.1's five segments. The types came from *the recording's own
embedded IDL* -- the gateway did not consult a vendored `spatial.owm` of its
own. And `spatial.owm.Entity` was served `operator-scoped` and latched because
the binding's durability table already carries that type, so the access class
is the binding's ruling and not this demo's preference.

## 2. The conformance suite, verbatim

```sh
python3 ../SpatialDDS-spec/web-binding/conformance/run.py \
    --endpoint http://127.0.0.1:8831 --token <redacted>
```

```
conformance self-check OK (112 schemas load; canonical-form helpers available).
  PASS     schemas endpoint is self-describing
  PASS     every resource validates against its published schema  (6 instance(s))
  PASS     canonical form on the wire  (int64_string=6, enum_name=30, guard_literal=24, union_tagged=0; UNEXERCISED by this endpoint's data: ['union_tagged'])
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
  PASS     §8.4 third-party reader, published schemas only  (6 instance(s) read from the published schemas alone)

14 checks: 13 passed, 0 failed, 1 skipped (skipped: §8.2 anchor resolve flow)
```

The one skip is honest and unavoidable: §8.2's flow needs a `GeoAnchor`
carrying a `manifest_uri` to follow, and this demo publishes no anchors. It is
UNEXERCISED, which is the suite's own vocabulary for a check whose subject
matter is absent, and the distinction between that and a failure is itself a
finding this program filed (finding 39).

Worth reading twice: `canonical form on the wire` reports
`int64_string=6, enum_name=30, guard_literal=24`. Those are the three lift
rules exercised by this demo's data -- six int64 members serialized as decimal
strings, thirty enums as names, twenty-four guarded members checked against
their guards. A migration that had broken one of them would show up there as a
count of zero, not as a failure, which is why the numbers are quoted and not
just the totals.

## 3. Every served payload against the endpoint's published schemas

The suite validates *latched* instances, which here is the six entities. The
pose and command lanes are VOLATILE, so they have no instance paths and the
suite never reaches them -- which would leave 177 of the 202 payloads, and two
of the three types, unvalidated by the run that passed above. That is a defect
in the suite and it is filed as one:
[`docs/conformance-volatile-lane-finding.md`](../docs/conformance-volatile-lane-finding.md).
The tool below is its prototype.

```sh
python3 tools/validate_served_owm.py --endpoint http://127.0.0.1:8831 \
    --token <redacted> --out validation.txt
```

```
validated against the endpoint's own published schemas

  spatial.owm.Entity               25 payload(s) validated (1 replayed, 24 live)
  spatial.owm.ModelCommand         89 payload(s) validated (0 replayed, 89 live)
  spatial.owm.ModelPose            88 payload(s) validated (0 replayed, 88 live)
  non-sample events (dispose/marker): 9

OK: 202 payload(s) across 3 type(s), 0 failures
```

That is every sample in the file: 25 + 88 + 89 reconciles exactly with the
recorder's own provenance, and the schemas were fetched from the endpoint over
HTTP rather than read off disk, so what passed is what a consumer holding
nothing but the URL can check.

## 4. The demo-able artifact

```sh
export SPATIALDDS_ENDPOINT=http://127.0.0.1:8831
export SPATIALDDS_TOKEN=<the gateway's token>
python3 examples/list_owm_entities.py
```

```
ent:duck:catalog-pose        FAST    AUTHORED (  14.06,  -8.47)  ACTIVE  Waddles
ent:duck:east                FAST    AUTHORED (  23.19, -17.82)  ACTIVE  Skipper
ent:duck:west                FAST    AUTHORED (  11.79,  -7.95)  ACTIVE  Bobbin
ent:fountain:littlefield     STATIC  OBSERVED (  11.71,  -5.23)  ACTIVE  Littlefield Fountain
ent:pond:littlefield         STATIC  DECLARED (  12.00, -12.38)  ACTIVE  Pond
ent:shallows:littlefield     STATIC  DECLARED (  14.75, -14.00)  ACTIVE  Shallows

6 entities in spatialdds-web-ut-austi, frame map/ut-littlefield-fountain (ENU, 1 m/unit, SCALE_DECLARED)
```

Six lines of the `spatialdds` client library, no DDS, nothing imported from
this repository. The last line is the migration's gain made visible: the venue
frame now says on the wire that it is metric -- `1 m/unit, SCALE_DECLARED` --
where before every consumer had to assume it.

## 5. The demo still runs as it did, through its own entry points

Everything above goes through a harness that constructs participants directly,
which is how this repository's tests have always driven the stack. That proves
the stack and not the demo: the shipped entry points call `require_dds_env`
and insist on `CYCLONEDDS_URI=file:///etc/cyclonedds.xml`, the container
contract, and none of them had been run since the migration.

So they were, in the demo image, under that contract, on a clean DDS domain:

```sh
docker run --rm -v "$PWD:/app" -w /app cyclonedds-python bash -c '
  export SPATIALDDS_TRANSPORT=dds CYCLONEDDS_URI=file:///etc/cyclonedds.xml
  export SPATIALDDS_DDS_DOMAIN=51 PYTHONPATH=/app
  python3 -m spatialdds_demo.model_service &
  python3 scripts/move_duck.py ent:duck:west 9.0 -12.0
  python3 scripts/reshape_pond.py --shrink
  python3 scripts/retire_entity.py ent:duck:east "winter"
  python3 scripts/retire_entity.py --restore
  python3 scripts/gnome_publisher.py
  python3 -m spatialdds_demo.pond_watch
  python3 -m spatialdds_demo.duck_mover'
```

Verbatim, trimmed to one line each:

```
ent:duck:west: (10.50, -13.00, -1.42) -> (9.00, -12.00, -1.42)
reshape: x -1.5..25.5, y -20.0..-4.8  ->  x 5.2..18.8, y -16.2..-8.6
retire: order tombstone -> disposed
retire: a retired id is not burned — the same id can be published again
gnome: ent:gnome:visitor — AUTHORED/SLOW, pose (20.00, -21.00, -0.98) in map/ut-littlefield-fountain
pondwatch: it disagrees with the venue by about 0.9 m, on purpose
mover: stopping after 116 moves. The ducks stay where they are
```

And the dropped verb refuses by name rather than by silence:

```
$ python3 scripts/retire_entity.py --edge rel:contains:pond-duck-east "x"
usage: retire_entity.py [-h] [--restore] [--domain DOMAIN] [entity_id] [reason]
retire_entity.py: error: unrecognized arguments: --edge
```

**This run found a defect, which is the reason for doing it.** The service
wrote **zero bytes for its entire life** when stdout was not a terminal: the
banner naming the domain, the type source, the scene and every topic, and
every line of the command lane after it, arrived only when the process was
killed. Measured at 0 bytes after 8 seconds and 1354 after SIGTERM. In a
terminal it looked perfect, because an interactive stdout is line-buffered
already -- so the failure was invisible in the shape a developer checks and
total in the shape the demo ships in, where `docker run` captures the logs.

Fixed by `live_log()`, called by all three long-lived OWM entry points, and
guarded by `tests/test_live_log.py`: 1315 bytes readable 7 seconds in with the
service still running, against 0 before. The test's own first version passed
for the wrong reason -- it looped on `readline()`, which blocks, so it sat
through the subprocess's whole sleep and collected the line at exit -- and its
injection case is what caught that. It now uses `select` with a real deadline:
the banner arrives in 0.135s with the fix and nothing arrives in 6.0s without
it, the writer alive in both.

## What this does not prove

- **The relationship hierarchy.** It is not in the recording because
  `spatial.owm/0.1` has no relationship type. The census's GAP row is the
  whole of the loss, and five tests went with it.
- **The browser view.** The web bridge is a later batch and still reads the
  pre-1.8 topics, so nothing it subscribes to is published any more.
- **The robot lane.** Same: the robot bridge publishes its entity and reads
  commands on the pre-1.8 names.
- **Anchors and content resolution.** This demo publishes neither.
- **Soak.** 18 seconds of one venue on loopback DDS says nothing about hours,
  many writers, or a loaded network.
