#!/usr/bin/env python3
"""Build the crossing scene's SUMO network from its hand-authored sources.

    python3 -m scenarios.crossing.build_net           # write crossing.net.xml
    python3 -m scenarios.crossing.build_net --check   # exit 1 if it is stale

The sources are `net/crossing.{nod,edg,con}.xml`. The built network is
committed beside them, so a run needs no netconvert step and a reviewer can
read what SUMO runs. `--check`, which the scene's tests run, rebuilds it and
compares, so the committed file cannot drift from its sources. netconvert
stamps the date and its own command line, with the builder's absolute paths,
into a header comment; the committed file is written without it, and that
comment is the one thing compared without.

This module and `world.py` are the scene's SUMO side; nothing else imports
SUMO (scenarios/crossing/tests/test_crossing_world.py holds that).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

NET_DIR = Path(__file__).resolve().parent / "net"
SOURCES = {"--node-files": "crossing.nod.xml",
           "--edge-files": "crossing.edg.xml",
           "--connection-files": "crossing.con.xml"}
NET = NET_DIR / "crossing.net.xml"

OPTIONS = [
    # The scene frame is the authored one: netconvert would otherwise move
    # the origin to the network's corner.
    "--offset.disable-normalization", "true",
    "--no-turnarounds", "true",
    "--walkingareas", "true",
    "--no-warnings", "true",
]


def sumo_bin(tool: str) -> str:
    import sumo                                  # the pinned eclipse-sumo wheel
    return os.path.join(sumo.SUMO_HOME, "bin", tool)


def build(out: Path) -> None:
    cmd = [sumo_bin("netconvert")]
    for flag, name in SOURCES.items():
        cmd += [flag, str(NET_DIR / name)]
    cmd += OPTIONS + ["-o", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def without_header(xml: str) -> str:
    """The network without netconvert's generated-on comment."""
    return re.sub(r"<!-- generated on .*?-->\s*", "", xml, count=1, flags=re.S)


def stale() -> bool:
    with tempfile.TemporaryDirectory() as d:
        fresh = Path(d) / "crossing.net.xml"
        build(fresh)
        return (without_header(fresh.read_text())
                != without_header(NET.read_text()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="rebuild into a temporary file and compare")
    args = ap.parse_args()
    if args.check:
        if stale():
            print(f"build_net: {NET.name} is stale; rebuild it")
            return 1
        print(f"build_net: {NET.name} matches its sources")
        return 0
    build(NET)
    NET.write_text(without_header(NET.read_text()))
    print(f"build_net: wrote {NET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
