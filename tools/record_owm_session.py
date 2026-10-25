#!/usr/bin/env python3
"""Record a live session of the Open World Model demo to a typed MCAP.

    python3 tools/record_owm_session.py --seconds 20 \
        --out recordings/owm-littlefield.mcap

This subscribes to the demo's DDS topics through the SDK (`spatialdds[dds]`),
the same way any other consumer does, and writes what arrives. It is a capture, not an
authoring tool: there is no input file, nothing is computed, and the only
thing it adds is the typed envelope -- §3.3.1 topic names as channels, the
flattened 1.8 IDL as `omgidl` schemas, and XCDR2 as the message encoding.

**The bytes are CycloneDDS's, on purpose.** Each sample is re-serialized with
the serializer that put it on the wire, and the gateway that reads this file
back decodes it with an independent pure-Python XCDR2 reader. Two
implementations of the same encoding have to agree about every member of
every record, which is a property no single-implementation test can check --
and the one that found the dead base64 rule in the web binding's own mapping
table. A recorder that borrowed the gateway's encoder would have proved
nothing but self-consistency.

**Disposes are recorded.** The SDK's reader names the key of each removal
(it keeps the handle-to-key map this recorder used to keep for itself), and
each one on a latched lane is written as an MCAP metadata record under the
web binding's own label, `spatialdds-web:dispose`, with that path's field
names. The gateway's recording loader applies these since spatialdds 0.7.0,
ordered against the messages by `seq` on the log-time clock. (Until then it
read none of them, which the two-doors finding records,
`docs/removal-two-doors-finding.md`.)

**The IDL is the SDK's pin**: each schema record is
`spatialdds.server.pin.schema_record`, the IDL the installed `spatialdds`
ships, at SpatialDDS-spec's `v1.8` tag. It is the gateway's own flatten, so
the reader on the other side parses it as the single translation unit it
expects. It was this repository's vendored `idl/v1.8` at 9c3e1f2 until the
SDK adoption.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mcap.writer import Writer  # noqa: E402

import spatialdds as sd  # noqa: E402
from spatialdds.server import mcapmeta, pin  # noqa: E402

from spatialdds_demo.lanes import (  # noqa: E402
    MODEL_COMMAND, MODEL_FAST, MODEL_LATCHED, SCENE)

#: The lanes the demo publishes, by registered type, with the profile each is
#: read with. The relationship lane is absent because `spatial.owm/0.1`
#: carries no relationship type.
LANES = [
    ("model_entity", MODEL_LATCHED),
    ("model_pose", MODEL_FAST),
    ("model_command", MODEL_COMMAND),
]

#: Removals are recorded only on lanes whose data is latched, and the reason is
#: not tidiness. An instance ending on a VOLATILE lane had no late-joiner state
#: to stop being the answer for, so "it was removed" says nothing about the
#: world. Short-lived operator tools write the volatile lanes, for one command
#: and exit, so what their endings shed is bookkeeping about processes. The
#: latched world lanes are owned by the model service, the authority over what
#: the venue contains, so a dispose there is the only kind that is a claim
#: about the world rather than about a writer.
#:
#: (Before the SDK adoption every short-lived tool's writer also *disposed* its
#: instances on exit, autodispose being DDS's default; a first pass recorded
#: one of those as a removal. SDK writers never autodispose.)


#: The web binding's own label for a removal, and its field names, so the
#: gateway applies these records when it serves the file (it does since
#: spatialdds 0.7.0). `seq` is on the log-time clock the messages use: the
#: gateway orders a removal against messages by it. A counter there, as this
#: recorder wrote until the SDK adoption, sorts every removal before the first
#: message, where the gateway skips it.
DISPOSE_RECORD = mcapmeta.DISPOSE_RECORD


def record(out: Path, seconds: float, domain_id: int,
           driver: str = "tools/record_owm_session.py") -> dict:
    """Capture every lane for `seconds`, and return the provenance."""
    participant = sd.participant(SCENE, domain=domain_id)
    readers = [(type_name, participant.reader(type_name, qos_profile=qos,
                                              settle_s=0.3))
               for type_name, qos in LANES]
    latched = {r.topic for (_t, r), (_n, qos) in zip(readers, LANES)
               if qos.latched}

    out.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {r.topic: 0 for _t, r in readers}
    started = time.time()
    last_t = 0

    def clock() -> int:
        # Strictly increasing, so a dispose taken in the same nanosecond as
        # its terminal sample still sorts after it (the SDK Recorder's rule).
        nonlocal last_t
        last_t = max(time.time_ns(), last_t + 1)
        return last_t

    schemas: dict[str, tuple] = {}
    with open(out, "wb") as f:
        writer = Writer(f)
        writer.start()
        channel_ids: dict[str, int] = {}
        disposes: list[dict] = []
        unnamed_disposes = 0
        no_writer_ends = 0
        volatile_ends = 0

        deadline = started + seconds
        while time.time() < deadline:
            for type_name, reader in readers:
                topic = reader.topic
                for ev in reader.take():
                    if isinstance(ev, sd.Liveliness):
                        # A writer leaving, not a removal anyone performed.
                        if not ev.alive:
                            no_writer_ends += 1
                        continue
                    if isinstance(ev, sd.Dispose):
                        if topic not in latched:
                            volatile_ends += 1
                            continue
                        if not ev.key:
                            unnamed_disposes += 1
                            continue
                        disposes.append({
                            "seq": str(clock()), "topic": topic,
                            "key": ev.key,
                            "reason": ev.reason or (
                                "observed on the bus as a dispose; the reason, "
                                "if any, is on the RETIRED sample before it")})
                        continue
                    if not isinstance(ev, sd.Sample):
                        continue
                    sample = ev.data
                    fqn = sd.dds.fqn_of(sample)
                    if fqn not in schemas:
                        name, data = pin.schema_record(fqn.replace(".", "::"))
                        schemas[fqn] = (writer.register_schema(
                            name=name, encoding="omgidl", data=data), data)
                    if topic not in channel_ids:
                        channel_ids[topic] = writer.register_channel(
                            topic=topic, message_encoding="cdr",
                            schema_id=schemas[fqn][0])
                    writer.add_message(
                        channel_id=channel_ids[topic],
                        log_time=clock(),
                        # The demo stamps every record with the time it was
                        # authored, and that is the publish time. Taking the
                        # capture clock for both would claim this recorder
                        # knows when the sample was written, which it does
                        # not -- it knows when it arrived.
                        publish_time=(int(sample.stamp.sec) * 1_000_000_000
                                      + sample.stamp.nanosec),
                        # CycloneDDS's own bytes, so the gateway's independent
                        # decoder has to agree with them member for member.
                        data=sample.serialize(),
                    )
                    counts[topic] += 1
            time.sleep(0.02)
        for d in disposes:
            writer.add_metadata(name=DISPOSE_RECORD, data=d)
        writer.finish()
    participant.close()

    elapsed = time.time() - started
    return {
        "recording": out.name,
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "bytes": out.stat().st_size,
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(started)),
        "capture_seconds": round(elapsed, 2),
        "scene": SCENE,
        "dds_domain": domain_id,
        "types": "spatial.owm/0.1 (provisional)",
        "spec_tag": sd.SPEC_TAG,
        "spec_commit": sd.SPEC_COMMIT,
        "idl_source": f"the IDL spatialdds {sd.__version__} ships "
                      f"(SpatialDDS-spec {sd.SPEC_TAG}, {sd.SPEC_COMMIT})",
        "message_encoding": "cdr (XCDR2, serialized by CycloneDDS)",
        "schema_encoding": "omgidl",
        "schemas": {name.replace(".", "::"): hashlib.sha256(data).hexdigest()
                    for name, (_id, data) in sorted(schemas.items())},
        "samples": counts,
        "disposes": [{"topic": d["topic"], "key": d["key"]} for d in disposes],
        "dispose_records": len(disposes),
        "dispose_record_label": DISPOSE_RECORD,
        "dispose_records_consumed_by": (
            "the gateway's recording loader (spatialdds >= 0.7.0), ordered "
            "against the messages by seq on the log-time clock"),
        "unnameable_disposes": unnamed_disposes,
        # Instances whose writer left rather than disposed. Counted and not
        # recorded: a publisher exiting is not a removal.
        "no_writer_instance_ends": no_writer_ends,
        # Instances that ended on a VOLATILE lane: counted, never recorded.
        "volatile_instance_ends": volatile_ends,
        "driven_by": driver,
        "recorded_by": "tools/record_owm_session.py",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "",
                                     formatter_class=argparse
                                     .RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path,
                        default=REPO / "recordings" / "owm-littlefield.mcap")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--domain", type=int, default=None,
                        help="DDS domain id (default: SPATIALDDS_DDS_DOMAIN; "
                             "the SDK refuses with neither, rather than "
                             "joining domain 0)")
    parser.add_argument("--driver", default="tools/record_owm_session.py",
                        help="who or what drove the session, recorded in the "
                             "provenance verbatim. A scripted session "
                             "honestly labeled beats a person's session "
                             "assumed.")
    args = parser.parse_args()

    print(f"record: scene {SCENE}, domain {args.domain}, "
          f"{args.seconds:.0f}s")
    for type_name, qos in LANES:
        print(f"record: {sd.dds.topic_name(SCENE, None, type_name)}  "
              f"<- {type_name} ({qos.name})")
    prov = record(args.out, args.seconds, args.domain, args.driver)
    side = args.out.with_suffix(".provenance.json")
    side.write_text(json.dumps(prov, indent=2) + "\n")

    total = sum(prov["samples"].values())
    for topic, n in prov["samples"].items():
        print(f"record: {n:5d} samples on {topic}")
    for d in prov["disposes"]:
        print(f"record: dispose  {d['key']} on {d['topic']}")
    if prov["volatile_instance_ends"]:
        print(f"record: {prov['volatile_instance_ends']} instance(s) ended on "
              f"a VOLATILE lane; counted, not recorded as removals")
    if prov["no_writer_instance_ends"]:
        print(f"record: {prov['no_writer_instance_ends']} instance(s) ended "
              f"because a writer left, not by dispose; counted, not recorded")
    if prov["unnameable_disposes"]:
        print(f"record: {prov['unnameable_disposes']} dispose(s) arrived for "
              f"instances never seen alive, so unnameable; not recorded")
    print(f"record: {total} samples, {prov['dispose_records']} dispose "
          f"record(s), {prov['bytes']} bytes, sha256 {prov['sha256'][:16]}")
    print(f"record: provenance {side.relative_to(REPO)}")
    if total == 0:
        print("record: nothing arrived -- is the demo running on this "
              "domain? An empty recording is not evidence.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
