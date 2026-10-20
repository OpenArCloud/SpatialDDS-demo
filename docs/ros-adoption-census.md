# The four seams where the demo meets `spatialdds_ros`

The robot lane moves onto the published library. `spatialdds-ros` is read-only
here at `e8e5ec2`: the demo becomes its first consumer, which is the adoption
proof the library's own acceptance could not provide. A gap in the library is a
filing for its repository, worked around here with a labelled note.

**The behaviour bar.** Everything the robot lane does today still happens —
kinematic robot, goto from the web, refusal reaching the asker, duck reaction
intact — now through the library.

All four seams turn out to be **configuration or naming, not library changes.**
Two things the swap surfaced outside them do need decisions, and both are
below.

---

## Seam 1 — keep-out in no-hardware mode

**The question.** The library ruled `CLEARANCE_M` out and owns no venue
geometry. The demo's kinematic robot refuses the pond via its own rasterised
mask. How does the demo's geometry reach the library's kinematic mode, and
does the refusal-at-the-pond behaviour survive identically?

**The finding: it does not need to reach it, because the demo keeps its own
sim.** The library's extension surface is wider than the brief's framing
assumed:

```
RobotAdapter(participant, config, navigator=None)
    observed(x, y, yaw)   refused(why)   poll_commands()   tick()
Navigator  = a Protocol: available() -> str|None,  send(x, y) -> str
```

`RobotAdapter` takes **any** `Navigator` and accepts poses from anywhere. So
the demo keeps `PlazaSim` and its mask exactly as they are, keeps
`KinematicNavigator` (which already satisfies the protocol — `available()`
returning `None`, `send()` setting a goal), and feeds `adapter.observed(...)`
and `adapter.refused(...)`. The library's own `KinematicSource` goes unused by
the demo.

The behaviour survives **identically because it is the same code**: the mask,
the 1.2 m clearance, the eight-point footprint and the abandonment message are
all the demo's and none of them move.

**And the library's `Bounds` could not have expressed the pond anyway**, which
is worth recording so nobody tries. `Bounds` is a box the robot must stay
*inside*; a keep-out is a region it must stay *outside*. They are complements,
not the same shape with a sign flipped — a venue with two ponds has no single
box that is "outside both". The library's bounds exist to give its own CI a
refusal path, which is all they claim to be.

**Verdict: configuration. No library change, no filing.**

---

## Seam 2 — georeference ownership

**The question.** The venue publishes its own `FrameTransform`. The robot must
run anchorless so the scene keeps the one claim.

**The finding.** The library's anchorless mode is exactly this, and it is
stronger than "publishes nothing": with no anchor there is **no writer on the
frames lane at all**, because to a reader a writer about to publish is
indistinguishable from one that never will.

The demo's configuration, constructed and validated against the library:

```
scene      ut-austin-littlefield
entity     ent:robot:tb3
frame      map/ut-littlefield-fountain
ground_z   -0.84
source_id  svc:robot:demo/tb3
domain     1                       <- see "the domain", below
georeference published?  False
```

`frame_id` carrying slashes is fine; only `scene` must be one topic segment.

**One wrinkle, and it is a filing.** The library's startup note for this mode
reads:

> no anchor configured, so no FrameTransform is published: this robot's entity
> and poses are on the bus and complete, and **nothing earth-references them**
> — a consumer that wants to place it on a globe cannot, and should say so
> rather than assume a position

That is true of the library and **false of this deployment**. In the demo the
venue *does* earth-reference the frame; anchorless is correct here precisely
*because* another participant owns the georeference, which is the opposite of
the absence the note describes. The note assumes the library is the only
possible publisher.

Worked around with a labelled note: the demo logs its own line saying the
venue owns the georeference. Filed for `spatialdds-ros` — the wording should
distinguish "no anchor and nobody else is publishing one" from "no anchor
because somebody else is".

**Verdict: configuration, plus one filing for the library's wording.**

---

## Seam 3 — `nav.status` naming

**The question.** The demo landed `demo.nav_status` (b2b826a) days before the
library landed its persist-until-next-acceptance semantics. Reconcile.

**The finding.** The library's convention wins, being the published one:

| | |
|---|---|
| demo publishes and reads | `demo.nav_status` |
| library publishes | `nav.status` |

