# The AR path picks its VPS by sort order

**Filed 2026-10-18, from the OpenVPS integration census. Not fixed in that
batch, and deliberately: the fix belongs to whoever next touches the AR
localize path, and this is the note that tells them.**

## The claim

`bridgeFindService` in `web/src/spatialdds_bridge.ts` discovers a service and
takes the first one:

```ts
const results = (payload as { results?: ServiceManifest[] }).results || [];
// The demo deployment announces one VPS. Taking the first result keeps
// that honest without pretending to a selection policy we do not have;
// results are ordered by service_id, so the choice is at least stable.
return results[0]?.service?.service_id || null;
```

The comment is honest about what it is doing and it is still
**aim-by-position**: the subject is chosen by its place in a sorted list
rather than by any property the caller cares about. With one VPS announced
that is invisible. With two, *which service localizes the demo is decided by
how their ids sort* — and nothing in the page, the bridge or the log would say
that a choice had been made at all.

This is the same shape as the front-page `search(...)[0]` defect fixed in
`spatialdds-web` by selecting on `Hit.serves()` rather than a cleverer index,
and it is the same family as the N.6 conformance check that POSTed to
`discover(ep)[0]` and so tested the wrong lane. Recorded here because the
pattern keeps recurring in discovery code, where "there is only one" is true
of the demo and not of the protocol.

## Why it matters now rather than in the abstract

The OpenVPS integration makes a second VPS real. `SPATIALDDS_MOCK_VPS=0`
exists so the demo's own mock *never starts*, and the census found that this
is a **load-bearing condition rather than a tidy one**: with both announced,
`svc:vps:demo/austin-downtown` sorts before `svc:vps:openvps/local`, so the
mock would win and the integration test would pass while measuring the mock.

That failure mode is worth naming precisely, because it is the dangerous kind:
not an error, but a green run against the wrong subject. The run would show a
successful localization, a geopose applied and the venue drawn — and prove
nothing about OpenVPS.

## What the fix is, when the path is next touched

Select by **coverage and kind**, which is what the caller actually means, and
make the choice visible:

- the search already filters `kind=VPS` and a geohash cell, so the remaining
  candidates are all *eligible*; the selection among them should state a
  policy rather than inherit one from `sort()`;
- where more than one is eligible, say so in the log with the ids, so a demo
  running against an unexpected service is visible rather than silent;
- a configured preference (an env naming the wanted `service_id`) is the
  smallest honest policy, and is what an integration test should assert
  against — "the service I asked for is the one that answered".

Not done in the integration batch because that batch is about proving the era
crossing, and changing selection while measuring selection would confuse the
two. The mock stays off by flag, which is correct and stated, and this stays a
known latency in the AR path.

## Classification

**Pre-existing**, latent, in the AR demo's 1.7 path. Not an integration
defect: nothing about OpenVPS or SpatialDDS 1.8 causes it, and it would have
been equally true of two mock VPSes. Not an instrument defect either — the
suite never had two services to confuse it, which is why it has never fired.
