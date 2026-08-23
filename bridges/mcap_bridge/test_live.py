#!/usr/bin/env python3
"""End-to-end live test: publish → record → replay → subscribe round-trip.

Drives real typed publishers over CycloneDDS plus the real recorder and
replayer. Run inside the cyclonedds-python image so DDS is wired up. Exits 0
on success, 1 on mismatch.

The samples are built with the publishers' own helpers, so what goes round
the loop is what the demo actually emits — and because the replayer rebuilds
each sample from the recording rather than relaying bytes, a recording that
does not deserialise fails the test instead of reaching the bus.

Usage (inside the cyclonedds-python container):
    python3 -m pip install -r bridges/requirements.txt
    SPATIALDDS_DDS_DOMAIN=42 python3 bridges/mcap_bridge/test_live.py
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Tuple

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT))

sys.path.insert(0, str(_REPO_ROOT / "multi_operator_fusion"))

import recorder as recorder_mod  # noqa: E402
import replayer as replayer_mod  # noqa: E402

SCENE = "scene/intersection"
DET_TYPE = "detection3d"
POSE_TYPE = "framed_pose"
TRACK_TYPE = "oarc.fused_track"


def _samples() -> List[Tuple[str, str, dict]]:
    """A mix over three service namespaces and three types, built for real."""
    from spatialdds_types import (
        make_detection, make_detection_set,
        make_framed_pose, make_fused_track_set,
    )
    from fusion import FusedTrack, Position, Velocity

    def det_set(operator, det_id, seq):
        det = make_detection(
            det_id=det_id, class_id="vehicle.car", score=0.9,
            center=(float(seq), 2.0, 0.0), size=(4.5, 1.8, 1.6),
            q=(0.0, 0.0, 0.0, 1.0), frame_ref_fqn=SCENE,
            timestamp_s=1700000000.0 + seq, source_id=operator)
        return make_detection_set(
            set_id=f"{operator}-{seq}", source_operator=operator,
            frame_ref_fqn=SCENE,
            dets=[det],
            frame_seq=seq, timestamp_s=1700000000.0 + seq)

    def pose(operator, seq):
        return make_framed_pose(
            float(seq), 0.0, 0.0, q=(0.0, 0.0, 0.0, 1.0),
            frame_ref_fqn=f"{operator}/map", timestamp_s=1700000000.0 + seq)

    track = FusedTrack(
        track_id="t1", position=Position(0.0, 0.0, 0.0),
        velocity=Velocity(0.0, 0.0, 0.0), position_uncertainty=0.3,
        object_class="vehicle.car", confidence=0.9,
        source_operators=["operator_x", "operator_y"],
        source_modalities=["det3d"], source_count=2,
        timestamp=1700000001.0, track_age=1.0)

    return [
        (POSE_TYPE, "spatialdds/operator_x/ego/pose/v1", pose("operator_x", 1)),
        (DET_TYPE, "spatialdds/operator_x/sensing/detection3d/v1",
         det_set("operator_x", "d1", 1)),
        (DET_TYPE, "spatialdds/operator_y/sensing/detection3d/v1",
         det_set("operator_y", "d2", 1)),
        (TRACK_TYPE, "spatialdds/platform/fusion/track/v1",
         make_fused_track_set([track], timestamp_s=1700000001.0)),
        (POSE_TYPE, "spatialdds/operator_x/ego/pose/v1", pose("operator_x", 2)),
        (DET_TYPE, "spatialdds/operator_x/sensing/detection3d/v1",
         det_set("operator_x", "d3", 2)),
    ]


SAMPLES: List[Tuple[str, str, dict]] = []      # filled in main()


def _fingerprint(type_name: str, topic: str, payload) -> Tuple[str, str, str]:
    """Stable identity for one sample, comparable after a JSON round trip."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return (type_name, topic, payload)
    return (type_name, topic, json.dumps(payload, sort_keys=True))


# Half a float32 ulp near 1.0. Several spec fields are float32 — Detection3D
# .score among them — so a value written as 0.9 comes back as
# 0.8999999761581421. Exact JSON equality would call that a lost sample; it
# is the type doing exactly what it says it does.
FLOAT_TOL = 1e-6


