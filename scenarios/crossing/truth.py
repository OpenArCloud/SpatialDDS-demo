"""A participant's connection to the world service: ground truth in, actuation out.

    feed = TruthFeed("operator-a", port=8871)
    for frame in feed:             # one truth frame per step, until the end
        ...
    feed.send({"cmd": "speed", "id": "car-a1", "v": 8.0})

This is how the scene's participants see the street, and the only way: they
never import SUMO (world.py is the seam). What a participant then publishes on
the bus is its own assertion, made from what it could sense.
"""
from __future__ import annotations

import json
import socket
import time
from typing import Any, Dict, Iterator, Optional


class TruthFeed:
    """Newline-delimited JSON from `world_service.py`."""

    def __init__(self, name: str, *, host: str = "127.0.0.1", port: int,
                 connect_s: float = 30.0) -> None:
        self.name = name
        deadline = time.time() + connect_s
        while True:
            try:
                self._sock = socket.create_connection((host, port), timeout=5)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.1)
        self._sock.settimeout(None)
        self._lines = self._sock.makefile("r", encoding="utf-8")
        self.send({"cmd": "hello", "name": name})
        self.hello: Optional[Dict[str, Any]] = None

    def send(self, cmd: Dict[str, Any]) -> None:
        self._sock.sendall((json.dumps(cmd) + "\n").encode())

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        for line in self._lines:
            msg = json.loads(line)
            kind = msg.get("type")
            if kind == "hello":
                self.hello = msg
            elif kind == "truth":
                yield msg
            elif kind == "end":
                return

    def stamp_of(self, t: float) -> float:
        """A record's stamp for simulation time `t`: the session's epoch plus
        `t`. At rate 1 that is also the wall time it was written; at any
        other rate it is still the simulation's clock, which is the one the
        scene's events are measured on."""
        return float(self.hello["epoch"]) + t

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
