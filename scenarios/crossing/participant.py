"""The loop every crossing participant runs: truth in, its own records out.

    python3 -m scenarios.crossing.<asserter> --domain 47 --port 8871

Each asserter is a plain SDK participant (`sd.participant`), named
`svc:crossing:<name>`, that reads the world service's feed and publishes only
what it asserts. A clean stop (SIGTERM, or the session ending) closes the
participant with `asserter stopped: ...`, which withdraws what it held; a
SIGKILL withdraws nothing, and its readers see liveliness end.
"""
from __future__ import annotations

import argparse
import signal
import sys
from typing import Callable, Optional

import spatialdds as sd

from spatialdds_demo import claims
from scenarios.crossing.scene import SCENE, service_id, to_time
from scenarios.crossing.truth import TruthFeed


class Stop(Exception):
    pass


def _stop(*_):
    raise Stop()


def parser(doc: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=doc.split("\n\n")[0])
    ap.add_argument("--domain", type=int, default=None,
                    help="DDS domain (default SPATIALDDS_DDS_DOMAIN)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True,
                    help="the world service's feed")
    return ap


def run(name: str, make: Callable[["sd.dds.Participant"], object],
        args: argparse.Namespace) -> int:
    """Open the participant, build the asserter with `make(participant)`,
    then call its `on_truth(frame, stamp)` for every truth frame, and
    `on_start(stamp)` once at time 0 if it has one."""
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    sid = service_id(name)
    participant = sd.participant(SCENE, domain=args.domain, service_id=sid)
    asserter = make(participant)
    feed: Optional[TruthFeed] = None
    reason = f"{claims.ASSERTER_STOPPED}: {sid} stopped cleanly"
    try:
        feed = TruthFeed(name, host=args.host, port=args.port)
        if hasattr(asserter, "feed"):
            # An actuator commands the world on the same connection.
            asserter.feed = feed
        started = False
        for frame in feed:
            if not started:
                if hasattr(asserter, "on_start"):
                    asserter.on_start(to_time(feed.stamp_of(0.0)))
                started = True
            asserter.on_truth(frame, to_time(feed.stamp_of(frame["t"])))
        reason = f"{claims.ASSERTER_STOPPED}: {sid}, the session ended"
    except Stop:
        pass
    finally:
        if feed is not None:
            feed.close()
        participant.close(reason=reason)
    print(f"{name}: closed ({reason})", flush=True)
    return 0


def main_for(name: str, make, doc: str) -> None:
    sys.exit(run(name, make, parser(doc).parse_args()))