def _same(sent, got) -> bool:
    """Structural equality, tolerant of float32 rounding."""
    if isinstance(sent, dict) and isinstance(got, dict):
        return (set(sent) == set(got)
                and all(_same(sent[k], got[k]) for k in sent))
    if isinstance(sent, list) and isinstance(got, list):
        return (len(sent) == len(got)
                and all(_same(a, b) for a, b in zip(sent, got)))
    if isinstance(sent, bool) or isinstance(got, bool):
        return sent is got
    if isinstance(sent, (int, float)) and isinstance(got, (int, float)):
        return abs(float(sent) - float(got)) <= FLOAT_TOL * max(
            1.0, abs(float(sent)))
    return sent == got


def _diff(sent, got, path: str = "") -> List[str]:
    """Field-level differences between what was sent and what came back."""
    out: List[str] = []
    if isinstance(sent, dict) and isinstance(got, dict):
        for key in sorted(set(sent) | set(got)):
            out += _diff(sent.get(key, "<absent>"), got.get(key, "<absent>"),
                         f"{path}.{key}" if path else key)
    elif isinstance(sent, list) and isinstance(got, list):
        if len(sent) != len(got):
            out.append(f"{path}: length {len(sent)} -> {len(got)}")
        for i, (a, b) in enumerate(zip(sent, got)):
            out += _diff(a, b, f"{path}[{i}]")
    elif not _same(sent, got):
        out.append(f"{path}: {sent!r} -> {got!r}")
    return out


def _announce_lanes(domain: int, samples):
    """
    Announce every lane, so the discovery-driven recorder opens readers.

    Returns the publisher; the caller keeps it alive, since closing it
    disposes each instance and tells consumers the services left.
    """
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo.stream import StreamPublisher
    from spatialdds_types import circle_coverage, make_announce, topic_meta

    profiles = {DET_TYPE: "RADAR_RT", POSE_TYPE: "POSE_RT",
                TRACK_TYPE: "POSE_RT"}
    by_service = {}
    for type_name, topic, _payload in samples:
        service = topic.split("/")[1]
        by_service.setdefault(service, {})[topic] = type_name

    publisher = StreamPublisher(DomainParticipant(domain))
    for service, lanes in by_service.items():
        publisher.announce(make_announce(
            operator=service, service_kind="SENSING",
            topics=[topic_meta(t, tn, profiles[tn]) for t, tn in lanes.items()],
            coverage=circle_coverage(0.0, 0.0, 100.0),
            timestamp_s=time.time()))
    return publisher


def _publish_thread(domain: int, sender: str, samples: List[Tuple[str, str, dict]],
                    inter_msg_delay: float, recorder_warmup: float,
                    done_evt: threading.Event):
    """Run from a daemon thread: wait for recorder discovery, publish, then SIGINT."""
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo import topic_types, typed_transport as tt

    profiles = {DET_TYPE: "RADAR_RT", POSE_TYPE: "POSE_RT",
                TRACK_TYPE: "POSE_RT"}
    participant = DomainParticipant(domain)
    writers = {
        topic: tt.TypedDictWriter(participant, topic,
                                  topic_types.resolve(type_name),
                                  profiles[type_name])
        for type_name, topic, _ in samples
    }
    try:
        # Give the recorder's readers a moment to come up via DDS discovery.
        time.sleep(recorder_warmup)
        for _type_name, topic, payload in samples:
            writers[topic].write(payload)
            time.sleep(inter_msg_delay)
        # Let the recorder drain the last samples.
        time.sleep(1.0)
    finally:
        done_evt.set()
        # Signal the main thread (recorder) to exit.
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except Exception:
            pass


def _phase1_record(domain: int, mcap_path: Path) -> Dict[str, int]:
    """Publisher thread + recorder in main thread → mcap_path."""
    done_evt = threading.Event()
    pub = threading.Thread(
        target=_publish_thread,
        kwargs=dict(domain=domain, sender="live-publisher",
                    samples=SAMPLES, inter_msg_delay=0.15,
                    recorder_warmup=2.0, done_evt=done_evt),
        daemon=True,
    )
    pub.start()
    counts = recorder_mod.record(
        str(mcap_path),
        domain_id=domain,
        duration_sec=15.0,  # safety: way longer than the publisher loop
    )
    pub.join(timeout=5.0)
    return counts


