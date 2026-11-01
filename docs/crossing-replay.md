# The crossing replay: owm-replay/2 and its viewer

Batch 3 of the crossing scene. One static page, `web/crossing/index.html`,
and one asset beside it, `web/crossing/crossing-replay.json`, exported from
the committed full session (`scenarios/crossing/recording/crossing.mcap`,
docs/crossing-claims.md §3) by `scenarios/crossing/export_replay.py`. The
page is also the OWM page's hero: it imports nothing from this repository,
only three.js 0.149.0 pinned from cdnjs, IBM Plex from Google Fonts, and the
asset.

    python3 -m scenarios.crossing.export_replay            # regenerate the asset
    python3 -m scenarios.crossing.export_replay --check    # exit 1 if stale
    python3 -m http.server --directory web/crossing        # then open the page

It is served by any static server; there is no gateway dependency. It sits
outside the venue client's Vite inputs and `public/`, so the venue's build
and its Playwright suite are untouched.

## 1. The asset, owm-replay/2

owm-replay/1 (the OWM replay export brief) was specified for the fountain and
never built; /2 is its shape, extended additively, built here first.
111,310 bytes against a 300,000-byte ceiling; deterministic (regenerated, it
is byte-identical, which its test holds).

| key | what | from |
| --- | --- | --- |
| `schema` | `"owm-replay/2"` | |
| `meta` | recording, sha256, commit, capture date, driver, frame, `window` (session seconds and absolute stamps), `loop_s`, `track_dt`, and `honesty`: the two lines the page prints | provenance, git |
| `map` | buildings (footprint box, height), zones (box, label), lane centre lines, the rail's two gauge lines, the roadside unit's mount and range, the gate | each from its AUTHORED or DECLARED record, key and basis kept |
| `tracks` | per actor: kind, size (length, width, height), role, the bus records that describe it, and `x`, `y`, `h` (ENU yaw, degrees) on a 5 Hz grid from `t0` | the truth log: the simulated world |
| `gate` | gate-9's declared state changes, each with its record verbatim | the recording |
| `sight` | intervals in which car-a1's line of sight to ped-17 is blocked, and by what | the visibility model over the simulated world |
| `claims` | per edge id: name (C1–C5), asserter, subject, predicate, object, lane; `segments` (from, to, ending, reason verbatim, every revision with its basis and `valid_from`); `records`, every sample in the recording verbatim in the binding's JSON form | the recording |
| `events` | the narration: time, text, who, and `sources`, each a record (lane, key, stamp) or `truth` (the world's own record of what it applied, where the bus has none) | both, labelled |

Times are seconds from the window's start (session 20.0 s to 82.0 s, both
beats). 23 tracks, 5,026 samples; 5 claims, 23 samples; 22 narration
lines, 7 of them naming the truth log for something the bus does not carry
(a controller's command, a native car's braking, a stop's distance).

**What is simulated and what is a record, on the page:** the header says it
without interaction ("Simulated world … Real records …"), the stage carries
a "simulated world · recorded claims" badge, and the footer gives the
recording, its digest, the commit and the driver. Every claim the page
shows, and every provenance card, is a recorded sample; the vehicles, the
pedestrian and the line of sight are the simulator's.

## 2. The viewer

The look test's register (`crossing-look-test.html`): thin linework, no
textures, the basis colours, labels pinned to the world, a toast narrating
the lifecycle; the 2D prototype's chart-recorder strip and provenance card
(`owm-hero-prototype.html`). Driven entirely by the asset; the staged arrays
are gone.

- **Scene.** The street, buildings, crosswalk, stop zone, crossing zone and
  rail from the map records; every actor as its oriented box; the roadside
  unit on its pole; gate-9's arms from its declared states (down at
  `closed`, rising through `opening`), with warning lamps. A slow orbit whose
  focus moves from the crosswalk to the gate between 41 and 48 s.
- **Claims on the world.** C1 and C4 as lines from the unit to their
  subject, coloured by the basis of the revision in force (so P2 shows as
  derived turning observed); C2 colours the bus; C3 tints the crossing zone;
  C5 labelled with its lineage (← C3); car-a1's blocked line of sight, cut at
  the bus. Each label says what the claim says, its basis and, while it lies
  ahead, its `valid_from`.
- **The strip.** One trace per edge, high while asserted, coloured by basis
  revision by revision, a tick at each revision (C1's seven kerb
  re-predictions are visible), a dotted lead from each assertion to a future
  `valid_from`, a cap at each ending (filled: no longer holds; hollow:
  asserter stopped, none in this window, and the legend says so). Its cursor
  is the scene's clock; click or drag scrubs both.
- **Provenance.** Click a claim label, the gate, or a strip row: the sample
  in force at that moment, verbatim, and every sample of the edge.
- **Narration.** Both protagonists, including why car-a1 sits out the gate
  tooth: still held at the crosswalk for ped-17 when the gate declares.
- **Controls.** Claims on/off (off shows only the simulated street); pause;
  `prefers-reduced-motion` starts paused with a still camera. Light and dark,
  following the viewer's setting or `data-theme`.
- **Review and test parameters:** `t` (session seconds), `paused`,
  `claims=off`, `card=C1…C5|gate-9`, `theme=light|dark`, `speed`, `asset`.
- **The loader seam.** `ReplaySource.open(url)` yields the document; a live
  source would yield the same shape. Not built.

## 3. Checked

`scenarios/crossing/tests/test_crossing_replay.py`: the asset regenerates
byte-identical; shape and ceiling; every claim sample equals the
recording's, every ending its recorded reason; every narration line names a
source, every record source exists in the recording; tracks keep to their
grid; the page loads only the pinned three.js, the fonts and the asset; and,
in Chromium through web/'s Playwright (`web/crossing/check.mjs`), two loops
at 16× with no console error, and gate-9's arms read back from the drawn
geometry: closed, each lies across the roadway from its pivot with the tips
meeting over the lanes; open, each stands (skips, naming the install,
without Node or the browser). Plants, each confirmed in the file first: one changed byte in the
asset failed the regeneration test; a thrown error in the page's narration
failed the browser test; the arms mirrored (swung out past the shoulders,
as first drawn) failed the gate check. Also run once at real speed: two loops, no console
error.

**Found on the way, fixed:** Chromium's software GL dropped whole line
segments running far behind the camera (the 440 m road edges vanished while
the 4 m crosswalk stripes drew), so long lines are drawn as short pieces;
and the generator's grid came from `round(0.25 * 10)`, which Python rounds
to 2, so tracks were sampled at 0.2 s and read at 0.25 s until the grid
became a whole number of steps carried in `meta.track_dt`.