Three read sites repoint: `web/src/app.ts:1306` (the log line) and `:1339`
(the entity panel's "Last refusal" row), and `web/tests/nav-refusal.spec.ts`
which injects the key into a borrowed record.

**The semantics change too, and this is the half that matters.** The demo's
refusal is passed to `_record()` once by `refused()` and is gone on the very
next pose — an *event*. **Confirmed live on the running demo today:** a
`goto (6, −10)` was accepted, the robot moved 0.00 m, the robot's log carried
the abandonment, and the entity on the lane carried **no `demo.nav_status` at
all** by the time anyone looked. That is why the refusal has read as silence
in the browser three times.

The library holds it until a new goal is **accepted** — cleared on acceptance
rather than arrival, so a declined command leaves the previous outcome
standing, because the requester still needs to see it. Adopting the library
adopts the fix; the demo's own half-fix retires with `RobotBridge`.

**Verdict: naming change in three places, and the persistence rule comes with
the library.**

---

## Seam 4 — command-lane coexistence

**The question.** `model_service` consumes `ModelCommand` verbs for venue
entities; the library consumes `goto` for its robot. What stops a command
being handled twice or dropped by both?

**The finding: subject filtering on both sides, and they are disjoint.**

`model_service.handle_command` returns `None` — silence, not a decline — for
any `ent:` subject it does not own, and its docstring is explicit about why:
*"'Not mine' is not 'no'. … a subject we do not own is silence."* It does not
publish `ent:robot:tb3`, so it does not own it.

The library's adapter returns `None` for any `subject_id` that is not its
configured `entity_id`, and for any verb that is not `goto`.

| command | model_service | library | handled by |
|---|---|---|---|
| `goto ent:robot:tb3` | silence (not owned) | accepts | library, once |
| `set_extent ent:pond:littlefield` | handles | silence (not its entity) | model_service, once |
| `goto ent:duck:east` | declines "unknown verb" | silence | model_service, once |
| `restore` (no subject) | handles | silence (verb) | model_service, once |

The gap worth a test in both directions is the fourth row's shadow: a
`goto ent:robot:tb3` with the robot lane **not running** is silence from
model_service and nothing from the library — dropped by both, with no record
anywhere. That is true today with `robot_bridge` too, and it is the case a
test should pin rather than discover.

**Verdict: configuration. Test both ways — handled exactly once when the lane
is up, and demonstrably nobody's when it is down.**

---

## Outside the four, classified

**1. How the demo consumes the library — needs a decision.** `spatialdds-ros`
is not on PyPI. It *is* publicly fetchable over HTTPS, verified:

```
git ls-remote https://github.com/OpenArCloud/spatialdds-ros
e8e5ec20bff1bbd40809276bb8a8606dfb41e965  refs/heads/main
```

so the container can `pip install
git+https://github.com/OpenArCloud/spatialdds-ros@e8e5ec2` with no SSH key.
Pinning to the commit rather than `main` is the recommendation: this is the
demo's first adoption and "the library changed under us" should be a
deliberate bump, not a rebuild artefact. **Classification: new, and the
decision is the ruling's.**

**2. A top-level package collision — pre-existing, latent, now load-bearing.**
Both repositories ship a top-level `spatialdds_idl18`: the demo has one at its
root, and the library installs one. They are **byte-identical today** (64 files
each, verified), because the library's was vendored from the demo's. With the
demo's repo root ahead of site-packages on `sys.path`, the demo's copy wins and
nothing changes.

The hazard is that this is invisible and silent. If either tree is regenerated
alone, the demo runs the library's code against the demo's types with no error
anywhere — and the failure mode of mismatched generated types is a decode that
produces plausible wrong values, not an exception. **Classification:
pre-existing, latent; the adoption is what makes it reachable.** Worth a gate
asserting the two trees agree, which is cheap and would have to fail before
anything subtle did.

**3. The domain.** The demo runs on `SPATIALDDS_DDS_DOMAIN` default **1**; the
library's `Config` defaults to 0. The demo must pass it. Trivial, recorded so
it is not discovered.

**4. The ROS tier's fate — needs a decision.** `robot_bridge._run_with_ros`
(85 lines) reads `/odom` and drives nav2 in the demo's own tier container. The
library's `robot_node` does the same job and better: its default input is TF
`map → base_link` rather than `/odom`, which is the finding the extraction
census filed against this very function. Either the tier adopts
`robot_node` — which fixes the `/odom` defect by replacement — or it keeps its
own and the defect stays. **Classification: in scope for this batch's "fixed in
passing" list if the tier adopts the node; otherwise a deferral with a dated
note.**

---

## What `robot_bridge` becomes

684 lines today:

| part | lines | fate |
|---|---|---|
| `RobotBridge` | 218 | **retires** — the library's `RobotAdapter` |
| `_run_with_ros` + `Nav2Navigator` | 85 | decision 4 above |
| `_run_kinematic` | 78 | **shrinks** to wiring the library |
| `PlazaSim` + `_extent_of` | 104 | **stays** — the venue's sim and mask |
| `already_published` | 23 | **retires** — the library has its own |
| `run` + `main` | 26 | **shrinks** |
| `KinematicNavigator` | 12 | **stays** — already satisfies the protocol |
| `_now`, `yaw_quaternion` | 6 | **retires** — the library has both |

So **247 lines retire outright** (218 + 23 + 6) and **116 stay** as
genuinely demo-specific geometry (104 + 12), with 104 more shrinking to
wiring — the shape the brief predicted. Counted from the file rather than
estimated, so the table above adds up.

**The duck reaction is unaffected and needs nothing.** `duck_mover` reads
`ent:robot:tb3` poses off the bus by entity id and its comment is explicit
that it does not care who publishes them: *"Nothing here knows what a robot
is… A second robot published under another id would be ignored."* The entity
id and the pose lane do not change.

---

## The ruling this census asks for

1. **Seam 1** — the demo keeps `PlazaSim`, `KinematicNavigator` and its mask,
   feeding the library's adapter. The library's `KinematicSource` is unused
   here. *(Recommended: yes — identical behaviour, no library change.)*
2. **Seam 2** — anchorless, with the demo logging its own line about who owns
   the georeference, and a filing for the library's wording.
3. **Seam 3** — repoint three read sites to `nav.status` and adopt
   persist-until-acceptance.
4. **Seam 4** — subject filtering as it stands, with a test both ways.
5. **Consumption** — pin `git+https://…/spatialdds-ros@e8e5ec2` in the demo's
   requirements. *(Recommended over tracking `main`.)*
6. **The `spatialdds_idl18` collision** — add a gate asserting the demo's tree
   and the installed library's tree agree. *(Recommended: yes, it is cheap and
   the failure it guards is silent.)*
7. **The ROS tier** — adopt the library's `robot_node` (fixing the `/odom`
   default by replacement), or keep `_run_with_ros` with a dated note.
   *(Recommended: adopt, since this batch already owns these files.)*