def _phase2_replay(domain: int, mcap_path: Path):
    """Subscriber + replayer (main thread) → verify what the subscriber receives."""
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo.stream import StreamSubscriber

    received: List[Tuple[str, str, str]] = []
    rx_lock = threading.Lock()
    stop = threading.Event()

    def on_sample(type_name, topic, payload, _stamp_ns):
        with rx_lock:
            received.append(_fingerprint(type_name, topic, payload))

    sub = StreamSubscriber(DomainParticipant(domain), on_sample)

    def _pump():
        while not stop.is_set():
            sub.poll()
            stop.wait(0.02)

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()
    try:
        # Allow DDS discovery to wire the subscriber up before the replayer
        # publishes — including the announces that tell it which lanes exist.
        time.sleep(3.0)
        # Real-time speed keeps the publisher's 0.15s spacing, comfortably
        # above the subscriber's 20ms poll. Squashing it races the
        # best-effort lanes' reader cache and drops samples — match
        # production timing instead.
        n = replayer_mod.replay(
            str(mcap_path),
            domain_id=domain,
            speed=1.0,
            loop=False,
            sender_id="live-replayer",
        )
        # Let the subscriber drain.
        time.sleep(3.0)
    finally:
        stop.set()
        pump.join(timeout=2)
    return n, received


def _prewarm_idl(domain: int) -> None:
    """CycloneDDS Python lazily fills the IDL type-object cache the first time
    a Topic is created. If two threads race that init concurrently, the
    second observer sees `version_support is None`. Pre-warm in the main
    thread before any worker threads build their own endpoints."""
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo import topic_types, typed_transport as tt

    participant = DomainParticipant(domain)
    for i, type_name in enumerate((DET_TYPE, POSE_TYPE, TRACK_TYPE)):
        tt.make_writer(participant, f"spatialdds/prewarm/{i}/v1",
                       topic_types.resolve(type_name), "EVENT_RT")
    time.sleep(0.3)


def main() -> int:
    domain = int(os.getenv("SPATIALDDS_DDS_DOMAIN", "42"))
    out_dir = Path(os.getenv("MCAP_TEST_DIR", "/tmp/spatialdds_mcap_live"))
    out_dir.mkdir(parents=True, exist_ok=True)
    mcap_path = out_dir / "live.mcap"
    if mcap_path.exists():
        mcap_path.unlink()

    _prewarm_idl(domain)
    SAMPLES.extend(_samples())
    lanes = _announce_lanes(domain, SAMPLES)      # keep alive for the run

    expected = [_fingerprint(t, topic, payload)
                for (t, topic, payload) in SAMPLES]
    print(f"[live] domain={domain} expected={len(expected)} mcap={mcap_path}", flush=True)

    # Phase 1: publish + record
    print("[live] phase 1: publish → record", flush=True)
    counts = _phase1_record(domain, mcap_path)
    if not mcap_path.exists() or mcap_path.stat().st_size == 0:
        print("[live] FAIL: recorder produced no MCAP file", flush=True)
        return 1
    print(f"[live] recorded {sum(counts.values())} samples across {len(counts)} topics, "
          f"file size {mcap_path.stat().st_size} bytes", flush=True)
    for topic in sorted(counts):
        print(f"  {counts[topic]:>6}  {topic}", flush=True)
    if sum(counts.values()) < len(expected):
        print(f"[live] FAIL: recorded {sum(counts.values())} < expected {len(expected)}",
              flush=True)
        return 1

    # Phase 2: replay + subscribe
    print("[live] phase 2: replay → subscribe", flush=True)
    n_published, received = _phase2_replay(domain, mcap_path)
    print(f"[live] replayer published {n_published}, subscriber received {len(received)}",
          flush=True)

    # Match each expected sample against an unclaimed received one, so a
    # duplicate on the wire cannot stand in for a missing sample.
    unclaimed = list(received)
    missing = []
    for fp in expected:
        sent = json.loads(fp[2])
        hit = next((r for r in unclaimed if r[0] == fp[0] and r[1] == fp[1]
                    and _same(sent, json.loads(r[2]))), None)
        if hit is None:
            missing.append(fp)
        else:
            unclaimed.remove(hit)
    if missing:
        print("[live] FAIL: samples did not survive the round trip:", flush=True)
        for fp in missing:
            print(f"  - type={fp[0]} topic={fp[1]}", flush=True)
            # Say *what* differs, not just that something did. A sample can
            # go missing because it was dropped or because it came back
            # changed, and those are different bugs.
            near = [r for r in received if r[0] == fp[0] and r[1] == fp[1]]
            if not near:
                print("      never arrived", flush=True)
                continue
            for diff in _diff(json.loads(fp[2]), json.loads(near[0][2])):
                print(f"      {diff}", flush=True)
        return 1

    print(f"[live] PASS — all {len(expected)} samples survived "
          f"publish→record→replay→subscribe", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
