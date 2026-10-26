# Recordings

Six committed sessions of the venue, each with a `*.provenance.json` beside
it. Three have an `*.evidence.md` saying what was measured on them, and the
sixth is measured in `docs/owm-02-worked-example.md`.

## Their era

The first five were captured **before the SDK adoption** (2026-10-17 to
2026-10-19): on this repository's own transport, with the IDL it then
vendored as `idl/v1.8` from SpatialDDS-spec `9c3e1f2`. Each provenance file
says so in its `spec_commit` and `idl_source`. They are kept, not recaptured,
because nothing they carry changed. Every lane in them is a
`spatialdds/ut-austin-littlefield/world/*` lane, which the adoption's topic
renames did not touch, and the wire is the same 1.8. A recording made since
the adoption names the SDK's pin in those two fields instead
(`tools/record_owm_session.py`).

Re-measured on the adopted stack (`spatialdds[dds,server]==0.7.3`,
2026-10-25), with the same instruments their evidence used:

- `owm-littlefield.mcap`, served by `python -m spatialdds.serve --mcap` and
  checked by `tools/validate_served_owm.py`: Entity 77, ModelCommand 261,
  ModelPose 243 payloads, 581 in all, 0 failures, as its evidence records.
  The spec's conformance suite at v1.8.2 against it: 15 checks, 14 passed,
  1 skipped (§8.2, no anchors).
- `robot-littlefield.mcap` and `robot-littlefield-on-library.mcap`, measured
  by `tools/measure_duck_reaction.py`: +2.03 m and +2.34 m, as before. A
  fresh capture on the adopted stack (`tools/capture_robot_session.sh`, kept
  as scratch) gives +1.10 m with the same sign; the size follows the ducks'
  wander, as it did across the earlier captures.

## The OWM 0.2 lifecycle film

`owm-02-lifecycle.mcap` is the first capture made on the adopted stack
(`spatialdds[dds,server]==0.7.3`, `spatialdds_ros==0.4.1`), and the first
with a relationship lane: the OWM 0.2 prototype's `oarc.relationship`, a
demo-owned extension whose IDL the recording embeds from its registration.
`tools/capture_owm02_lifecycle.sh` drove it against the stack and the nav2
tier: the robot into the fountain's extent and out, a zone watch killed and
the other stopped cleanly, both restarted, and the sim killed.
`tools/owm02_film_report.py` reads everything the worked example quotes off
this file and its provenance alone.

Its pinned-type payloads validate against the gateway's published schemas
(`tools/validate_served_owm.py`). Its relationship payloads cannot, because
the gateway publishes no schema for an extension, live or replayed
(spatialdds-py finding 72), so the report checks them by two implementations
agreeing instead.
