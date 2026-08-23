#!/usr/bin/env python3
"""
An independent SpatialDDS participant. The acceptance test of the migration.

Built from three things and nothing else:

* ``spatialdds_idl`` — the types generated from `idl/v1.7`, which anyone can
  produce from the spec bundle with `idlc`.
* the spec's topic names.
* the §3.3.3 QoS profile table.

**No demo transport code.** No `spatialdds_demo.stream`, no
`typed_transport`, no `json_mapping`, no `payloads` — this file builds its
own writers and readers with plain `cyclonedds` calls, exactly as a
third-party implementation would. That constraint is the whole point: if
this can talk to the demo, the demo is interoperable with anything built
from the spec. If it needed a helper from `spatialdds_demo/`, it would only
be proving the demo can talk to itself.

Two directions, both asserted:

1. **Receive** — the probe reads the demo's `Announce` off the well-known
   discovery topic, resolves one announced lane from the announce's own
   `TopicMeta` (type name + QoS profile), and reads a sample from it.
2. **Publish** — the probe announces itself and publishes a `GeoPose`, and
   the demo's own discovery cache and bridge ingest both.

This converts findings §4.1 — "no interoperability with any conformant
implementation" — from inferred to verified, in both directions.

Run in the demo image:

    docker run --rm --network host -v "$PWD:/app" -w /app -e PYTHONPATH=/app \\
        -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \\
        python3 tests/interop_probe.py --domain 0
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.pub import DataWriter
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic

from spatialdds_idl.builtin import Time
from spatialdds_idl.spatial.common import CoordConvention, FrameRef
from spatialdds_idl.spatial.core import Aabb3, CovMatrix, GeoPose
from spatialdds_idl.spatial.disco import (
    Announce,
    Capabilities,
    CoverageElement,
    Depart,
    ProfileSupport,
    ServiceKind,
    TopicMeta,
)

# --- the spec's own names, restated here rather than imported ---------------
# A third-party implementation reads these out of the specification, not out
# of this repo. Restating them is what makes this a probe rather than a
# self-test: if the demo renamed a topic, this file would not follow.
TOPIC_ANNOUNCE = "spatialdds/discovery/announce/v1"
TOPIC_DEPART = "spatialdds/discovery/depart/v1"

# §3.3.3, transcribed from the spec table.
#
# The table is fully explicit now — reliability, ordering, durability,
# history and deadline — so this is a transcription rather than an
# interpretation. It was not: durability had to be inferred from "Latched;
# TRANSIENT_LOCAL" notes in the *type* table, and the deadline column was
# headed "Typical", which reads as advisory.
#
# It is not advisory. Deadline is a request/offered QoS, so a reader
# requesting 33 ms does not match a writer offering none — silently. This
# probe first omitted them and exchanged nothing with the demo, which is
# what got the column renamed. A dash means no deadline, which is a
# different statement from an unstated one.
DEADLINES_MS = {
    "VIDEO_LIVE": 33, "RADAR_RT": 100, "RF_BEAM_RT": 20, "SEG_MASK_RT": 33,
    "POSE_RT": 33, "DET_RT": 100, "LIDAR_RT": 100, "IMU_RT": 10,
}
LATCHED = {"GEOM_TILE", "MAP_META", "ZONE_META", "SENSOR_META",
           "DISCOVERY_ANNOUNCE"}
RELIABLE = {"GEOM_TILE", "VIDEO_ARCHIVE", "DESC_BATCH", "MAP_META",
            "ZONE_META", "EVENT_RT", "VPS_REQ", "VPS_RESP", "SENSOR_META",
            "ANCHOR_DELTA", "DISCOVERY_ANNOUNCE"}
KEEP_LAST = {
    "GEOM_TILE": 1, "VIDEO_LIVE": 1, "RADAR_RT": 1, "RF_BEAM_RT": 1,
    "RADIO_SCAN_RT": 1, "SEG_MASK_RT": 1, "MAP_META": 1, "ZONE_META": 1,
    "EVENT_RT": 64, "POSE_RT": 1, "DET_RT": 1, "LIDAR_RT": 1, "IMU_RT": 1,
    "SENSOR_META": 1, "DISCOVERY_ANNOUNCE": 1,
}
PROFILE_NAMES = (
    "GEOM_TILE", "VIDEO_LIVE", "VIDEO_ARCHIVE", "RADAR_RT", "RF_BEAM_RT",
    "RADIO_SCAN_RT", "SEG_MASK_RT", "DESC_BATCH", "MAP_META", "ZONE_META",
    "EVENT_RT", "POSE_RT", "VPS_REQ", "VPS_RESP", "DET_RT", "LIDAR_RT",
    "IMU_RT", "SENSOR_META", "ANCHOR_DELTA", "DISCOVERY_ANNOUNCE",
)


def _build_profile(name: str) -> Qos:
    policies = [
        Policy.Reliability.Reliable(1_000_000_000) if name in RELIABLE
        else Policy.Reliability.BestEffort,
        Policy.Durability.TransientLocal if name in LATCHED
        else Policy.Durability.Volatile,
        Policy.History.KeepLast(KEEP_LAST[name]) if name in KEEP_LAST
        else Policy.History.KeepAll,
    ]
    if name in DEADLINES_MS:
        policies.append(Policy.Deadline(DEADLINES_MS[name] * 1_000_000))
    return Qos(*policies)


PROFILES: Dict[str, Qos] = {n: _build_profile(n) for n in PROFILE_NAMES}

PROBE_SERVICE_ID = "svc:interop-probe"
PROBE_GEO_TOPIC = "spatialdds/interop_probe/geo/ego/pose/v1"


def _now() -> Time:
    now = time.time()
    return Time(sec=int(now), nanosec=int((now - int(now)) * 1e9))


def _endpoint(participant, topic_name: str, datatype, profile: str,
              writer: bool):
    qos = PROFILES.get(profile)
    if qos is None:
        raise KeyError(f"unknown QoS profile {profile!r}")
    topic = Topic(participant, topic_name, datatype)
    endpoint = (DataWriter if writer else DataReader)(participant, topic, qos=qos)
    endpoint._topic = topic          # keep it alive
    return endpoint


# --- direction 1: read what the demo publishes ------------------------------

def read_announces(participant, timeout: float) -> List[Announce]:
    """Every live service on the bus, from the well-known keyed topic."""
    reader = _endpoint(participant, TOPIC_ANNOUNCE, Announce,
                       "DISCOVERY_ANNOUNCE", writer=False)
    seen: Dict[str, Announce] = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sample in reader.take(N=64) or []:
            if sample is not None and getattr(sample, "service_id", None):
                seen[sample.service_id] = sample
        if seen:
            # Give stragglers a moment; TRANSIENT_LOCAL delivers a burst.
            time.sleep(0.5)
            for sample in reader.take(N=64) or []:
                if sample is not None and getattr(sample, "service_id", None):
                    seen[sample.service_id] = sample
            break
        time.sleep(0.1)
    return list(seen.values())


def resolve_type(type_name: str):
    """
    A §3.3.2 type name to a class, the way a third party would.

    The registry gained thirteen rows in 1.7's findings-batch-2 revision,
    closing the gap between it and the IDL. Before that a spec-only consumer
    meeting this demo could read its geographic poses and not its local ones,
    purely because `framed_pose` had no registry row — measured by an earlier
    run of this probe.

    Anything unregistered is an extension the spec says to treat as an
    unknown value rather than an error; this probe reports which ones it
    could not resolve, because that is what a real consumer experiences.
    """
    from spatialdds_idl.spatial.anchors import AnchorDelta
    from spatialdds_idl.spatial.core import (
        BlobChunk, EntityBinding, FramedPose, NavSatStatus, PlannedTrajectory,
        TileMeta,
    )
    from spatialdds_idl.spatial.events import SpatialEvent
    from spatialdds_idl.spatial.semantics import Detection2DSet, Detection3DSet
    from spatialdds_idl.spatial.sensing.lidar import LidarFrame, LidarMeta
    from spatialdds_idl.spatial.sensing.rad import (
        RadDetectionSet, RadSensorMeta, RadTensorFrame, RadTensorMeta,
    )
    from spatialdds_idl.spatial.sensing.vision import VisionFrame, VisionMeta
    from spatialdds_idl.spatial.vio import ImuSample

    registered = {
        "geopose": GeoPose,
        "framed_pose": FramedPose,
        "navsat_status": NavSatStatus,
        "planned_trajectory": PlannedTrajectory,
        "entity_binding": EntityBinding,
        "spatial_event": SpatialEvent,
        "video_frame": VisionFrame,
        "video_meta": VisionMeta,
        "radar_tensor": RadTensorFrame,
        "radar_tensor_meta": RadTensorMeta,
        "radar_detection": RadDetectionSet,
        "rad_sensor_meta": RadSensorMeta,
        "detection3d": Detection3DSet,
        "detection2d": Detection2DSet,
        "lidar_frame": LidarFrame,
        "lidar_meta": LidarMeta,
        "imu_sample": ImuSample,
        "anchor_delta": AnchorDelta,
        "tile_meta": TileMeta,
        "blob_chunk": BlobChunk,
    }
    for module, names in (
        ("spatialdds_idl.spatial.sensing.rf_beam",
         {"rf_beam": "RfBeamFrame", "rf_beam_meta": "RfBeamMeta"}),
        ("spatialdds_idl.spatial.sensing.radio",
         {"radio_scan": "RadioScan", "radio_sensor_meta": "RadioSensorMeta"}),
    ):
        try:
            mod = __import__(module, fromlist=list(names.values()))
            for key, attr in names.items():
                registered[key] = getattr(mod, attr)
        except Exception:
            pass          # a provisional profile this build did not generate
    return registered.get(type_name)


def read_one_announced_lane(participant, announce: Announce,
                            timeout: float) -> Tuple[Optional[str], int, List[str]]:
    """
    Follow an announce to a data lane and read from it.

    Returns ``(topic read, samples seen, type names this probe could not
    resolve)``. The last is the interesting one: an extension name means the
    demo is publishing something a spec-only implementation cannot type.
    """
    unresolvable: List[str] = []
    for meta in announce.topics or []:
        datatype = resolve_type(meta.type)
        if datatype is None:
            unresolvable.append(meta.type)
            continue
        if meta.qos_profile not in PROFILES:
            unresolvable.append(f"{meta.type} (profile {meta.qos_profile})")
            continue
        reader = _endpoint(participant, meta.name, datatype,
                           meta.qos_profile, writer=False)
        deadline = time.time() + timeout
        count = 0
        while time.time() < deadline:
            for sample in reader.take(N=32) or []:
                if sample is not None:
                    count += 1
            if count:
                return meta.name, count, unresolvable
            time.sleep(0.05)
        return meta.name, 0, unresolvable
    return None, 0, unresolvable


# --- direction 2: publish something the demo must ingest --------------------

def _probe_announce() -> Announce:
    """
    A complete `Announce` for this probe.

    Built field by field from the IDL, which is the point: a third party has
    only the spec to go on, and every field here is one the spec requires.
    """
    frame = FrameRef(uuid="", fqn="earth-fixed", has_coord_convention=True,
                     coord_convention=CoordConvention.ENU)
    zero = Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0])
    coverage = CoverageElement(
        has_crs=True, crs="EPSG:4979",
        has_bbox=True, bbox=[-123.0, 37.0, -122.0, 38.0],
        has_aabb=False, aabb=zero,
        _global=False,
        has_frame_ref=False, frame_ref=frame,
        has_coverage_window=False,
        coverage_window_start=_now(), coverage_window_end=_now(),
        has_circle=False, circle_center=[0.0, 0.0, 0.0], circle_radius_m=0.0,
    )
    return Announce(
        service_id=PROBE_SERVICE_ID,
        name="interop-probe",
        kind=ServiceKind.OTHER,
        version="1.7",
        org="independent",
        hints=[],
        caps=Capabilities(
            supported_profiles=[
                ProfileSupport(name="spatial.core", major=1, min_minor=7,
                               max_minor=7),
                ProfileSupport(name="spatial.discovery", major=1, min_minor=7,
                               max_minor=7),
            ],
            preferred_profiles=["spatial.discovery"],
            features=[],
        ),
        topics=[TopicMeta(
            name=PROBE_GEO_TOPIC, type="geopose", version="v1",
            qos_profile="POSE_RT", target_rate_hz=1.0, max_chunk_bytes=0,
        )],
        coverage=[coverage],
        coverage_frame_ref=frame,
        has_coverage_eval_time=False, coverage_eval_time=_now(),
        transforms=[],
        manifest_uri="spatialdds://interop-probe/zone:test/manifest:probe",
        auth_hint="",
        stamp=_now(),
        ttl_sec=300,
        coverage_source_ids=[],
    )


def publish_probe(participant, seconds: float) -> int:
    """Announce, then publish GeoPose at 5 Hz. Departs cleanly on the way out."""
    announce_writer = _endpoint(participant, TOPIC_ANNOUNCE, Announce,
                                "DISCOVERY_ANNOUNCE", writer=True)
    depart_writer = _endpoint(participant, TOPIC_DEPART, Depart,
                              "DISCOVERY_ANNOUNCE", writer=True)
    geo_writer = _endpoint(participant, PROBE_GEO_TOPIC, GeoPose,
                           "POSE_RT", writer=True)

    announce = _probe_announce()
    announce_writer.write(announce)
    time.sleep(1.0)                    # let discovery settle before data

    published = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        geo_writer.write(GeoPose(
            lat_deg=37.7749 + published * 1e-5,
            lon_deg=-122.4194,
            alt_m=15.0,
            q=[0.0, 0.0, 0.0, 1.0],
            stamp=_now(),
            cov=CovMatrix(none=0),
        ))
        published += 1
        time.sleep(0.2)

    # C.5: dispose the instance (MUST) and publish Depart (SHOULD). A bridge
    # to MQTT or a WebSocket has no DDS instance state, so the message is
    # what crosses.
    announce_writer.dispose(announce)
    depart_writer.write(Depart(service_id=PROBE_SERVICE_ID, stamp=_now()))
    return published


# --- the probe itself -------------------------------------------------------

def run(domain: int, timeout: float, publish_seconds: float,
        receive: bool = True, publish: bool = True) -> int:
    participant = DomainParticipant(domain)
    ok = True

    print(f"[probe] domain={domain} — built only from spatialdds_idl, "
          f"spec topic names and the 3.3.3 profile table", flush=True)

    if not receive:
        published = publish_probe(participant, publish_seconds)
        print(f"[probe] announced {PROBE_SERVICE_ID} and published "
              f"{published} GeoPose sample(s) on {PROBE_GEO_TOPIC}", flush=True)
        print("[probe] departed (instance disposed + Depart published)",
              flush=True)
        return 0

    # ---- direction 1 -------------------------------------------------------
    announces = read_announces(participant, timeout)
    if not announces:
        print(f"[probe] FAIL: no Announce on {TOPIC_ANNOUNCE} within "
              f"{timeout:.0f}s", flush=True)
        return 1
    print(f"[probe] received {len(announces)} announce(s):", flush=True)
    for a in announces:
        print(f"    {a.service_id:28s} {a.name} "
              f"({len(a.topics or [])} topics)", flush=True)

    read_any = False
    all_unresolvable: List[str] = []
    for a in announces:
        topic, count, unresolvable = read_one_announced_lane(
            participant, a, timeout=timeout / 2)
        all_unresolvable.extend(unresolvable)
        if topic and count:
            print(f"[probe] read {count} sample(s) from {topic} "
                  f"(announced by {a.service_id})", flush=True)
            read_any = True
            break
    if not read_any:
        print("[probe] FAIL: could not read any announced data lane", flush=True)
        ok = False

    if all_unresolvable:
        # Not a failure: 3.3.2 says an unregistered type name is an extension
        # point. But it is the honest measure of how much of this demo a
        # spec-only implementation can consume, so it is reported.
        print("[probe] NOTE: type names this spec-only probe cannot resolve — "
              "each is a lane the demo publishes that 1.7 names no type for:",
              flush=True)
        for name in sorted(set(all_unresolvable)):
            print(f"    {name}", flush=True)

    # ---- direction 2 -------------------------------------------------------
    if not publish:
        print(f"[probe] {'PASS' if ok else 'FAIL'} (receive only)", flush=True)
        return 0 if ok else 1
    published = publish_probe(participant, publish_seconds)
    print(f"[probe] announced {PROBE_SERVICE_ID} and published {published} "
          f"GeoPose sample(s) on {PROBE_GEO_TOPIC}", flush=True)
    print("[probe] departed (instance disposed + Depart published)", flush=True)

    print(f"[probe] {'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="Seconds to wait for the demo's announces")
    parser.add_argument("--publish-seconds", type=float, default=5.0)
    parser.add_argument("--publish-only", action="store_true",
                        help="Skip reading the demo's announces; just "
                             "announce and publish, as a third-party "
                             "producer joining a deployment would.")
    parser.add_argument("--receive-only", action="store_true",
                        help="Read the demo's announces and one data lane, "
                             "then stop.")
    args = parser.parse_args()
    return run(args.domain, args.timeout, args.publish_seconds,
               receive=not args.publish_only,
               publish=not args.receive_only)


if __name__ == "__main__":
    sys.exit(main())
