#!/usr/bin/env python3
"""Run a live Open World Model session on local DDS and record it.

    python3 tools/run_owm_session.py --seconds 20 --domain 47

Three processes on one DDS domain, which is the shape the demo actually runs
in: the model service holding the venue latched and answering the command
lane, the duck mover asking for moves, and the recorder subscribing like any
other consumer. Nothing is shared in memory -- the recorder sees only what
reaches the bus.

**Why a harness rather than the demo's own entry points.** `main()` in each
module calls `require_dds_env`, which insists on
`CYCLONEDDS_URI=file:///etc/cyclonedds.xml`: the container contract the demo
ships under. This harness is the same stack with participants constructed
directly, which is how `tests/test_duck_mover.py` has always run the service
and the mover together, and it needs no file under /etc to reproduce.

One command goes through the lane on purpose. A recording whose command topic
carried nothing would have a channel with no messages in it, and "the lane
exists" would be a claim about the schema rather than about the demo.

No retirement is asked for here, and this harness is no longer what the
committed recording comes from: `tools/capture_owm_session.sh` is, running the
real entry points inside the demo image. This file stays because it needs no
container and is the quickest way to put traffic on a bus.

**If you add a removal to this harness, mind the window.** The recorder must
finish before any publisher does. `ModelPublisher.close` disposes every entity
deliberately, and a capture that outlives it collects six genuine disposes of
a venue nobody dismantled -- disposes carrying no RETIRED tombstone, which the
gateway's recording loader then drops, so the served recording would show a
venue its own metadata says was removed. The ordering contract is written out
at the top of `capture_owm_session.sh`; this harness happens to satisfy it by
recording in a subprocess that returns first.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _service(domain: int, seconds: float) -> None:
    """The model service: seed, hold latched, answer the command lane."""
    from cyclonedds.domain import DomainParticipant
    from spatialdds_demo import typed_transport as tt
    from spatialdds_demo.model_service import ModelPublisher, seed_entities
    from spatialdds_demo.qos_profiles import MODEL_COMMAND
    from spatialdds_demo.topics import TOPIC_MODEL_COMMAND_V1
    from spatialdds_idl18.spatial.owm import ModelCommand

    participant = DomainParticipant(domain)
    publisher = ModelPublisher(participant)
    for entity in seed_entities():
        publisher.publish_entity(entity)
    commands = tt.make_reader(participant, TOPIC_MODEL_COMMAND_V1,
                              ModelCommand, MODEL_COMMAND.name)
    print(f"session: service holding {len(publisher._published)} entities",
          flush=True)
    deadline = time.time() + seconds
    while time.time() < deadline:
        for command in tt.take_samples(commands) or []:
            line = publisher.handle_command(command)
            if line and not line.startswith("move "):
                print(f"session: {line}", flush=True)
        time.sleep(0.02)
    publisher.close()


def _mover(domain: int, seconds: float, seed: int) -> None:
    """The duck mover: asks the owner to move its ducks."""
    import random

    from cyclonedds.domain import DomainParticipant
    from spatialdds_demo.duck_mover import DuckMover

    time.sleep(1.0)                       # let the venue latch first
    mover = DuckMover(DomainParticipant(domain), rng=random.Random(seed))
    deadline = time.time() + seconds
    ticks = 0
    while time.time() < deadline:
        mover.tick()
        ticks += 1
        time.sleep(0.2)
    print(f"session: mover asked for {ticks} rounds of moves", flush=True)


def _one_command(domain: int) -> None:
    """One operator command through the real lane, mid-session."""
    from cyclonedds.domain import DomainParticipant
    from spatialdds_demo import typed_transport as tt
    from spatialdds_demo.model_service import POND_MIN, POND_MAX
    from spatialdds_demo.qos_profiles import MODEL_COMMAND
    from spatialdds_demo.topics import TOPIC_MODEL_COMMAND_V1
    from spatialdds_idl18.builtin import Time
    from spatialdds_idl18.spatial.core import Aabb3, PoseSE3
    from spatialdds_idl18.spatial.owm import ModelCommand

    participant = DomainParticipant(domain)
    writer = tt.make_writer(participant, TOPIC_MODEL_COMMAND_V1, ModelCommand,
                            MODEL_COMMAND.name)
    time.sleep(0.5)
    now = time.time()
    # The shallows, a metre narrower in y. A real `set_extent`: the service
    # rewrites its own latched copy and the movers read the new bounds.
    writer.write(ModelCommand(
        command_id="cmd:session:set-extent-shallows",
        verb="set_extent",
        subject_id="ent:shallows:littlefield",
        reason="recorded session: one command through the real lane",
        requester_id="tool:run_owm_session",
        has_pose=False,
        pose=PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=True,
        extent=Aabb3(min_xyz=[POND_MIN[0], POND_MIN[1] + 0.5, POND_MIN[2]],
                     max_xyz=[POND_MAX[0], POND_MAX[1] - 0.5, POND_MAX[2]]),
        stamp=Time(sec=int(now), nanosec=int((now % 1) * 1e9)),
    ))
    time.sleep(1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "",
                                     formatter_class=argparse
                                     .RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--domain", type=int, default=47)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path,
                        default=REPO / "recordings" / "owm-littlefield.mcap")
    args = parser.parse_args()

    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(target=_service, args=(args.domain, args.seconds + 4)),
        ctx.Process(target=_mover, args=(args.domain, args.seconds + 2,
                                         args.seed)),
    ]
    for p in procs:
        p.start()
    cmd_proc = None
    try:
        time.sleep(2.0)                   # discovery, then capture
        recorder = subprocess.Popen(
            [sys.executable, str(REPO / "tools" / "record_owm_session.py"),
             "--seconds", str(args.seconds), "--domain", str(args.domain),
             "--out", str(args.out)])
        cmd_proc = ctx.Process(target=_one_command, args=(args.domain,))
        cmd_proc.start()
        rc = recorder.wait()
    finally:
        for p in procs + ([cmd_proc] if cmd_proc else []):
            p.join(timeout=12)
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
    return rc


if __name__ == "__main__":
    sys.exit(main())
