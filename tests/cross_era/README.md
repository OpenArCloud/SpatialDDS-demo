# Cross-era exhibit: a 1.7 participant against the 1.8 demo

**The claim.** A participant built from nothing but SpatialDDS **1.7**
generated types, the spec's topic names and the §3.3.3 QoS profile table, with
no code from this repository, interoperates with this **1.8** demo in both
directions: it reads the demo's announces and one announced data lane, and
the demo discovers it, resolves the type it announces, reads its data and
sees it depart.

This is the one place in the repository where a second era is kept, and it
is kept on purpose. Everything else in the repository is 1.8.

## What is here

| file | what it is |
|---|---|
| `interop_probe.py` | the participant. **Byte-identical** to `tests/interop_probe.py` as it last ran in the 1.7 era (sha256 `619422b0…`, the blob at `bcea0c4`); its docstring's run command names that old path |
| `interop_driver.py` | drives the probe against the demo, both ways. Was `tests/test_interop.py`; named so pytest does not collect it, which would put this directory on the test process's `sys.path` |
| `spatialdds_idl/` | the subset of the 1.7 generated tree the probe needs — see below |
| `MANIFEST.sha256` | the digest of every file above. Its own digest is pinned in `tests/test_cross_era_fixture.py`, outside this directory |

Run it in the demo image (the standard tier's "cross-era interop probe" lane):

```bash
docker run --rm --network host -v "$PWD:/app" -w /app -e PYTHONPATH=/app \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \
  python3 -m unittest tests.cross_era.interop_driver
```

**The probe is isolated by the import system, not by convention.** The
driver starts it with `PYTHONPATH` set to this directory alone and from this
directory, so `spatialdds_idl` is the subset here and `spatialdds_demo` does
not import at all. A probe that reached for a demo helper would fail to start
rather than quietly prove the demo can talk to itself.

## The subset, derived rather than chosen

<!-- exhibit-subset: modules=19 packages=23 -->
`spatialdds_idl/` holds **19 of the 1.7 tree's 29 generated modules**, in 23
packages,
computed by `tests/test_cross_era_fixture.py`'s `type_closure()`: start from
every `spatialdds_idl` name the probe imports (including the two provisional
modules it imports by string), then follow every import a reached module
makes and every `spatialdds_idl.<module>.<Type>` its annotations and typedefs
spell. Package `__init__` files are not followed: idlc's import every sibling
eagerly, which would make the closure the whole tree by construction.

Left out: `oarc_demo` (the demo's own 1.7 types), `spatial.mapping` (six
modules), `spatial.slam_frontend`, `spatial.core.GnssService`, and
`_field_aliases`.

- **The 19 modules are byte-identical** to the 1.7 tree at `bcea0c4`.
- **Three `__init__.py` files are not**: the root, `spatial` and
  `spatial.core`, each filtered mechanically to drop the imports and
  `__all__` entries of the modules left out. Those are the only bytes here
  that idlc and `scripts/generate_types.py` did not write. The headers still
  say "Regenerate with…"; this tree is never regenerated.
- **A type-level walk finds 18, not 19.** Following field types alone misses
  `spatial.events.enums.zone_kind_enum`. `_events.py` defines a module-level
  `ZoneKind` typedef pointing into it, though no type the probe uses has a
  `ZoneKind` field. A module kept whole keeps everything it names, so the
  module-level closure is the right one.

`tests/test_cross_era_fixture.py` re-derives the closure over the subset
itself on every run. It requires the subset to be closed (nothing referenced
is missing) and minimal (nothing present is unreached). It also checks every
file against the manifest, and the manifest against its pinned digest.

## Provenance

- **Types:** generated from `idl/v1.7`, vendored verbatim from
  SpatialDDS-spec tag `v1.7` (2026-08-23), by idlc 11.0.1 in the demo image.
- **Probe:** its §3.3.3 profile table is a transcription of the 1.7 spec
  table, deadlines included. Why the deadlines matter is
  `tests/test_deadline_qos.py`, which was split out of the driver so the
  exhibit does not freeze a test of 1.8 behaviour.
- **Retirement:** the full 1.7 tree, `idl/v1.7` and `idl/demo` were
  retired on 2026-10-21, in the same change that built this directory, so
  the repository never had neither. Git history holds the rest.

## Changing it

Don't, unless the claim itself changes. If it must change, the edit is
deliberate in three places, by design:

1. the file;
2. `MANIFEST.sha256`, via `python3 tests/test_cross_era_fixture.py --write-manifest`;
3. the pinned digest in `tests/test_cross_era_fixture.py`.

The driver imports the demo's `spatialdds_demo.stream` for its half of the
exchange. If that API changes, the driver must be edited, and the manifest
records that it was.
