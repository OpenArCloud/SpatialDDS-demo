# OWM 0.2 relationship prototype: census

The stop before building `oarc.relationship`. It records what the venue can
honestly assert as edges, which predicates name them, the lane they travel on,
and what the browser needs. Every number here was read from the file named
beside it on 2026-10-25, at demo `02c6de2`, against `spatialdds 0.7.3`.

**The main finding: the brief's centerpiece edge cannot be born.** The brief
proposes "robot within keep-out zone" as the observed, bitemporal edge. The
venue is built so that this is never true. Only DECLARED extents become
keep-out (`keepout.py:65`): the pond and the shallows. The nav2 tier plans
around that mask. The kinematic tier refuses any step into it, and keeps 1.2 m
off (`plaza.py`, the `ROBOT_START_XY` note). An asserter for that edge would
publish nothing, and its lifecycle could never be recorded. §1.2 sets out the
options for the observed edge.

## 1. The edge inventory

### 1.1 What is on the bus

These are the seed entities (`model_service.seed_entities`), plus the two
entities other services publish:

| entity | basis | extent, venue-frame m (x, y) | publisher |
| --- | --- | --- | --- |
| `ent:fountain:littlefield` | OBSERVED | −2.39..25.81, −21.48..11.02 | model_service |
| `ent:pond:littlefield` | DECLARED | −1.5..25.5, −20.0..−4.75 | model_service |
| `ent:shallows:littlefield` | DECLARED | 9.5..20.0, −18.0..−10.0 | model_service |
| three ducks | AUTHORED | point | model_service (moved by duck_mover) |
| `ent:pond:observed` | DERIVED | declared pond + 0.9 ± 0.25 m per edge | pond_watch |
| `ent:robot:tb3` | OBSERVED | point | spatialdds_ros robot_node |

**The venue itself is not an entity.** "Venue contains pond", as the brief
puts it, has no subject to point at. The old hierarchy this demo published,
before the 1.8 migration retired its `Relationship` type
(`docs/relationship-candidate-1.9.md`), was fountain → pond → shallows, with
the pond containing each duck.

### 1.2 Candidate edges

| # | edge | basis | asserter | validity, honestly | what retires it |
| --- | --- | --- | --- | --- | --- |
| D1 | pond part-of fountain | declared | model_service | unset: true while asserted | the pond or the fountain retiring |
| D2 | shallows part-of pond | declared | model_service | unset | as D1 |
| D3 | duck within shallows (×3) | authored | duck_mover | unset: true while the clamp holds | duck_mover stopping, or its `--bounds` set to something else |
| O1 | robot within fountain | observed | zone asserter | valid_from on entry, valid_until on exit | the robot leaving, or the robot going UNOBSERVED |
| N1 | robot near duck (×3) | derived | near fuser | valid_from when the distance drops under the threshold | the distance rising past it |

**D1 and D2 are mereology, not geometry.** That is the point the 1.9
candidate made. Both pairs also nest geometrically: the pond's box is inside
the fountain's, and the shallows' box inside the pond's. But "the pond is
part of the fountain" is a claim about the venue, which the boxes cannot
carry. A DE-9IM predicate on these pairs would state only what any client can
compute from the extents. So the declared edges take a part-of predicate
(§2), and the geometric predicates are kept for observed and derived edges.

**D3's asserter is duck_mover, not the venue.** The ducks are kept inside the
shallows only because duck_mover clamps them there. Its default is
`--bounds shallows`, and `--bounds declared` or `derived` would make a
venue-asserted "within shallows" false (`duck_mover.py:89-94`). The service
that enforces a fact is the one that can honestly assert it. The basis is
`authored`, matching the ducks' own.

**O1, the observed edge, has three honest options. This needs your ruling.**

- **(a) Robot within the fountain** (recommended). The fountain's box is
  OBSERVED and not keep-out. The headline route, from the south plaza to the
  grass behind the pond, crosses into it and back out, so the edge is born
  and retired in every session.
  - The cost: the box is a locator, not a boundary. The seeder calls it
    "coarse on purpose" (`keepout.py` docstring). So the claim is true of the
    box, and only roughly true of the fountain.
  - That is the same locator-versus-boundary gap `keepout.py` already
    records. An edge would make it visible on the wire, which is evidence
    for the design document rather than a defect to hide.
- **(b) Robot within the derived pond.** The derived pond reaches past the
  declared box by 0.65–1.15 m, while the kinematic robot keeps 1.2 m off it.
  The edge would be born only when the wobble reaches the robot, so it would
  flicker.
  - It is the most interesting claim: by one service's measurement, the
    robot is in the water.
  - It is a poor film, because its lifetime is set by `WOBBLE_M`, not by
    anything the robot does.
- **(c) The keep-out moved onto the robot**, using `set_extent` on the
  shallows, which is a real verb. The edge is a violation, born when the
  zone is redrawn over the robot.
  - It retires only through `restore`, because neither tier will drive out
    of a lethal cell.
  - It is honest, but the robot does nothing in the film: the zone moves
    instead.

**N1's threshold already exists.** duck_mover's `AVOID_RADIUS_M = 15.0` is
the distance inside which the venue's ducks react to the robot
(`duck_mover.py:151`). Its comment records that the robot cannot come nearer
than about 4.2 m. A near fuser at 15 m therefore asserts exactly the relation
the venue already acts on, and it fires on the headline route: the duck
reaction instrument measures the same interval.

**Two asserters on one proposition (brief §3), proposed.** The staging needs
two claims about the *same* proposition. A second near fuser with a different
threshold does not give that. Below 6 m, both fusers would claim; between 6 m
and 15 m, only one would, and silence is not a contrary claim. And if the
threshold sits in `meta`, two "near" claims with different thresholds are two
propositions under one predicate URI. That is a design finding in itself
(§2).

