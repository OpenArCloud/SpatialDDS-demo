#!/usr/bin/env python3
"""The world service: SUMO stepped in real time, ground truth served over TCP.

    python3 -m scenarios.crossing.world_service --port 8871 \
        --expect operator-a operator-b mmwave-ut-04 gate-ctl-9 map-author \
        --truth-log /tmp/truth.jsonl.gz

This is the seam's process. It runs `world.World` and is the only process of
the scene that does; every participant learns the street from it and nothing
else. Ground truth is **not on the bus**: it travels on this TCP feed, one
JSON object per line, so the DDS bus carries only what participants assert,
and a consumer that reads the bus cannot see the simulator.

The feed, newline-delimited JSON:

* a client sends `{"cmd": "hello", "name": ...}` first, then any commands
  (`world.World.command`);
* the service sends `{"type": "hello", ...}` with the scenario and the
  session's `epoch` (the wall time of simulation time 0), then one
  `{"type": "truth", ...}` per step, then `{"type": "end"}`.

**Time.** Stepping waits for every `--expect`ed client, so each participant
sees time 0. Then simulation time is paced to the wall clock at `--rate`
(simulation seconds per wall second; 0 runs unpaced). A record's stamp is
`epoch + t`, so at rate 1 a stamp is also when it was written.

The truth log, if asked for, holds every truth frame: the ground truth the
scene's acceptance measures against.
"""
from __future__ import annotations

import argparse
import gzip
import json
import queue
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from scenarios.crossing.world import World, load_scenario


class Feed:
    """The TCP side: accepts clients, broadcasts lines, collects commands."""

    def __init__(self, host: str, port: int) -> None:
        self.server = socket.create_server((host, port), reuse_port=False)
        self.port = self.server.getsockname()[1]
        self.clients: Dict[str, socket.socket] = {}
        self.commands: "queue.Queue[dict]" = queue.Queue()
        self.lock = threading.Lock()
        self.hello: Optional[bytes] = None
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,),
                             daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        name = None
        try:
            for line in conn.makefile("r", encoding="utf-8"):
                msg = json.loads(line)
                if msg.get("cmd") == "hello":
                    name = str(msg.get("name") or f"anon-{id(conn)}")
                    with self.lock:
                        if self.hello is not None:
                            conn.sendall(self.hello)
                        self.clients[name] = conn
                else:
                    self.commands.put(dict(msg, sender=name))
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                if name and self.clients.get(name) is conn:
                    del self.clients[name]
            conn.close()

    def names(self) -> List[str]:
        with self.lock:
            return sorted(self.clients)

    def broadcast(self, msg: dict) -> None:
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        with self.lock:
            if msg.get("type") == "hello":
                self.hello = data
            for name, conn in list(self.clients.items()):
                try:
                    conn.sendall(data)
                except OSError:
                    del self.clients[name]

    def close(self) -> None:
        self.server.close()
        with self.lock:
            for conn in self.clients.values():
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                    conn.close()
                except OSError:
                    pass
            self.clients.clear()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", type=Path, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="simulation seconds per wall second (0: unpaced)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--expect", nargs="*", default=[],
                    help="client names to wait for before time 0")
    ap.add_argument("--wait-s", type=float, default=60.0,
                    help="how long to wait for them")
    ap.add_argument("--truth-log", type=Path, default=None)
    ap.add_argument("--port-file", type=Path, default=None,
                    help="write the listening port here once bound")
    args = ap.parse_args()

    scenario = load_scenario(args.scenario)
    feed = Feed(args.host, args.port)
    if args.port_file:
        args.port_file.write_text(str(feed.port))
    print(f"world: feed on {args.host}:{feed.port}", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    deadline = time.time() + args.wait_s
    while not stop.is_set() and not set(args.expect) <= set(feed.names()):
        if time.time() > deadline:
            missing = sorted(set(args.expect) - set(feed.names()))
            print(f"world: gave up waiting for {missing}", flush=True)
            feed.close()
            return 2
        time.sleep(0.05)

    world = World(scenario, seed=args.seed)
    world.start()
    log = gzip.open(args.truth_log, "wt") if args.truth_log else None
    epoch = time.time()
    hello = {"type": "hello", "epoch": epoch, "seed": world.seed,
             "step_s": world.step_s, "rate": args.rate,
             "scenario": scenario}
    feed.broadcast(hello)
    if log:
        log.write(json.dumps(hello) + "\n")
    print(f"world: t=0 at epoch {epoch:.3f}, seed {world.seed}, "
          f"rate {args.rate:g}, clients {feed.names()}", flush=True)

    try:
        while not stop.is_set() and not world.done():
            while True:
                try:
                    world.command(feed.commands.get_nowait())
                except queue.Empty:
                    break
            truth = world.step()
            if args.rate > 0:
                lag = epoch + truth["t"] / args.rate - time.time()
                if lag > 0:
                    time.sleep(lag)
            truth = {"type": "truth", "wall": time.time(), **truth}
            feed.broadcast(truth)
            if log:
                log.write(json.dumps(truth, separators=(",", ":")) + "\n")
    finally:
        feed.broadcast({"type": "end", "t": world.t})
        world.close()
        if log:
            log.close()
        time.sleep(0.2)
        feed.close()
    print(f"world: end at t={world.t:.1f} after {time.time() - epoch:.1f}s",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
