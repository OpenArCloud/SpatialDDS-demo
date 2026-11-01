"""What a sensing asserter holds: one track per thing it sees, and how each ends.

The rule mmwave-ut-04 has kept since Batch 1, shared now with the operators'
perception, so both say the same thing the same way:

* seen this step: the track is ACTIVE at where it was seen, its pose on the
  fast lane;
* not seen: UNOBSERVED, at the last place it was seen (not where the world
  now has it), with what hides it;
* not seen for `lost_after_s`, or gone from the street: withdrawn, "track
  lost".

Each track keeps its recent published poses (`history`), with their stamps,
so an asserter can name the very records a claim was derived from.
"""
from __future__ import annotations

from collections import deque
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.owm import Basis, LifecycleState

from scenarios.crossing.scene import (
    Publisher, entity, footprint_extent, oriented_dims)

#: (t, stamp, actor state as seen)
Fix = Tuple[float, Time, dict]
#: How many published poses each track remembers.
HISTORY = 20


class Tracks:
    """One asserter's tracks. `props(aid, actor)` gives a track's properties
    beyond the label and the oriented box."""

    def __init__(self, pub: Publisher, eid: Callable[[str], str], *,
                 props: Callable[[str, dict], Iterable[Tuple[str, str]]],
                 lost_after_s: float = 2.0) -> None:
        self.pub = pub
        self.eid = eid
        self.props = props
        self.lost_after_s = lost_after_s
        self.last_seen: Dict[str, float] = {}
        self.last: Dict[str, dict] = {}
        self.history: Dict[str, Deque[Fix]] = {}
        #: Tracks withdrawn this step, for the asserter's claims.
        self.lost: List[str] = []

    def _record(self, aid: str, a: dict, stamp: Time,
                state: LifecycleState = LifecycleState.ACTIVE,
                reason: str = ""):
        return entity(
            self.eid(aid), Basis.OBSERVED, a["kind"],
            source=self.pub.source, stamp=stamp,
            pose=(a["x"], a["y"], a["z"], a["yaw_deg"]),
            extent=footprint_extent(a["x"], a["y"], a["z"], a["yaw_deg"],
                                    a["length"], a["width"], a["height"]),
            properties=[("demo.label", aid), *self.props(aid, a),
                        *oriented_dims(a["length"], a["width"], a["height"])],
            state=state, state_reason=reason)

    def update(self, t: float, stamp: Time, actors: Dict[str, dict],
               seen: Dict[str, Optional[str]], arrived: Iterable[str]) -> None:
        """`seen` maps each actor in range to None if seen, or to what blocks
        it; actors out of range are absent from it."""
        self.lost = []
        gone = set(arrived)
        for aid, blocked in seen.items():
            if blocked is None:
                a = actors[aid]
                self.last_seen[aid] = t
                self.last[aid] = a
                self.history.setdefault(aid, deque(maxlen=HISTORY)).append(
                    (t, stamp, a))
                self.pub.assert_entity(self._record(aid, a, stamp),
                                       now=t, moving=True)
        for aid in list(self.last_seen):
            if self.last_seen[aid] == t:
                continue
            if t - self.last_seen[aid] >= self.lost_after_s or aid in gone:
                self.pub.withdraw(self.eid(aid), "track lost")
                del self.last_seen[aid], self.last[aid]
                self.history.pop(aid, None)
                self.lost.append(aid)
                continue
            held = self.pub.held().get(self.eid(aid))
            if held is not None and held.state == LifecycleState.ACTIVE:
                why = (f"out of view: behind {seen[aid]}" if seen.get(aid)
                       else "out of range")
                self.pub.assert_entity(self._record(
                    aid, self.last[aid], stamp, LifecycleState.UNOBSERVED, why),
                    now=t, moving=False)
        self.pub.settle(t)

    def active(self, aid: str) -> bool:
        return self.last_seen.get(aid) is not None and aid in self.last
