#!/usr/bin/env python3
"""Record the crossing scene to a typed MCAP.

    python3 -m scenarios.crossing.record --domain 61 --seconds 130 \
        --out recordings/crossing.mcap

The same file the venue's recorder writes (`tools/record_owm_session.py`),
for this scene's lanes: §3.3.1 topic names as channels, the SDK pin's
flattened IDL as `omgidl` schemas (an extension's from its registration),
CycloneDDS's own XCDR2 bytes as `cdr` messages, and each removal on a latched
lane as the web binding's `spatialdds-web:dispose` metadata. The venue's
recorder is bound to the fountain's scene and lanes, and the fountain is not
this scene's to change, so this is its own capture loop, built from the same
shared pieces imported unchanged: `lanes.schema_record`, `lanes.open_reader`,
the SDK's `mcapmeta.DISPOSE_RECORD`. That the two formats agree is checked
the way the venue's is, by the gateway serving the file and a validator
reading it back (docs/crossing-census.md).

The lanes add one the venue's recording lacks: the scene frame's
georeference (`frame_transform`, on stream "frames"), which a replay needs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import signal
import sys
import time
from pathlib import Path

from mcap.writer import Writer

import spatialdds as sd
from spatialdds.server import mcapmeta

from spatialdds_demo.lanes import (
    MODEL_FAST, MODEL_LATCHED, RELATIONSHIP, open_reader, schema_record)
from scenarios.crossing.scene import LANES, SCENE

#: Lane type -> the profile it is read with.
PROFILES = {"model_entity": MODEL_LATCHED, "model_pose": MODEL_FAST,
            RELATIONSHIP: MODEL_LATCHED, "frame_transform": MODEL_LATCHED}


def record(out: Path, seconds: float, domain: int, driver: str) -> dict:
    participant = sd.participant(SCENE, domain=domain)
    readers = [(t, open_reader(participant, t, LANES[t],
                               qos_profile=PROFILES[t], settle_s=0.3))
               for t in LANES]
    latched = {r.topic for t, r in readers if PROFILES[t].latched}
    counts = {r.topic: 0 for _t, r in readers}
    last_t = 0

    def clock() -> int:
        # Strictly increasing, so a dispose taken in the same nanosecond as
        # its terminal sample still sorts after it (as the venue's does).
        nonlocal last_t
        last_t = max(time.time_ns(), last_t + 1)
        return last_t

    schemas: dict = {}
    disposes, writer_left = [], []
    unnamed = no_writer = volatile_ends = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    # SIGTERM ends the capture early and still writes a whole file: the
    # session runner sends it once every participant has closed.
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    started = time.time()
    with open(out, "wb") as f:
        writer = Writer(f)
        writer.start()
        channels: dict = {}
        while time.time() < started + seconds and not stopping:
            for _t, reader in readers:
                topic = reader.topic
                for ev in reader.take():
                    if isinstance(ev, sd.Liveliness):
                        if not ev.alive:
                            no_writer += 1
                            if topic in latched and ev.key:
                                writer_left.append({"topic": topic, "key": ev.key})
                        continue
                    if isinstance(ev, sd.Dispose):
                        if topic not in latched:
                            volatile_ends += 1
                        elif not ev.key:
                            unnamed += 1
                        else:
                            disposes.append({
                                "seq": str(clock()), "topic": topic,
                                "key": ev.key, "reason": ev.reason or (
                                    "observed on the bus as a dispose; the "
                                    "reason, if any, is on the RETIRED "
                                    "sample before it")})
                        continue
                    if not isinstance(ev, sd.Sample):
                        continue
                    sample = ev.data
                    fqn = sd.dds.fqn_of(sample)
                    if fqn not in schemas:
                        name, data = schema_record(fqn)
                        schemas[fqn] = (writer.register_schema(
                            name=name, encoding="omgidl", data=data), data)
                    if topic not in channels:
                        channels[topic] = writer.register_channel(
                            topic=topic, message_encoding="cdr",
                            schema_id=schemas[fqn][0])
                    writer.add_message(
                        channel_id=channels[topic], log_time=clock(),
                        publish_time=(int(sample.stamp.sec) * 1_000_000_000
                                      + sample.stamp.nanosec),
                        data=sample.serialize())
                    counts[topic] += 1
            time.sleep(0.02)
        for d in disposes:
            writer.add_metadata(name=mcapmeta.DISPOSE_RECORD, data=d)
        writer.finish()
    participant.close()
    return {
        "recording": out.name,
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "bytes": out.stat().st_size,
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "capture_seconds": round(time.time() - started, 2),
        "scene": SCENE,
        "dds_domain": domain,
        "types": "spatial.owm/0.1 (provisional), the OWM 0.2 prototype's "
                 "oarc.relationship (demo-owned extension), and "
                 "spatial.core.FrameTransform",
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
        "dispose_record_label": mcapmeta.DISPOSE_RECORD,
        "unnameable_disposes": unnamed,
        "no_writer_instance_ends": no_writer,
        "latched_writer_left": writer_left,
        "volatile_instance_ends": volatile_ends,
        "driven_by": driver,
        "recorded_by": "scenarios/crossing/record.py",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seconds", type=float, required=True,
                    help="the longest to record; SIGTERM ends it sooner")
    ap.add_argument("--domain", type=int, default=None)
    ap.add_argument("--driver", default="scenarios/crossing/record.py")
    args = ap.parse_args()
    for t, topic in LANES.items():
        print(f"record: {topic}  <- {t} ({PROFILES[t].name})", flush=True)
    prov = record(args.out, args.seconds, args.domain, args.driver)
    args.out.with_suffix(".provenance.json").write_text(json.dumps(prov, indent=2) + "\n")
    total = sum(prov["samples"].values())
    for topic, n in prov["samples"].items():
        print(f"record: {n:5d} samples on {topic}")
    print(f"record: {total} samples, {prov['dispose_records']} dispose "
          f"record(s), {prov['no_writer_instance_ends']} writer departure(s), "
          f"{prov['bytes']} bytes, sha256 {prov['sha256'][:16]}")
    if total == 0:
        print("record: nothing arrived; an empty recording is not evidence.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