The honest staging is two zone asserters for O1, reading different evidence:
- one reads the robot's latched `Entity`, on its cadence;
- one reads the `ModelPose` fast lane.

Both make the same claim, robot within fountain, from the same robot. Their
`valid_from` values differ by up to a latch interval, and neither is wrong.
That shows what the brief wants: validity is relative to the asserter, and
nothing crowns one claim.

**UNOBSERVED subjects.** Since `spatialdds_ros 0.4.1`, a dead sim leaves the
robot UNOBSERVED at its last pose. Should the zone asserter retire O1, with a
reason such as "subject unobserved", or hold it? I propose that it retires,
with that reason. This exercises the reason field with something other than
"left", and the case belongs in the design document either way.

## 2. Predicates

These were checked against OGC GeoSPARQL 1.1 (22-047r1, 2024-01-29), not
written from memory:

- `geo:` is `http://www.opengis.net/ont/geosparql#`.
- `geo:sfWithin` has the DE-9IM pattern `T*F**F***`, and `geo:sfContains`
  has `T*****FF*` (Table 2, clause 9.2). Their domain and range is
  `geo:SpatialObject`, and the text says the relations "may connect features
  as well as geometries". So a feature-to-feature edge such as O1 is in
  scope.
- **GeoSPARQL has no nearness relation.** Distance exists only as the
  functions `geof:distance` and `geof:metricDistance`, and their buffer
  counterparts. So "near" needs a demo-owned URI. I propose
  `https://oarc.dev/owm/0.2/predicate/near`, with `meta`
  `oarc.near.threshold_m = 15.0` and a note in meta that it is
  `geof:metricDistance(subject, object) < threshold`.
  - The finding: with the threshold in meta, two thresholds share one
    predicate. Proposition identity is keyed on (subject, predicate,
    object), so the shape cannot tell "near at 15 m" from "near at 6 m". The
    design document should choose between the threshold in the predicate
    URI and the threshold in the identity.
- **Part-of for D1 and D2:** Wikidata `wdt:P361` ("part of"), so
  `http://www.wikidata.org/prop/direct/P361`. That is the same source the
  entity types already use (`TYPE_BASIN`, `TYPE_FOUNTAIN`), and the 1.9
  candidate cites the same fact, Q810524 part-of Q483453.

## 3. Lane shape

- **Name:** `spatialdds/ut-austin-littlefield/world/oarc.relationship/v1`.
  `sd.dds.topic_name(SCENE, "world", "oarc.relationship")` builds it, under
  the ruling's rule: scene, then stream (`world`, the entity lane's), then
  the registered type name. That matches the `oarc.fusion_coverage`
  precedent.
- **QoS:** the demo's existing `MODEL_LATCHED` profile (RELIABLE,
  TRANSIENT_LOCAL, KEEP_LAST(1)), keyed on `id`. That mirrors the entity
  lane.
- **Gates:** `tests/test_topic_registry.py` fails on any new deployed name
  until it is classified. The new lane is classified as "demo-owned
  extension (§3.3.2)", the class the VPS lanes already use.
- **Optional members:** the pinned IDL has no `@optional`. Its optional
  members are `has_*` flags (`owm.idl:78, 80`). `confidence`, `valid_from`,
  `valid_until` and `derived_from` follow that.
- **Lineage refs:** the brief asks that `derived_from` name "the entity
  records it read". The near fuser reads poses from the `ModelPose` fast
  lane, and the robot's latched `Entity` is not restamped on every pose. So
  an entity id alone would not say which evidence was read. I propose each
  ref be `(topic, key, stamp)`, the sample read, so that acceptance item 3
  checks a stamp, not just a name.

## 4. The browser's read surface

**What the panel and highlight need:**
- The claims panel needs the lane's latched snapshot (N.3) and live events
  (N.5), carrying: subject, predicate, object, asserter, basis, `valid_from`,
  `valid_until`, confidence and lifecycle.
- It needs dispose-with-reason, so that a retirement reads as one in the
  panel.
- The containment highlight needs only O1's live state (subject and object
  ids). Nothing else.

**What the gateway lacks today**, all on the demo side, with no SDK change:
- **Types.** `bridges/web_bridge/binding.py` builds the gateway's types from
  the SDK's pinned IDL only (`pin.schema_record`). An extension has a
  `record` (`sd.dds` `Extension.record`), which the bridge adds.
- **Latching.** `gateway.DURABILITY` has no row for an extension, so it
  defaults to not latched and operator-scoped. The bridge sets latched
  itself.
- **Access.** The bridge needs an `ACCESS` row making the lane PUBLIC. The
  reasoning is the existing one: these are claims about a fountain, a robot
  and three rubber ducks, not about people. Without it, a cross-origin page
  cannot read the lane (blocker B, `docs/browser-credential-finding.md`).

Replay needs nothing: the gateway reads an extension's IDL from the
recording, which the tap embeds.

## 5. For the ruling

1. **O1:** (a) within fountain, (b) within derived pond, or (c) the zone
   moved onto the robot. I recommend (a).
2. **The inventory as amended:** part-of, not contains, for D1 and D2; no
   venue entity; D3 asserted by duck_mover.
3. **Coexistence staged as two O1 asserters** (latched record against fast
   lane), not two near thresholds.
4. **Predicates:** `geo:sfWithin`, `wdt:P361`, and a demo-owned `near` URI
   with its threshold in meta. The threshold-identity question is filed for
   the design document.
5. **Lane name** as in §3, and lineage refs as `(topic, key, stamp)`.
6. **Where the 0.2 design document lives.** It is not in any repository
   here. The spec reserves the question (Appendix E, "Reserved"), and the
   brief restates the shape. Shape findings need a target to be filed
   against as proposed edits.
