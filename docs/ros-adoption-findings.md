# What the adoption found

**Filed 2026-10-19, during the swap the four-seam ruling authorised.** Three
findings, in the order they cost time. The first is the one the brief predicted
adoption would surface and the library's own acceptance could not: a defect
reachable only with a real peer.

---

## 1. `spatialdds_ros` crashes on an `InvalidSample` — for the library

> **Closed in spatialdds-ros 0.2.1 (`aa52a74`), 2026-10-19.** `take_samples`
> now reads sample state, `take_with_state` is the general read beneath it,
> and the library's suite has a peer with a lifetime
> (`APeerThatDies`). The workaround below is retired. The fix also closed a
> second call site nothing here had reached: `already_published` took
> `.entity_id` off a tombstone, so an adapter could crash *at startup*
> because an unrelated robot exited while it was checking its own key.

**The robot died the first time any peer joined and left the command lane.**

```
AttributeError: 'InvalidSample' object has no attribute 'subject_id'
  spatialdds_ros/adapter.py:218, via poll_commands
```

`spatialdds_ros.transport.take_samples` filters only `None`:

```python
return [s for s in (reader.take(N=n) or []) if s is not None]
```

CycloneDDS's `take()` also yields `InvalidSample` objects — dispose and
no-writer notifications carry instance state and no payload. `handle_command`
then reads `.subject_id` off one and raises. This repository's own transport
has always got it right, and the contrast is the whole finding:

```python
# spatialdds_demo/typed_transport.py:187
return [s.data for s in take_with_state(reader, n=n) if s.data is not None]
```

**Why the library's own 153 tests never saw it.** Nothing in them ever joined
and left a command lane. Every test wrote commands from a writer that outlived
the reader, so no invalid sample was ever produced. The defect needs a *peer
with a lifetime* — which is the first thing a real deployment supplies and the
last thing a unit test does.

Found here within a minute of the swap, because the demo's capture tool starts
its own robot, that robot is correctly refused by the one-writer-per-key guard,
and its participant then goes away — producing exactly the sample the library
cannot read.

**Worked around with a labelled note** in `robot_bridge._run_kinematic`: the
demo polls with its own filter and calls the library's *public*
`handle_command` per sample. The workaround and the loop it replaces are both
marked, and both come out when the library's `take_samples` is fixed.

**For the library:** `take_samples` should unwrap `.data` and drop invalid
samples, and a test should produce one — a writer created, a sample written,
the writer disposed or dropped, and the reader polled after.

---

## 2. Two transports disagree about where QoS goes — an observation

> **Closed in spatialdds-ros 0.2.1 (`aa52a74`), 2026-10-19**, and this
> finding had the mechanism wrong. The refusal is **not** a function of
> construction order. A topic name is unique within a participant and its QoS
> must agree among every *live* `Topic` object for that name — in either
> direction:
>
> | first (held) | second | |
> |---|---|---|
> | profile | default | `INCONSISTENT_POLICY` |
> | default | profile | `INCONSISTENT_POLICY` |
> | profile | profile | accepted |
> | default | default | accepted |
>
> Order only decides which call is refused, and **retention** decides which
> that is in practice: `typed_transport.get_topic` caches its topic objects
> deliberately, so this repository's convention persisted and the library's
> was the call that failed. The library now creates topics with default QoS
> and keeps §3.3.3 on the endpoints, where request/offered is decided.
>
> What produced the correction: the first probe written for the fix discarded
> its first `Topic`, which let the name go free and made every combination
> look accepted — the opposite error, and the one that would have shipped a
> vacuous test. `spatialdds-ros/docs/failability.md` §4 carries the table and
> both construction orders shown failing.

Both this repository and the library apply spec §3.3.3 profiles, and their
`QosProfile.to_qos()` results are *identical*. They disagree about **where**:

| | topic QoS | endpoint QoS |
|---|---|---|
| `spatialdds_demo.typed_transport` | default | the profile |
| `spatialdds_ros.transport` | the profile | the profile |

