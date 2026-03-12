#!/usr/bin/env python3
"""Run DeepSense publisher + subscriber together."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DeepSense -> SpatialDDS -> Rerun demo")
    parser.add_argument("--dataroot", required=True)
    parser.add_argument("--sequence", type=int, default=1)
    parser.add_argument("--domain", type=int, default=1)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--spawn-viewer", action="store_true")
    parser.add_argument("--rerun-connect", default="")
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
    if args.max_samples > 0:
        sub_cmd.extend(["--max-frames", str(args.max_samples)])
    if args.spawn_viewer:
        sub_cmd.append("--spawn-viewer")
    if args.rerun_connect:
        sub_cmd.extend(["--connect-grpc", args.rerun_connect])
    subscriber = subprocess.Popen(sub_cmd, env=env)
    time.sleep(1.0)

    pub_cmd = [
        sys.executable,
        str(base / "publisher.py"),
        "--dataroot",
        args.dataroot,
        "--sequence",
        str(args.sequence),
        "--domain",
        str(args.domain),
        "--speed",
        str(args.speed),
        "--max-samples",
        str(args.max_samples),
    ]
    publisher = subprocess.Popen(pub_cmd, env=env)

    try:
        publisher_done_at = None
        while True:
            pub_rc = publisher.poll()
            sub_rc = subscriber.poll()
            if sub_rc is not None and pub_rc is None:
                print(f"[deepsense-run_demo] subscriber exited early with code {sub_rc}", file=sys.stderr)
                publisher.terminate()
                return 1
            if pub_rc is not None:
                if publisher_done_at is None:
                    publisher_done_at = time.time()
                if sub_rc is not None or (time.time() - publisher_done_at) >= 1.0:
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
