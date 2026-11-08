#!/usr/bin/env python3
"""
Record the relationship lane's lifecycle as a reader sees it: JSON lines.

The claims tooth's bus-side witness. Every sample the lane delivers is one
line: receive time, the claim id, its asserter, basis and lifecycle state
when it has a payload, and the middleware's instance state always. That last
field is the whole point: a crash and a retirement differ only there and in
the lifecycle member, so a recorder that kept payloads only could not tell
them apart.

    python3 tools/claims_watch.py OUT.jsonl      # until SIGTERM

Reads through `spatialdds_ros.transport.take_with_state`, the library's own
one place for lifecycle notifications.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time

from cyclonedds.domain import DomainParticipant

from spatialdds_idl18.oarc_owm import Relationship
from spatialdds_ros import transport as tt
from spatialdds_ros.qos import MODEL_LATCHED
from spatialdds_ros.topics import Topics

SCENE = "ut-austin-littlefield"


def main() -> int:
    out = open(sys.argv[1], "a", buffering=1)
    domain = int(os.environ.get("SPATIALDDS_DDS_DOMAIN", "1"))
    reader = tt.make_reader(DomainParticipant(domain),
                            Topics(SCENE).relationship, Relationship,
                            MODEL_LATCHED.name)
    handles = {}
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    print(f"claims_watch: recording {Topics(SCENE).relationship} on domain "
          f"{domain}", flush=True)
    while not stop:
        for s in tt.take_with_state(reader):
            row = {"t": round(time.time(), 3), "instance": s.state_name}
            if s.data is not None:
                e = s.data
                handles[s.instance_handle] = e.id
                row.update(id=e.id, asserter=e.asserter,
                           basis=e.basis.name, state=e.state.name,
                           reason=e.state_reason, subject=e.subject_ref,
                           predicate=e.predicate, object=e.object_ref)
            else:
                row["id"] = handles.get(s.instance_handle)
            out.write(json.dumps(row) + "\n")
        time.sleep(0.05)
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