Both are legal DDS. In one participant they are not compatible: the adapter
creates the entity topic first, this repository's reader asks for the same
name with different topic QoS, and CycloneDDS refuses with
`DDS_RETCODE_INCONSISTENT_POLICY` — on the *second* creation, which makes it
look like a problem with whichever endpoint happened to be built later.

Fixed here by opening the lanes the library owns with the library's transport.
One convention per topic.

**For the library:** making topic QoS part of the contract is a choice every
consumer has to match, and nothing about the API says so. Worth either
documenting or dropping — the library's own
`_topic` already excludes Lifespan for a closely related reason, which is the
same lesson one policy short.

---

## 3. The gnome can lose a startup race — pre-existing

`scripts/gnome_publisher.py` writes one latched `Entity` and holds. In one
container cycle during this batch the binding never saw it: the entity was
provably on the bus (a fresh late-joining reader saw all nine instances,
`ent:gnome:visitor` among them) and the binding's pump had logged 89 entity
samples without it. `model-stack.spec.ts`'s "a fresh tab shows the venue"
failed on the missing gnome.

Restarting the publisher alone fixed it, which locates the fault in delivery
to one subscriber rather than in the publisher or the page.

**Not attributable to the swap.** The gnome is published by a separate process
this batch does not touch, and the probable trigger is mine but incidental: the
capture tool was run twice against the live domain, each run joining and
leaving with its own participants, and a single latched sample is exactly what
discovery churn can strand.

**What would fix it properly:** the gnome should re-assert on a cadence the way
the announce does. "Write once and hold" trusts TRANSIENT_LOCAL to survive
every future subscriber *and* all discovery churn, and a demo entity that can
silently fail to appear is worth more than one line of a publisher. Filed
rather than fixed because it is the venue's publisher, not the robot lane, and
this batch owns the latter.

## 4. The pin moves to the published release, and the acceptance is re-run

On 2026-10-21 the demo's `spatialdds_ros` moved from a git commit (`aa52a74`,
0.2.1) to the PyPI release `spatialdds_ros==0.3.0`. The demo is the library's
first consumer, and that proof is strongest against the artifact a user
installs. The gateway already embeds `spatialdds` from PyPI on the same
reasoning. From here the demo consumes releases. A commit pin is for the
window of developing against an unreleased fix.

0.3.0's library code is 0.2.1's. What changed is packaging: `install_requires`
is now `cyclonedds>=0.10`, which the demo image's 11.0.1 already meets, so the
install swaps nothing. Each of the three places the lane runs was checked to
import 0.3.0: the capture container, the bridge container (installed from
`requirements.txt` at start) and the rebuilt robot-tier image.

The robot lane's three claims, re-run on 0.3.0:

- **Reaction contrast.** A fresh `tools/capture_robot_session.sh` capture,
  measured by `tools/measure_duck_reaction.py`. Ducks sit **+1.49 m** closer to
  the corner furthest from the robot while it is inside the radius than while
  it is outside (237 against 171 observations). The same instrument gives
  +2.03 m on `recordings/robot-littlefield.mcap` and +2.34 m on
  `recordings/robot-littlefield-on-library.mcap`. The contrast holds with the
  same sign; its size follows the ducks' wander. The capture was scratch and
  replaces neither committed recording.
- **Goto from the web.** `web/tests/robot-goto.spec.ts`, "a tap on the ground
  sends the robot there", passed against the nav2 tier: asked for (22, −24),
  arrived at (20.97, −23.88).
- **Refusal surfaced.** `web/tests/nav-refusal.spec.ts`, "a refusal on the
  robot's record is reported, once", passed.

**One thing the capture showed that predates the bump.** Leg 2 of
`tools/capture_robot_session.sh` stops at (12.47, −21.25), short of its goal at
(12, −21), because a straight line from there enters the declared shallows.
The script's header says neither goal crosses a keep-out. All three captures
end at the same point, so this is not the library. It is a stale claim in the
header, filed here rather than fixed in this batch.
