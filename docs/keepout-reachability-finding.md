# The declared pond is a wall across the plaza, and two thirds of the lawn is one tap away from nowhere

**Filed 2026-10-19, after "the robot refuses to move to the grass" was
reported against the deployed demo for the third time. It is not a regression
and not a nav defect: the keep-out refusal has behaved this way since
`2bca874` (2026-09-30), and the kinematic tier declines by design. What had
never been measured is how much of the venue that leaves reachable.**

## The measurement

`ent:pond:littlefield` is `DECLARED` with an axis-aligned extent of

    x [ -1.50 ..  25.50]   y [-20.00 ..  -4.75]    27.00 x 15.25 m

in a venue roughly 28 m wide. `ent:shallows:littlefield` adds a second
declared box inside it. Those two are the whole keep-out --
`KEEPOUT_BASES = ("DECLARED",)` -- and the rasteriser fills each box whole.

So the keep-out is not an obstacle in the plaza. It is a **wall across the
plaza**, from one edge to the other, 15 m deep.

With the robot at its start position `ROBOT_START_XY = (17.0, -21.5)`, just
south of the wall, and the kinematic tier's 1.2 m clearance applied as eight
points on a circle:

    185 of 553 legal cells are reachable in one tap  (33%)

(Re-derived from `POND_MIN`/`POND_MAX`, `SHALLOWS_MIN`/`SHALLOWS_MAX`,
`ROBOT_START_XY` and `CLEARANCE_M`, on a 1 m lattice over
x [-6, 30], y [-26, 2] -- so it needs no running stack to reproduce, and it
moves if any of those four constants does.)

Everything north of the water -- the whole upper half of the venue, legal
ground the robot is permitted to stand on -- is unreachable by a straight
line, because every straight line to it crosses the declared box. That is the
entire content of the complaint: the grass *is* legal, and one tap cannot get
there.

## Update, 2026-10-19 evening: the start was 0.30 m from the wall

Reported again from the live stack, and this time not as "it won't cross the
pond" but as **"I can't even edge it onto the grass"** -- with a screenshot
showing visibly dry bank in front of the robot.

That is a second effect of the same rectangle, and it had not been measured.
The legal band south of the water begins at `y = -21.20` (the box's south
edge, `-20.00`, less the tier's 1.2 m clearance). `ROBOT_START_XY` was
`(17.0, -21.5)`: **0.30 m inside it.** In the session that prompted this the
robot had drifted to `y = -21.27`, leaving **0.07 m**, and every goal with
any northward component was refused on the first step:

    goto (24.00, -20.60)  ->  abandoned at (23.30, -21.27)

a 0.7 m nudge onto dry-looking ground. Driven the other way it moved at once,
`(23.30, -21.27) -> (25.92, -22.95)`, arrived. So the robot was never stuck;
it was pinned against a wall it cannot see, which reads identically.

`ROBOT_START_XY` is now `(17.0, -23.0)` -- 1.80 m of room, one-tap
reachability `192/553` (35%). The reachability barely moves, because the wall
still spans the plaza. **The room is the point.**

### The declaration also disagrees with the venue's own observation

Noticed while measuring the above, and not yet acted on:

| | x | y |
|---|---|---|
| `ent:pond:littlefield` (DECLARED) | `-1.50 .. 25.50` | `-20.00 .. -4.75` |
| `ent:pond:observed` (DERIVED) | `-2.41 .. 26.23` | `-21.15 .. -4.03` |

The south edges differ by **1.15 m, in the unsafe direction**: the venue's own
derived observation puts water *further south* than its declaration does. The
legal band starts at `-21.20`, which is **0.05 m** from the observed
waterline -- so the strip the robot is confined to is, by the venue's own
measurement, the water's edge.

Nothing checks that the DECLARED box contains the DERIVED one. That check is
worth having on its own terms: a venue contradicting itself about where its
water is should not be something a person finds by driving a robot into it.

### And the law was reported as changing 455 times

`refresh_keepout` diffed every known entity's extent, so the venue's
continuous republishing of `ent:pond:observed` -- a DERIVED claim, which by
policy cannot contribute -- reported a change on nearly every tick: **455
"keep-out from ..." lines, every one of them naming the same 41463 cells**,
against 112 pose heartbeats in the same log. A real reshape would have been
invisible in that. The change is now detected from `contributing()`, the same
function the rasteriser uses, so what is watched and what is rasterised
cannot drift apart -- and an entity that stops contributing (retired, or its
basis changed) now registers, which the extent diff could not see at all.

---

## It is a wall with a door

The fountain's extent is `OBSERVED`, so it never becomes keep-out, and the
ground off the plaza's west edge is therefore legal. Driven and verified, in
three straight segments:

    goto (-4, -23)   arrived after 34.5 s
    goto (-4,   0)   arrived after 34.5 s
    goto ( 8,   3)   arrived at (7.91, 2.98) after 19.5 s

and back the same way to within 0.05 m of the start. So the north lawn is
reachable; nothing is fenced off absolutely. nav2 finds that door on its own
-- `plaza.py` already notes that the start position decides whether it rounds
the west or the east end -- which is why the abandonment message points at
`scripts/run_robot_tier.sh`.

## What the fix is, and what it is not

**Not the policy.** Admitting `OBSERVED` extents into the keep-out was tried
and is worse, and `tests/test_keepout.py` keeps the reasoning: the fountain's
bounding box "forbade 916 m2 of mostly-dry plaza and swallowed the pond
whole, so reshaping the water changed the robot's world by zero cells", and
the robot's own start sits inside it -- under that policy the robot "began
life in a forbidden cell". `OBSERVED` locates, `DECLARED` legislates, and
that distinction is load-bearing.

**Not the fountain's existing extent**, for the same reason: it is a claim
about where the fountain is, not a model of its geometry, and it covers the
robot's start.

**The declaration.** The venue already tells its own consumers what is
wrong with it, in the pond entity's published `demo.note`:

> The venue declares a rectangle around a pool that is not one, so the box
> covers dry paving at the northern bays -- over-claiming, which is the safe
> direction for something a machine is told to stay out of.

Over-claiming *is* the safe direction, and that reasoning is sound in
isolation. What it does not account for is that this particular over-claim
spans the venue, so the safety is bought by making two thirds of the lawn
unreachable in one gesture. A U-shaped pool wants the arms of the U as
separate declared boxes -- which is already the established idiom here, since
the shallows is a second box beside the pond for exactly the reason that "no
single rectangle does both". Leaving the northern bays' dry paving out of the
declaration would open a corridor through the middle and need no planner
change at all.

Not done here because the waterline is a survey question, not a code one.
This records the cost precisely so the geometry can be chosen against a
number.

## Why it kept being reported as a regression

Because the robot went quiet. A `goto` is accepted before anyone can know
whether it is drivable, so the refusal arrives later -- and it used to arrive
only on the robot process's stdout. From the browser the sequence was
`robot:goto accepted` and then a motionless robot, which looks exactly like a
tap that was ignored, three times over. Refusals now ride the robot's latched
entity and the page reports them; see the `demo.nav_status` property and
`web/tests/nav-refusal.spec.ts`.
