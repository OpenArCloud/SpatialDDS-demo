#!/usr/bin/env python3
"""
What the OWM 0.2 lifecycle film shows, read off the recording alone.

    python3 tools/owm02_film_report.py recordings/owm-02-lifecycle.mcap

Every number in docs/owm-02-worked-example.md that comes from the film comes
from this, so the document quotes a measurement and can be checked against
the file. It reads only the MCAP and its provenance:

* **the lifecycle**: every edge's births and endings, by asserter, with the
  three retirement causes counted by the reason's prefix;
* **coexistence**: for each entry of the robot into the fountain, the two
  zone watches' valid_from, and the gap between them;
* **lineage**: every derived edge's `derived_from` resolved against the
  samples actually in the recording, by topic, key and stamp. An unresolved
  reference is reported, never assumed: the pose lane is best-effort, and a
  sample the recorder missed is a gap in the recording, not in the claim.
  A reference stamped before the recording's first message is counted
  apart: that edge was replayed from before the window, and its evidence
  was never in the file to find;
* **the payloads**: every relationship sample, decoded by the gateway's own
  XCDR2 reader against the IDL the recording embeds and projected to N.1,
  then read back by the SDK's strict `from_json` and compared with
  CycloneDDS's own deserialization, and passed through `sd.dds.validate()`.
  This stands in for validation against a published schema, which the
  gateway does not publish for an extension (spatialdds-py finding 72), and
  it is the two-implementation check the recorder's design rests on;
* **crash semantics**: the instances whose writer left without withdrawing
  (the provenance's latched_writer_left), and whether a dispose followed
  before the next sample on that id.

Exit 1 if any payload disagrees or fails validation, if a lineage reference stamped inside the window names a lane the
recording carries and is not found on it, or if any retirement's reason has
none of the three causes.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mcap.reader import make_reader  # noqa: E402

import spatialdds as sd  # noqa: E402
from spatialdds.server import gateway, webjson, xcdr2  # noqa: E402

from spatialdds_demo import claims  # noqa: E402
from spatialdds_demo.lanes import RELATIONSHIP_TOPIC  # noqa: E402
from spatialdds_demo_idl18.oarc_owm import Relationship  # noqa: E402
from spatialdds.dds.types.spatial.owm import Entity, ModelPose  # noqa: E402

DECODE = {"entity/v1": Entity, "model_pose/v1": ModelPose}


def stamp(t) -> str:
    return f"{t.sec}.{t.nanosec:09d}"


def main(path: Path) -> int:
    prov = json.loads(path.with_suffix(".provenance.json").read_text())
    evidence = defaultdict(set)                # topic -> {(key, stamp)}
    edges = []                                 # (log_time, Relationship)
    disposes = []                              # (seq, key, reason)
    t0 = None
    with open(path, "rb") as f:
        reader = make_reader(f)
        for _schema, ch, m in reader.iter_messages():
            t0 = t0 or m.log_time
            if ch.topic == RELATIONSHIP_TOPIC:
                edges.append((m.log_time, Relationship.deserialize(m.data)))
                continue
            for suffix, cls in DECODE.items():
                if ch.topic.endswith(suffix):
                    s = cls.deserialize(m.data)
                    evidence[ch.topic].add((s.entity_id, stamp(s.stamp)))
        f.seek(0)
        for md in make_reader(f).iter_metadata():
            if md.metadata.get("topic") == RELATIONSHIP_TOPIC:
                disposes.append((int(md.metadata["seq"]), md.metadata["key"],
                                 md.metadata["reason"]))

    def at(t: int) -> str:
        return f"{(t - t0) / 1e9:7.2f} s"

    bad = 0
    print(f"recording {path.name}: {prov['capture_seconds']} s, "
          f"{prov['samples'][RELATIONSHIP_TOPIC]} relationship samples, "
          f"{len(disposes)} relationship disposes, sha256 {prov['sha256'][:16]}")

    print("\n== births and endings, by asserter and predicate")
    born, causes = Counter(), Counter()
    for _t, e in edges:
        who = (e.asserter, claims.near(15) if e.predicate.startswith(
            claims.NEAR_BASE) else e.predicate)
        if e.state.name == "CREATED":
            born[who] += 1
    for _seq, _key, reason in disposes:
        cause = claims.cause_of(reason)
        causes[cause or "NONE"] += 1
        if not cause:
            bad += 1
            print(f"   NO CAUSE: {reason!r}")
    for (who, pred), n in sorted(born.items()):
        print(f"   {n:3d} born  {who:30} {pred.rsplit('/', 1)[-1]}")
    for cause, n in sorted(causes.items()):
        print(f"   {n:3d} retired, {cause}")

    print("\n== coexistence: the robot within the fountain, two asserters")
    zone = {claims.SF_WITHIN}
    opens = [(t, e) for t, e in edges
             if e.predicate in zone and e.subject_ref == "ent:robot:tb3"
             and e.state.name == "CREATED"]
    pending = {}
    for t, e in opens:
        side = "fast" if e.asserter.endswith("zone-fast") else "latched"
        pending[side] = (t, e)
        if len(pending) == 2:
            f_, l_ = pending["fast"][1], pending["latched"][1]
            gap = ((l_.valid_from.sec - f_.valid_from.sec)
                   + (l_.valid_from.nanosec - f_.valid_from.nanosec) / 1e9)
            print(f"   {at(min(pending['fast'][0], pending['latched'][0]))}  "
                  f"fast valid_from {stamp(f_.valid_from)}  latched "
                  f"{stamp(l_.valid_from)}  latched - fast = {gap:+.3f} s")
            pending = {}

    print("\n== lineage: derived_from against the recording's own samples")
    resolved = unresolved = before = 0
    first_sec = t0 / 1e9
    for t, e in edges:
        if e.basis.name != "DERIVED" or e.state.name != "CREATED":
            continue
        for ref in e.derived_from:
            hit = (ref.key, stamp(ref.stamp)) in evidence.get(ref.topic, ())
            if hit:
                resolved += 1
            elif ref.stamp.sec + ref.stamp.nanosec / 1e9 < first_sec:
                before += 1
            else:
                unresolved += 1
                if ref.topic in evidence:
                    print(f"   {at(t)} UNRESOLVED {ref.topic.rsplit('/', 2)[-2]} "
                          f"{ref.key} @ {stamp(ref.stamp)} ({e.object_ref})")
    print(f"   {resolved} references resolved to a sample in the recording, "
          f"{unresolved} not; {before} stamped before the window opened "
          f"(edges replayed from before it)")

    print("\n== payloads: two implementations, and the SDK's validate()")
    model = gateway.wire_model(path)
    dec, proj = xcdr2.Decoder(model), webjson.Projector(model, webjson.tables())
    fqn = "oarc_owm.Relationship"
    agree = disagree = 0
    with open(path, "rb") as f:
        for _schema, ch, m in make_reader(f).iter_messages(
                topics=[RELATIONSHIP_TOPIC]):
            native = Relationship.deserialize(m.data)
            served = proj.project(fqn, dec.decode(fqn, m.data))
            try:
                sd.dds.validate(native)
                same = sd.dds.from_json(Relationship, served,
                                        strict=True) == native
            except Exception as error:                # noqa: BLE001
                same = False
                print(f"   {native.id}: {error}")
            agree += same
            disagree += not same
    print(f"   {agree} relationship payloads: the gateway's projection reads "
          f"back strictly to CycloneDDS's sample, and validate() passes; "
          f"{disagree} do not")
    bad += disagree

    print("\n== crash semantics: writers that left without withdrawing")
    for left in prov.get("latched_writer_left", []):
        key = left["key"]
        seq = [(t, e.state.name) for t, e in edges if e.id == key]
        disp = [s for s, k, _r in disposes if k == key]
        print(f"   {key} writer left at {left['at_utc']}")
        for t, state in seq:
            print(f"      {at(t)} {state}")
        for s in disp:
            print(f"      {at(s)} DISPOSE")
    return 1 if bad or unresolved else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
