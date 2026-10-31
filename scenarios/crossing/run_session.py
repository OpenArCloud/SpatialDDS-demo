#!/usr/bin/env python3
"""Run the crossing scene once and record it.

    python3 -m scenarios.crossing.run_session --domain 61 \
        --out recordings/crossing.mcap

Starts, as separate processes on one DDS domain:

* the recorder (`record.py`), first, as a peer like any other consumer;
* the world service (`world_service.py`), which waits for every participant
  before time 0;
* the participants: the map author, operator-a and operator-b, mmwave-ut-04
  and gate-ctl-9.

Then waits for the world to end, for each participant to close (each
withdraws what it held, "asserter stopped: ... the session ended"), and,
`TAIL_S` after the last of them, ends the recorder, so the recording holds
every withdrawal however long the participants took. Writes beside the recording its provenance and the world's
truth log (`.truth.jsonl.gz`), the ground truth the scene is measured
against. Every process it started is stopped by its PID before it returns.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[2]
#: How long the recorder keeps listening after the last participant closes.
TAIL_S = 3.0
SCENARIO = json.loads((Path(__file__).resolve().parent / "scenario.json").read_text())

PARTICIPANTS = {
    "map": ["-m", "scenarios.crossing.map_author"],
    "operator-a": ["-m", "scenarios.crossing.operator", "--name", "operator-a"],
    "operator-b": ["-m", "scenarios.crossing.operator", "--name", "operator-b"],
    SCENARIO["sensing_unit"]["id"]: ["-m", "scenarios.crossing.mmwave"],
    SCENARIO["gate"]["controller"]: ["-m", "scenarios.crossing.gate_ctl"],
}


def run(out: Path, domain: int, rate: float, seed: int, *,
        logs: Path, settle_s: float = 4.0) -> Dict[str, object]:
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
    py = sys.executable
    procs: Dict[str, subprocess.Popen] = {}
    duration = float(SCENARIO["sim"]["duration_s"])
    wall = duration / rate if rate > 0 else duration

    def start(name: str, argv: List[str]) -> subprocess.Popen:
        log = open(logs / f"{name}.log", "w")
        p = subprocess.Popen([py, *argv], cwd=REPO, env=env, stdout=log,
                             stderr=subprocess.STDOUT)
        procs[name] = p
        return p

    out.parent.mkdir(parents=True, exist_ok=True)
    truth_log = out.with_suffix(".truth.jsonl.gz")
    port_file = logs / "world.port"
    # An upper bound only: the recorder is ended TAIL_S after the session.
    record_s = settle_s + 60.0 + 2 * wall
    try:
        start("recorder", ["-m", "scenarios.crossing.record",
                           "--seconds", f"{record_s:.0f}", "--domain", str(domain),
                           "--out", str(out), "--driver",
                           f"scenarios/crossing/run_session.py (seed {seed}, "
                           f"rate {rate:g}, scripted: no person drove it)"])
        time.sleep(settle_s)
        start("world", ["-m", "scenarios.crossing.world_service", "--port", "0",
                        "--port-file", str(port_file), "--rate", str(rate),
                        "--seed", str(seed), "--truth-log", str(truth_log),
                        "--expect", *PARTICIPANTS])
        deadline = time.time() + 20
        while not port_file.exists() or not port_file.read_text():
            if time.time() > deadline or procs["world"].poll() is not None:
                raise RuntimeError("the world service did not start; see "
                                   f"{logs / 'world.log'}")
            time.sleep(0.05)
        port = port_file.read_text().strip()
        for name, argv in PARTICIPANTS.items():
            start(name, [*argv, "--domain", str(domain), "--port", port])
        world_rc = procs["world"].wait(timeout=wall + 120)
        for name in PARTICIPANTS:
            procs[name].wait(timeout=wall + 120)
        time.sleep(TAIL_S)
        procs["recorder"].send_signal(signal.SIGTERM)
        recorder_rc = procs["recorder"].wait(timeout=60)
    finally:
        for name, p in procs.items():
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
    rcs = {name: p.returncode for name, p in procs.items()}
    return {"world": world_rc, "recorder": recorder_rc, "returncodes": rcs,
            "truth_log": str(truth_log)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path,
                    default=REPO / "recordings" / f"{SCENARIO['scene']}.mcap")
    ap.add_argument("--domain", type=int, default=61)
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=SCENARIO["sim"]["seed"])
    ap.add_argument("--logs", type=Path, default=None,
                    help="where each process's output goes (default: a "
                         "temporary directory, printed)")
    args = ap.parse_args()
    logs = args.logs or Path(tempfile.mkdtemp(prefix="crossing-logs-"))
    logs.mkdir(parents=True, exist_ok=True)
    print(f"session: domain {args.domain}, seed {args.seed}, rate {args.rate:g}, "
          f"logs {logs}", flush=True)
    result = run(args.out, args.domain, args.rate, args.seed, logs=logs)
    for name, rc in result["returncodes"].items():
        print(f"session: {name:14} exit {rc}")
    print((logs / "recorder.log").read_text().rstrip())
    bad = {n: rc for n, rc in result["returncodes"].items() if rc != 0}
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
