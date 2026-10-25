"""
The SDK, as the venue's tests drive it.

Every test of a venue program does the same three things: join a domain as a
participant, open a lane with the venue's profile, and read what has arrived.
Since the SDK adoption those are `sd.participant`, `p.reader` / `p.writer`, and
`Reader.take()`. They are spelled here once so the tests read as what they
test, not as transport plumbing.
"""
import itertools
import unittest

import spatialdds as sd

from spatialdds_demo.lanes import MODEL_COMMAND, MODEL_FAST, MODEL_LATCHED, SCENE

#: The venue's lanes and the profile each is opened with.
PROFILE = {
    "model_entity": MODEL_LATCHED,
    "model_pose": MODEL_FAST,
    "model_command": MODEL_COMMAND,
}

_ids = itertools.count(1)


def participant(domain_id: int, service_id: str = "") -> "sd.dds.Participant":
    """A participant on the venue's scene, or a skip if this environment has no DDS.

    Each gets its own service id unless given one, because a writer needs a
    participant that says who it is.
    """
    try:
        return sd.participant(SCENE, domain=domain_id,
                              service_id=service_id or f"svc:test:{next(_ids)}")
    except Exception as exc:
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


def reader(p: "sd.dds.Participant", type_name: str, **kw) -> "sd.dds.Reader":
    return p.reader(type_name, qos_profile=PROFILE[type_name], **kw)


def writer(p: "sd.dds.Participant", type_name: str, **kw) -> "sd.dds.Writer":
    if PROFILE[type_name].latched:
        kw.setdefault("on_exit", "withdraw")
    return p.writer(type_name, qos_profile=PROFILE[type_name], **kw)


def samples(r: "sd.dds.Reader") -> list:
    """The samples that have arrived, now, as typed objects."""
    return [ev.data for ev in r.take() if isinstance(ev, sd.Sample)]
