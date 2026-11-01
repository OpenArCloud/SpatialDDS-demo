"""The scene's asserters run offline: a participant that keeps what is written.

`OfflineParticipant` stands in for `sd.participant` with writers that append
every sample (and every withdrawal) to a list instead of the bus, so an
asserter's own code, `Publisher` and `claims.ClaimWriter` included, can be
driven step by step over a truth log and what it would have published read
back. No DDS, no SUMO. What the bus itself adds (delivery, latching,
liveliness) is the whole-session test's to show, not this.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterator, List, Tuple

from scenarios.crossing.scene import service_id, to_time

RECORDING = Path(__file__).resolve().parents[1] / "recording"


class OfflineWriter:
    def __init__(self, out: List[Tuple[str, str, object]], topic: str) -> None:
        self.out, self.topic = out, topic

    def write(self, sample) -> None:
        self.out.append(("write", self.topic, sample))

    def withdraw(self, key: str, reason: str = "", **kw) -> None:
        self.out.append(("withdraw", self.topic, (key, reason, kw)))


class OfflineParticipant:
    def __init__(self, name: str) -> None:
        self.service_id = service_id(name)
        self.out: List[Tuple[str, str, object]] = []

    def writer(self, type_name, *, stream=None, **kw) -> OfflineWriter:
        name = type_name if isinstance(type_name, str) else kw.get("type_name")
        return OfflineWriter(self.out, name)

    def written(self, topic: str):
        return [s for op, t, s in self.out if op == "write" and t == topic]


def frames(truth: Path = RECORDING / "crossing.truth.jsonl.gz") -> Iterator[dict]:
    with gzip.open(truth, "rt") as f:
        for line in f:
            msg = json.loads(line)
            if msg.get("type") == "truth":
                yield msg


def drive(asserter, truth: Path = RECORDING / "crossing.truth.jsonl.gz",
          epoch: float = 1_000_000.0, until: float = 1e9) -> None:
    """Every truth frame through `asserter.on_truth`, stamped epoch + t."""
    if hasattr(asserter, "on_start"):
        asserter.on_start(to_time(epoch))
    for fr in frames(truth):
        if fr["t"] > until:
            break
        asserter.on_truth(fr, to_time(epoch + fr["t"]))
