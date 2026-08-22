#!/usr/bin/env python3
"""End-to-end live test: publish → record → replay → subscribe round-trip.

Drives the real `EnvelopeTransport` (CycloneDDS-backed) plus the real
recorder/replayer. Run inside the cyclonedds-python image so DDS is wired
up. Exits 0 on success, 1 on mismatch.

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

from nuscenes.dds_envelope_transport import EnvelopeTransport  # noqa: E402

import recorder as recorder_mod  # noqa: E402
import replayer as replayer_mod  # noqa: E402


# A synthetic mix that exercises three operator namespaces and four msg_types.
SAMPLES: List[Tuple[str, str, dict]] = [
    ("ANNOUNCE",            "spatialdds/operator_x/announce/v1",                  {"service_id": "svc:op_x", "version": "1.7"}),
    ("NUSC_EGO_POSE",       "spatialdds/operator_x/ego/pose/v1",                  {"frame_seq": 1, "stamp": {"sec": 1700000000, "nanosec": 0}}),
    ("NUSC_DET3D_SET",      "spatialdds/operator_x/sensing/detection3d/v1",       {"frame_seq": 1, "detections": [{"det_id": "d1"}]}),
    ("NUSC_VISION_FRAME",   "spatialdds/operator_x/vision/CAM_FRONT/frame/v1",    {"stream_id": "cam", "schema_version": "1.7"}),
    ("NUSC_DET3D_SET",      "spatialdds/operator_y/sensing/detection3d/v1",       {"frame_seq": 1, "detections": [{"det_id": "d2"}]}),
    ("NUSC_FUSED_TRACK_SET","spatialdds/platform/fusion/track/v1",                {"frame_seq": 1, "tracks": [{"track_id": "t1"}]}),
    ("NUSC_EGO_POSE",       "spatialdds/operator_x/ego/pose/v1",                  {"frame_seq": 2, "stamp": {"sec": 1700000001, "nanosec": 0}}),
    ("NUSC_DET3D_SET",      "spatialdds/operator_x/sensing/detection3d/v1",       {"frame_seq": 2, "detections": []}),
]


def _fingerprint(env_msg_type: str, env_topic: str, payload: str) -> Tuple[str, str, str]:
    """Stable identity for a published envelope, comparable after JSON re-encoding."""
    try:
        normalized = json.dumps(json.loads(payload), sort_keys=True)
    except Exception:
        normalized = payload
    return (env_msg_type, env_topic, normalized)


def _publish_thread(domain: int, sender: str, samples: List[Tuple[str, str, dict]],
                    inter_msg_delay: float, recorder_warmup: float, done_evt: threading.Event):
    """Run from a daemon thread: wait for recorder discovery, publish, then SIGINT."""
    transport = EnvelopeTransport(lambda _e: None, domain, sender)
    transport.start()
    try:
        # Give the recorder's reader a moment to come up via DDS discovery.
        time.sleep(recorder_warmup)
        for msg_type, topic, payload in samples:
            transport.publish(
                logical_topic=topic,
                msg_type=msg_type,
                payload_json=json.dumps(payload),
            )
            time.sleep(inter_msg_delay)
        # Let the recorder drain the last envelopes.
        time.sleep(1.0)
    finally:
        transport.stop()
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
    received: List[Tuple[str, str, str]] = []
    rx_lock = threading.Lock()

    def on_env(env: object) -> None:
        with rx_lock:
            received.append(
                _fingerprint(
                    getattr(env, "msg_type", "") or "",
                    getattr(env, "logical_topic", "") or "",
                    getattr(env, "payload_json", "") or "",
                )
            )

    sub = EnvelopeTransport(on_env, domain, "live-subscriber")
    sub.start()
    try:
        # Allow DDS discovery to wire the subscriber up before the replayer publishes.
        time.sleep(2.0)
        # Real-time speed: keeps the publisher's 0.15s spacing, which is well
        # above the EnvelopeTransport's 10ms poll loop. Squashing this with
        # speed>>1 races the default KEEP_LAST(1) best-effort reader cache
        # and drops samples — match production timing instead.
        n = replayer_mod.replay(
            str(mcap_path),
            domain_id=domain,
            speed=1.0,
            loop=False,
            sender_id="live-replayer",
        )
        # Let the subscriber drain.
        time.sleep(2.0)
    finally:
        sub.stop()
    return n, received


def _prewarm_idl(domain: int) -> None:
    """CycloneDDS Python lazily fills the IDL type-object cache the first time
    a Topic is created. If two threads race that init concurrently, the
    second observer sees `version_support is None`. Pre-warm in the main
    thread before any worker threads spin up their own transports."""
    warm = EnvelopeTransport(lambda _e: None, domain, "live-prewarm")
    warm.start()
    time.sleep(0.2)
    warm.stop()


def main() -> int:
    domain = int(os.getenv("SPATIALDDS_DDS_DOMAIN", "42"))
    out_dir = Path(os.getenv("MCAP_TEST_DIR", "/tmp/spatialdds_mcap_live"))
    out_dir.mkdir(parents=True, exist_ok=True)
    mcap_path = out_dir / "live.mcap"
    if mcap_path.exists():
        mcap_path.unlink()

    _prewarm_idl(domain)

    expected = [
        _fingerprint(t, topic, json.dumps(payload))
        for (t, topic, payload) in SAMPLES
    ]
    print(f"[live] domain={domain} expected={len(expected)} mcap={mcap_path}", flush=True)

    # Phase 1: publish + record
    print("[live] phase 1: publish → record", flush=True)
    counts = _phase1_record(domain, mcap_path)
    if not mcap_path.exists() or mcap_path.stat().st_size == 0:
        print("[live] FAIL: recorder produced no MCAP file", flush=True)
        return 1
    print(f"[live] recorded {sum(counts.values())} envelopes across {len(counts)} topics, "
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

    received_set = set(received)
    missing = [fp for fp in expected if fp not in received_set]
    if missing:
        print("[live] FAIL: missing fingerprints in subscriber output:", flush=True)
        for fp in missing:
            print(f"  - msg_type={fp[0]} topic={fp[1]} payload={fp[2][:80]}", flush=True)
        return 1

    print(f"[live] PASS — all {len(expected)} envelopes survived "
          f"publish→record→replay→subscribe", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
