#!/usr/bin/env python3
"""Launch the full multi-operator fusion demo (6 processes).

  * 3 AV fleet publishers (operator_a/b/c) replaying the same nuScenes
    scene with different spatial offsets and sensor modality filters.
  * 1 infrastructure publisher replaying DeepSense Scenario 9.
  * 1 platform fusion service subscribing to every ``sensing/detection3d/v1``
    topic and emitting FusedTrack + coverage metrics.
  * 1 Rerun subscriber rendering per-operator streams and the fused view.

All processes share a single CycloneDDS domain so they auto-discover each
other. ``CYCLONEDDS_URI`` defaults to the repo's ``/etc/cyclonedds.xml``.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

SELF_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    sensor_filter: str
    offset: Tuple[float, float, float]


# Matches design spec: A=south/full suite, B=east/camera-only, C=west/lidar+radar.
DEFAULT_OPERATORS: List[OperatorSpec] = [
    OperatorSpec("operator_a", "full",        (0.0, -60.0, 0.0)),
    OperatorSpec("operator_b", "camera",      (60.0, 0.0, 0.0)),
    OperatorSpec("operator_c", "lidar_radar", (-60.0, 0.0, 0.0)),
]
# Infrastructure sits at the NW corner of the conceptual intersection.
DEFAULT_INFRA_OFFSET: Tuple[float, float, float] = (-30.0, 30.0, 0.0)


def _spawn(cmd: List[str], env: dict, label: str) -> subprocess.Popen:
    print(f"[run_demo] spawn {label}: {' '.join(cmd)}", file=sys.stderr)
    return subprocess.Popen(cmd, env=env)


def _build_env(domain: int) -> dict:
    env = os.environ.copy()
    env.setdefault("SPATIALDDS_TRANSPORT", "dds")
    env.setdefault("SPATIALDDS_DDS_DOMAIN", str(domain))
    env.setdefault("CYCLONEDDS_URI", "file:///etc/cyclonedds.xml")
    return env


def _subscriber_cmd(args: argparse.Namespace) -> List[str]:
    cmd = [sys.executable, str(SELF_DIR / "subscriber_rerun.py"),
           "--dataroot", args.nuscenes_dataroot,
           "--domain", str(args.domain)]
    if args.spawn_viewer:
        cmd.append("--spawn-viewer")
    if args.rerun_connect:
        cmd.extend(["--connect-grpc", args.rerun_connect])
    return cmd


def _fusion_cmd(args: argparse.Namespace) -> List[str]:
    return [sys.executable, str(SELF_DIR / "fusion_service.py"),
            "--domain", str(args.domain),
            "--tick-hz", str(args.tick_hz)]


def _operator_cmd(args: argparse.Namespace, op: OperatorSpec) -> List[str]:
    return [sys.executable, str(SELF_DIR / "publisher.py"),
            "--operator", op.name,
            "--sensor-filter", op.sensor_filter,
            "--offset-x", str(op.offset[0]),
            "--offset-y", str(op.offset[1]),
            "--offset-z", str(op.offset[2]),
            "--dataroot", args.nuscenes_dataroot,
            "--version", args.nuscenes_version,
            "--scene", args.nuscenes_scene,
            "--domain", str(args.domain),
            "--rate-hz", str(args.rate_hz),
            "--max-samples", str(args.max_samples)]


def _infra_cmd(args: argparse.Namespace) -> List[str]:
    ox, oy, oz = args.infra_offset
    return [sys.executable, str(SELF_DIR / "infrastructure_publisher.py"),
            "--dataroot", args.deepsense_dataroot,
            "--sequence", str(args.deepsense_sequence),
            "--domain", str(args.domain),
            "--speed", str(args.deepsense_speed),
            "--max-samples", str(args.infra_max_samples or args.max_samples),
            "--offset-x", str(ox), "--offset-y", str(oy), "--offset-z", str(oz)]


def _wait(procs: List[Tuple[str, subprocess.Popen]]) -> int:
    """Block until all publishers have exited, then shut the rest down.

    The subscriber and fusion service run until interrupted — their
    early exit is an error. Publishers run for their configured sample
    count (which can differ between AV and infra streams), so we wait
    for all of them before tearing down service processes.
    """
    publisher_labels = {"operator_a", "operator_b", "operator_c", "infrastructure"}
    remaining_publishers = {label for label, _ in procs if label in publisher_labels}
    worst_rc = 0
    try:
        while remaining_publishers:
            for label, p in procs:
                rc = p.poll()
                if rc is None:
                    continue
                if label in publisher_labels:
                    if label in remaining_publishers:
                        print(f"[run_demo] {label} done (rc={rc})", file=sys.stderr)
                        remaining_publishers.discard(label)
                        if rc != 0 and worst_rc == 0:
                            worst_rc = rc
                    continue
                print(f"[run_demo] {label} exited early rc={rc}; aborting", file=sys.stderr)
                return 1
            time.sleep(0.2)
        print("[run_demo] all publishers done; shutting down", file=sys.stderr)
        return worst_rc
    except KeyboardInterrupt:
        return 130


def _shutdown(procs: List[Tuple[str, subprocess.Popen]]) -> None:
    for label, p in procs:
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
    time.sleep(0.5)
    for label, p in procs:
        if p.poll() is None:
            p.terminate()


def run(args: argparse.Namespace) -> int:
    env = _build_env(args.domain)
    procs: List[Tuple[str, subprocess.Popen]] = []

    try:
        procs.append(("subscriber", _spawn(_subscriber_cmd(args), env, "subscriber")))
        time.sleep(0.5)
        procs.append(("fusion", _spawn(_fusion_cmd(args), env, "fusion")))
        time.sleep(0.3)

        if not args.skip_infra:
            procs.append(("infrastructure", _spawn(_infra_cmd(args), env, "infrastructure")))

        for op in DEFAULT_OPERATORS:
            procs.append((op.name, _spawn(_operator_cmd(args, op), env, op.name)))

        return _wait(procs)
    finally:
        _shutdown(procs)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-operator fusion demo launcher (6 processes)")
    p.add_argument("--nuscenes-dataroot", required=True)
    p.add_argument("--nuscenes-version", default="v1.0-mini")
    p.add_argument("--nuscenes-scene", default="scene-0061")
    p.add_argument("--rate-hz", type=float, default=2.0)

    p.add_argument("--deepsense-dataroot", required=True)
    p.add_argument("--deepsense-sequence", type=int, default=1)
    p.add_argument("--deepsense-speed", type=float, default=1.0)
    p.add_argument("--skip-infra", action="store_true", help="Run without the infrastructure publisher")

    p.add_argument("--domain", type=int, default=1)
    p.add_argument("--tick-hz", type=float, default=2.0)
    p.add_argument("--max-samples", type=int, default=0,
                   help="Per-publisher sample cap (0 = scene end). Applies to AV publishers; "
                        "infra uses --infra-max-samples if given.")
    p.add_argument("--infra-max-samples", type=int, default=0,
                   help="Override sample cap for the infra publisher (10 Hz) to match the "
                        "slower AV publishers' (2 Hz) runtime. 0 = inherit --max-samples.")

    p.add_argument("--spawn-viewer", action="store_true")
    p.add_argument("--rerun-connect", default="")
    p.add_argument("--infra-offset", nargs=3, type=float, metavar=("X", "Y", "Z"),
                   default=list(DEFAULT_INFRA_OFFSET))
    return p.parse_args()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
