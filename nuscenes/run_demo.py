#!/usr/bin/env python3
"""Run nuScenes publisher + Rerun subscriber together."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run nuScenes -> SpatialDDS -> Rerun demo")
    parser.add_argument("--dataroot", required=True)
    parser.add_argument("--scene", default="scene-0061")
    parser.add_argument("--version", default="v1.0-mini")
    parser.add_argument("--domain", type=int, default=1)
    parser.add_argument("--rate-hz", type=float, default=2.0)
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--spawn-viewer", action="store_true")
    parser.add_argument("--rerun-connect", default="", help="Rerun gRPC endpoint, e.g. 127.0.0.1:9876")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = Path(__file__).resolve().parent

    env = os.environ.copy()
    env.setdefault("SPATIALDDS_TRANSPORT", "dds")
    env.setdefault("SPATIALDDS_DDS_DOMAIN", str(args.domain))
    env.setdefault("CYCLONEDDS_URI", "file:///etc/cyclonedds.xml")

    sub_cmd = [
        sys.executable,
        str(base / "subscriber_rerun.py"),
        "--dataroot",
        args.dataroot,
        "--domain",
        str(args.domain),
    ]
    if args.spawn_viewer:
        sub_cmd.append("--spawn-viewer")
    if args.rerun_connect:
        sub_cmd.extend(["--connect-grpc", args.rerun_connect])
    subscriber = subprocess.Popen(sub_cmd, env=env)

    publisher = subprocess.Popen(
        [
            sys.executable,
            str(base / "publisher.py"),
            "--dataroot",
            args.dataroot,
            "--version",
            args.version,
            "--scene",
            args.scene,
            "--domain",
            str(args.domain),
            "--rate-hz",
            str(args.rate_hz),
            "--max-samples",
            str(args.max_samples),
        ],
        env=env,
    )

    try:
        while True:
            pub_rc = publisher.poll()
            sub_rc = subscriber.poll()
            if sub_rc is not None and pub_rc is None:
                print(f"[run_demo] subscriber exited early with code {sub_rc}", file=sys.stderr)
                publisher.terminate()
                return 1
            if pub_rc is not None:
                return pub_rc
            time.sleep(0.1)
    except KeyboardInterrupt:
        return 130
    finally:
        for proc in (publisher, subscriber):
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        for proc in (publisher, subscriber):
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
