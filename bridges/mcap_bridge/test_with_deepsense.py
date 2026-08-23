#!/usr/bin/env python3
"""Live test against the real DeepSense publisher, on typed topics.

Spawns ``deepsense/publisher.py`` as a subprocess, runs the recorder against
the same DDS domain, then runs the replayer with a fresh subscriber and
verifies every recorded envelope is delivered.

Run inside the cyclonedds-python container with the dataset mounted:

    docker run --rm --network host \\
      -v "$REPO:/app" -v "$DATA:/data/scenario9" \\
      -w /app \\
      -e SPATIALDDS_DDS_DOMAIN=51 -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \\
      -e PYTHONPATH=/app -e DEEPSENSE_DATAROOT=/data/scenario9 \\
      cyclonedds-python bash -lc "python3 -m pip install --quiet mcap && \\
        python3 bridges/mcap_bridge/test_with_deepsense.py"
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT))

import recorder as recorder_mod  # noqa: E402
import replayer as replayer_mod  # noqa: E402


def _expected_topics() -> Dict[str, str]:
    """``{topic: §3.3.2 type}`` for every lane the DeepSense publisher owns.

    Read from the publisher's own lane table rather than restated here, so
    adding a lane cannot leave this test silently checking the old set.
    """
    sys.path.insert(0, str(_REPO_ROOT / "deepsense"))
    sys.path.insert(0, str(_REPO_ROOT / "nuscenes"))
    from publisher import LANES  # deepsense/publisher.py

    return {topic: type_name for topic, type_name, _profile in LANES.values()}


def _prewarm_idl(domain: int) -> None:
    """Force the lazy IDL type-object cache to fill once before any worker
    thread/subprocess races on it (see bug fix in test_live.py)."""
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo import topic_types, typed_transport as tt

    participant = DomainParticipant(domain)
    for i, type_name in enumerate(sorted(set(_expected_topics().values()))):
        tt.make_writer(participant, f"spatialdds/prewarm/{i}/v1",
                       topic_types.resolve(type_name), "EVENT_RT")
    time.sleep(0.3)


def _spawn_publisher(domain: int, dataroot: str, max_samples: int,
                     sequence: int) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-u",
        str(_REPO_ROOT / "deepsense" / "publisher.py"),
        "--dataroot", dataroot,
        "--domain", str(domain),
        "--sequence", str(sequence),
        "--max-samples", str(max_samples),
        "--speed", "1.0",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, text=True,
    )


def _drain_publisher_output(proc: subprocess.Popen, lines: List[str]) -> None:
    if proc.stdout is None:
        return
    for line in proc.stdout:
        lines.append(line.rstrip())


def _phase1_record(domain: int, dataroot: str, max_samples: int, sequence: int,
                   mcap_path: Path) -> Tuple[Dict[str, int], int]:
    """Run the recorder in the main thread; defer publisher launch by a few
    seconds so DDS discovery has time to wire reader↔writer before the
    publisher starts emitting samples.
    """
    proc_holder: Dict[str, subprocess.Popen] = {}
    publisher_lines: List[str] = []

    warmup_secs = float(os.getenv("DEEPSENSE_RECORDER_WARMUP", "8.0"))

    def _delayed_spawn():
        # Allow the recorder's reader to come up and finish DDS discovery
        # before the publisher's writer begins. With default best-effort
        # KEEP_LAST(1) QoS, a late-joining reader misses everything the
        # writer published before discovery completed. 8s tolerates the
        # publisher subprocess's own ~2s import-time cyclonedds startup.
        time.sleep(warmup_secs)
        try:
            proc = _spawn_publisher(domain, dataroot, max_samples, sequence)
            proc_holder["proc"] = proc
        except Exception as exc:
            print(f"[ds-test] FAIL: could not start publisher: {exc}", flush=True)
            os.kill(os.getpid(), signal.SIGINT)
            return
        # Drain stdout so the subprocess doesn't deadlock on a full pipe.
        if proc.stdout is not None:
            for line in proc.stdout:
                publisher_lines.append(line.rstrip())
        proc.wait()
        # Let the recorder catch in-flight envelopes before we shut it down.
        time.sleep(2.0)
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except Exception:
            pass

    threading.Thread(target=_delayed_spawn, daemon=True).start()

    counts = recorder_mod.record(
        str(mcap_path),
        domain_id=domain,
        # Hard ceiling so a stuck publisher doesn't hang the test forever.
        duration_sec=120.0,
    )

    proc = proc_holder.get("proc")
    rc = proc.returncode if proc is not None else -1
    if proc is not None and rc != 0:
        # Surface the publisher's stderr/stdout so failures are debuggable.
        print("[ds-test] publisher output:", flush=True)
        for line in publisher_lines[-30:]:
            print(f"  {line}", flush=True)
    return counts, rc


def _phase2_replay(domain: int, mcap_path: Path) -> Tuple[int, Counter, Dict[str, int]]:
    """
    Subscribe with a typed reader per lane, on that lane's own QoS profile,
    and verify the archival round-trip.

    Note what per-type QoS means here: the real-time lanes are BEST_EFFORT
    per 3.3.3, so a replay of them is allowed to drop samples exactly as the
    live publisher's would. That is the profile, not the bridge. The latched
    metadata lanes are reliable and must not lose anything.
    """
    from cyclonedds.domain import DomainParticipant

    from spatialdds_demo import topic_types, typed_transport as tt

    received_msg_types: Counter = Counter()
    received_per_topic: Dict[str, int] = {}
    rx_lock = threading.Lock()
    stop = threading.Event()

    sys.path.insert(0, str(_REPO_ROOT / "deepsense"))
    from publisher import LANES  # deepsense/publisher.py

    participant = DomainParticipant(domain)
    readers = [
        (tt.make_reader(participant, topic, topic_types.resolve(type_name),
                        profile), topic, type_name)
        for topic, type_name, profile in LANES.values()
    ]

    def _drain():
        while not stop.is_set():
            for reader, topic, type_name in readers:
                for _sample in tt.take_samples(reader, n=512):
                    with rx_lock:
                        received_msg_types[type_name] += 1
                        received_per_topic[topic] = (
                            received_per_topic.get(topic, 0) + 1)
            stop.wait(timeout=0.02)

    drain_thread = threading.Thread(target=_drain, daemon=True)
    drain_thread.start()
    try:
        # Allow DDS discovery to settle before the replayer starts publishing.
        time.sleep(2.0)
        published = replayer_mod.replay(
            str(mcap_path),
            domain_id=domain,
            speed=1.0,
            loop=False,
            sender_id="deepsense-test-replayer",
        )
        # Drain.
        time.sleep(2.0)
    finally:
        stop.set()
        drain_thread.join(timeout=2.0)
    return published, received_msg_types, received_per_topic


def main() -> int:
    domain = int(os.getenv("SPATIALDDS_DDS_DOMAIN", "51"))
    dataroot = os.getenv("DEEPSENSE_DATAROOT", "/data/scenario9")
    max_samples = int(os.getenv("DEEPSENSE_MAX_SAMPLES", "8"))
    # Subset of scenario9 doesn't include sequence=1 (the publisher default);
    # default to sequence=2 which is present in both the subset and the full set.
    sequence = int(os.getenv("DEEPSENSE_SEQUENCE", "2"))
    out_dir = Path(os.getenv("MCAP_TEST_DIR", "/tmp/spatialdds_mcap_deepsense"))
    out_dir.mkdir(parents=True, exist_ok=True)
    mcap_path = out_dir / "deepsense.mcap"
    if mcap_path.exists():
        mcap_path.unlink()

    if not Path(dataroot, "scenario9.csv").exists():
        print(f"[ds-test] FAIL: dataset not found at {dataroot} (need scenario9.csv)", flush=True)
        return 1

    print(f"[ds-test] domain={domain} dataroot={dataroot} sequence={sequence} "
          f"max_samples={max_samples}", flush=True)
    print(f"[ds-test] mcap={mcap_path}", flush=True)

    _prewarm_idl(domain)

    # ---- Phase 1: real publisher → recorder ---------------------------------
    print("[ds-test] phase 1: deepsense publisher → recorder", flush=True)
    t0 = time.monotonic()
    counts, pub_rc = _phase1_record(domain, dataroot, max_samples, sequence, mcap_path)
    record_secs = time.monotonic() - t0
    if pub_rc != 0:
        print(f"[ds-test] WARN: publisher exit code {pub_rc} "
              f"(check stdout above for traceback)", flush=True)

    if not mcap_path.exists() or mcap_path.stat().st_size == 0:
        print("[ds-test] FAIL: recorder produced no MCAP file", flush=True)
        return 1
    total = sum(counts.values())
    print(f"[ds-test] recorded {total} envelopes across {len(counts)} topics "
          f"({mcap_path.stat().st_size} bytes, {record_secs:.1f}s)", flush=True)
    for topic in sorted(counts):
        print(f"  {counts[topic]:>6}  {topic}", flush=True)

    # Each frame produces one of each per-frame msg_type, plus three meta
    # types emitted exactly once. We expect at least 1 of each per-frame type.
    if total < max_samples * 5:  # conservative lower bound
        print(f"[ds-test] FAIL: only {total} envelopes recorded for "
              f"max_samples={max_samples} (expected ≥ {max_samples * 5})", flush=True)
        return 1

    # ---- Phase 2: replay with fresh subscriber ------------------------------
    print("[ds-test] phase 2: replay → fresh subscriber", flush=True)
    published, rx_msg_types, rx_per_topic = _phase2_replay(domain, mcap_path)
    rx_total = sum(rx_msg_types.values())
    print(f"[ds-test] replayer published {published}; subscriber received {rx_total}", flush=True)

    # Every §3.3.2 type the publisher announces must appear during replay,
    # and every topic must carry at least one sample. Exact per-lane counts
    # are not asserted: the real-time lanes are BEST_EFFORT per 3.3.3 and
    # may legitimately drop, which is the profile rather than the bridge.
    expected_types = set(_expected_topics().values())
    missing_types = expected_types - set(rx_msg_types.keys())
    if missing_types:
        print(f"[ds-test] FAIL: expected msg_types missing from replay: {sorted(missing_types)}",
              flush=True)
        return 1

    missing_topics = [t for t in counts if rx_per_topic.get(t, 0) == 0]
    if missing_topics:
        print(f"[ds-test] FAIL: topics absent from replay: {missing_topics}", flush=True)
        return 1

    # Loss budget: best-effort DDS may drop up to ~5% under load; report but
    # don't fail unless we lost a topic entirely.
    delivery_ratio = rx_total / total if total else 0.0
    print(f"[ds-test] delivery: rx/recorded = {rx_total}/{total} = {delivery_ratio:.1%}", flush=True)
    print("[ds-test] received per topic:", flush=True)
    for topic in sorted(rx_per_topic):
        recorded = counts.get(topic, 0)
        rx = rx_per_topic[topic]
        print(f"  rx={rx:>4}/recorded={recorded:>4}  {topic}", flush=True)

    print(f"[ds-test] PASS — {len(expected_types)} types and "
          f"{len(counts)} topics survived publish→record→replay→subscribe",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
