"""The venue's lanes, said only where the SDK's registry cannot say them for us.

Since the SDK adoption (docs/sdk-adoption-census.md) the names, QoS profiles
and types of every registered lane come from `spatialdds[dds]`'s registry,
generated from the spec text at the `v1.8` tag. What is left is this
deployment's own part. `topics.py`, `qos_profiles.py` and `topic_types.py`
held all of it, beside a copy of everything the registry now holds. Here it
is the deployment's part alone:

* **The scene.** `sd.participant(SCENE, ...)` names every venue lane from it.
* **Three profiles §3.3.2 does not name.** The owm rows give templates but no
  profile (this repository's spec queue, entry 5), so a deployment declares
  its own. They are declared once, as `sd.dds.Profile`, and checked against
  what the rows spell when a writer is opened.
* **This repository's own types.** The `oarc.*` extensions are registered
  with the SDK (`sd.dds.extend`) when this module is imported, with the IDL
  they were generated from. Anything that converts, keys, records or opens
  one by name needs the registration, and importing this module is how it
  gets it.
* **Lanes outside the scene.** The VPS, bootstrap, coverage-reply and
  anchor-delta names, opened with `topic=`, because the registry builds only
  `spatialdds/<scene>/...`.
* **The web bridge's provenance labels**, which name where a topic name came
  from and are not lanes at all.
* **One labelled workaround**, for cyclonedds-python's `wait_for_acks` (below).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import spatialdds as sd

from spatialdds_demo_idl18.oarc_demo import (
    BootstrapQuery, BootstrapResponse, FusionCoverage)

SCENE = "ut-austin-littlefield"

# -- a labelled workaround -----------------------------------------------------
# **Until cyclonedds-python fixes `pub.py:325`.** `DataWriter.wait_for_acks`
# is documented to return False when its timeout elapses. In 11.0.1 that branch
# reads `Exception.DDS_RETCODE_TIMEOUT`, which does not exist, so every timeout
# raises AttributeError instead. The SDK's `close()` awaits acks after writing
# each dispose and Depart, so a clean stop with one slow peer exits with a
# traceback and skips its remaining flushes. Measured: a reader whose process
# is SIGSTOPped makes `close()` raise, every time. Filed against spatialdds-py
# as S17. This restores the documented contract and nothing else, in one
# place, for every process here that opens an SDK lane (they import this
# module). tests/test_ack_timeout_workaround.py fails once cyclonedds no
# longer has the bug, so this is removed rather than outliving it.

def _ack_timeout_is_false() -> bool:
    try:
        from cyclonedds.pub import DataWriter
    except ImportError:                       # no [dds]: nothing to wait on
        return False
    if getattr(DataWriter.wait_for_acks, "_oarc_workaround", False):
        return True
    broken = DataWriter.wait_for_acks

    def wait_for_acks(self, timeout):
        try:
            return broken(self, timeout)
        except AttributeError as error:
            if "DDS_RETCODE_TIMEOUT" not in str(error):
                raise
            return False

    wait_for_acks._oarc_workaround = True
    wait_for_acks._oarc_broken = broken
    wait_for_acks.__doc__ = broken.__doc__
    DataWriter.wait_for_acks = wait_for_acks
    return True


ACK_TIMEOUT_WORKAROUND = _ack_timeout_is_false()

# -- profiles ---------------------------------------------------------------

#: The latched model lane: definitions a late joiner has to be handed. The
#: columns are MAP_META's; the name is the deployment's, as it always was.
MODEL_LATCHED = sd.dds.Profile("MODEL_LATCHED", reliability="RELIABLE",
                               durability="TRANSIENT_LOCAL",
                               history="KEEP_LAST", depth=1)

#: The fast pose lane. BEST_EFFORT because a pose that missed its moment is
#: worth less than the next one, VOLATILE because a late joiner should read
#: the latched entity rather than a pose from before it existed. Not POSE_RT,
#: which declares a 33 ms deadline: this demo publishes at about 2 Hz, and a
#: borrowed deadline would be a contract the publisher has no intention of
#: meeting.
MODEL_FAST = sd.dds.Profile("MODEL_FAST", reliability="BEST_EFFORT",
                            durability="VOLATILE", history="KEEP_LAST", depth=1)

#: Commands are events, not state. RELIABLE so a retirement is not lost in
#: transit, VOLATILE so a service restarting does not replay yesterday's.
#: KEEP_LAST(1), as §3.3.2 spells the `model_command` row: the lane is keyed
#: on `command_id` and every command mints its own, so depth is per command
#: and one is enough. (It said KEEP_LAST(16) and "unkeyed" until the
#: adoption; the type had been keyed all along.)
MODEL_COMMAND = sd.dds.Profile("MODEL_COMMAND", reliability="RELIABLE",
                               durability="VOLATILE", history="KEEP_LAST",
                               depth=1)

#: Where an asker's coverage replies arrive. `CoverageResponse` has no §3.3.2
#: row, so the reply lane is the deployment's. RELIABLE + VOLATILE: a reply
#: answers one query, and a late joiner has no business being handed it. The
#: asker's reader keeps every reply (`history="KEEP_ALL"`), because every
#: service covering the area answers on the one topic and the type has no
#: key. Until the SDK adoption both lanes were ZONE_META, TRANSIENT_LOCAL: a
#: service that joined later was handed old queries to answer.
COVERAGE_REPLY = sd.dds.Profile("COVERAGE_REPLY", reliability="RELIABLE",
                                durability="VOLATILE", history="KEEP_LAST",
                                depth=1)

#: The deployment's profiles by name, for a program that reads a profile name
#: from an announce (`TopicMeta.qos_profile`) and has to open a matching
#: reader. Registered §3.3.3 names come from `sd.dds.registry.profile`.
PROFILES: Dict[str, sd.dds.Profile] = {
    p.name: p for p in (MODEL_LATCHED, MODEL_FAST, MODEL_COMMAND,
                        COVERAGE_REPLY)}


def profile(name: str) -> sd.dds.Profile:
    """A profile by the name an announce carries: registered, or this deployment's."""
    if name in PROFILES:
        return PROFILES[name]
    return sd.dds.registry.profile(name)


# -- this repository's own types ----------------------------------------------

#: The IDL `spatialdds_demo_idl18` is generated from (scripts/generate_types.py).
OARC_IDL = Path(__file__).resolve().parent.parent / "idl" / "demo18" / "oarc_demo.idl"

#: Every `oarc.*` extension, by its §3.3.2-style name.
EXTENSIONS = {
    "oarc.fusion_coverage": FusionCoverage,
    "oarc.bootstrap_query": BootstrapQuery,
    "oarc.bootstrap_response": BootstrapResponse,
}
for _name, _cls in EXTENSIONS.items():
    sd.dds.extend(_name, _cls, OARC_IDL)

#: Names this deployment gives pinned types that §3.3.2 does not register.
#: `blob_chunk` is the blob lane's: the spec leaves that lane unnamed
#: (spatialdds-py candidates 9.6), so its announced `TopicMeta.type` is ours.
DEPLOYMENT_NAMES = {
    "blob_chunk": "spatial.core.BlobChunk",
    "frame_transform": "spatial.core.FrameTransform",
}


def class_for(type_name: str) -> type:
    """The generated class for any type name this deployment announces.

    The SDK's `class_for` first: registered names, well-known messages,
    dotted identities and the `oarc.*` extensions. Then this deployment's
    names for pinned types the registry does not register.
    """
    try:
        return sd.dds.class_for(type_name)
    except sd.dds.RegistryError:
        if type_name in DEPLOYMENT_NAMES:
            return sd.dds.class_for(DEPLOYMENT_NAMES[type_name])
        raise


# -- opening a lane by the names an announce or a message carries -----------
# A bridge, a recorder or a replayer learns a lane as strings: a type name, a
# topic, a profile name. These turn them into an SDK endpoint, the one way.

#: Profiles this deployment declares where §3.3.2 names none: the `oarc.*`
#: extensions, the pinned types the registry does not register, and two
#: registered rows that spell no profile. Each is a claim this deployment
#: makes, and says so here. Until the SDK adoption the last two were
#: `topic_types.profile_for`'s silent defaults (census finding 6); the SDK
#: refuses an unprofiled row rather than guess, and these are the answer.
DECLARED_PROFILES = {
    "oarc.fusion_coverage": "MAP_META",
    "oarc.bootstrap_query": "MAP_META",
    "oarc.bootstrap_response": "MAP_META",
    "blob_chunk": "GEOM_TILE",
    "frame_transform": "MODEL_LATCHED",
    "model_entity": "MODEL_LATCHED",
    "model_pose": "MODEL_FAST",
    "model_command": "MODEL_COMMAND",
    "navsat_status": "POSE_RT",       # a fix is a pose-rate sample
    "entity_binding": "MAP_META",     # a binding is state, latched
}


def endpoint(type_name: str, qos_profile: Any = None):
    """`(type_arg, keywords)` that open a lane of `type_name` on p.reader or
    p.writer. A registered type is passed by name; anything else is its
    class with `type_name=`. The profile is `qos_profile` (a name or a
    Profile) if given, else the row's, else this deployment's declaration."""
    if isinstance(qos_profile, str):
        qos_profile = profile(qos_profile)
    try:
        row = sd.dds.registry.registration(type_name)
    except sd.dds.RegistryError:
        row = None
    if qos_profile is None and (row is None or not row.profile):
        declared = DECLARED_PROFILES.get(type_name)
        qos_profile = profile(declared) if declared else None
    kw = {} if qos_profile is None else {"qos_profile": qos_profile}
    if row is not None:
        return type_name, kw
    return class_for(type_name), dict(kw, type_name=type_name)


def topic_meta(name: str, type_name: str, qos_profile: str, *,
               target_rate_hz: float = 0.0, max_chunk_bytes: int = 0) -> dict:
    """One TopicMeta row for a document (an announce or manifest built as data).

    The SDK's builder (`sd.records.topic`), which holds the name to section
    3.3.1 and derives `durability` from a registered profile. For a profile
    only this deployment declares, the durability is that declaration's: the
    SDK leaves it empty, rightly, since it cannot read a name it was never
    given. Until the SDK adoption this was `topics.topic_meta`, with its own
    table. (A participant's own announce needs none of this: its rows are
    its endpoints' own TopicMeta.)
    """
    prof = profile(qos_profile)
    return sd.records.topic(
        type_name, scene=SCENE, name=name, qos_profile=qos_profile,
        durability=None if prof.registered else prof.durability,
        rate_hz=float(target_rate_hz), max_chunk_bytes=int(max_chunk_bytes))


def topic_meta_problems(rows) -> list:
    """Why each TopicMeta row is not a lane this deployment can open, or [].

    A section 3.3.1 name (or a template's), a version, a type that resolves
    and a profile registered or declared: what `endpoint` and the SDK check
    when a lane is opened, asked of a document's rows before anything is.
    Until the SDK adoption this was `topics.validate_topic_meta`, against the
    demo's own copy of the registries.
    """
    problems = []
    for row in rows:
        name = row.get("name") or "<unnamed>"
        try:
            if not row.get("version"):
                raise ValueError("TopicMeta.version is required")
            sd.dds.registry.check_topic(name, row.get("type") or "")
            endpoint(row.get("type") or "", row.get("qos_profile") or None)
            if not row.get("qos_profile"):
                raise ValueError("TopicMeta.qos_profile is required")
        except Exception as error:
            problems.append(f"{name}: {error}")
    return problems


def _latched_and_keyed(type_name: str, qos: Any) -> bool:
    if qos is None:
        row = sd.dds.registry.registration(type_name)
        qos = profile(row.profile) if row.profile else None
    fqn = sd.dds.fqn_of(class_for(type_name))
    return bool(qos is not None and qos.latched
                and sd.dds.codec.key_member(fqn) is not None)


def open_writer(p: "sd.dds.Participant", type_name: str, topic: str, *,
                qos_profile: Any = None, on_exit: str = "withdraw"):
    """A writer for `type_name` on `topic`.

    `on_exit` is what a clean stop does to a latched keyed lane's instances,
    and applies only there (the SDK refuses it anywhere else). A producer
    withdraws what it published. A carrier, which writes what someone else
    asserted (a bridge, a replayer), passes "keep": the presence is the
    asserter's, so readers see the carrier leave and nothing removed.
    """
    type_arg, kw = endpoint(type_name, qos_profile)
    if _latched_and_keyed(type_name, kw.get("qos_profile")):
        kw["on_exit"] = on_exit
    return p.writer(type_arg, topic=topic, **kw)


def open_reader(p: "sd.dds.Participant", type_name: str, topic: str, *,
                qos_profile: Any = None, **kw):
    type_arg, ekw = endpoint(type_name, qos_profile)
    return p.reader(type_arg, topic=topic, **ekw, **kw)


# -- the blob lane ------------------------------------------------------------
# 1.8 defines BlobChunk and the rule (heavy bytes by reference, never inlined)
# and names no lane, registry row or profile for it (spatialdds-py candidates
# 9.6). This deployment's lane is `blob_chunk` on GEOM_TILE, on the
# four-segment name it has always had, opened with `topic=`.
#
# **Not renamed, deliberately.** The name is a contract with OpenVPS: its
# maplocalizer reads query imagery from exactly this topic
# (`maplocalizer/server/spatialdds/service.py:65` there) and fails a request
# whose image never arrives. The web bridge is what sends it. A scene-shaped
# name here would break localization in the OpenVPS demo, so the rename moves
# when OpenVPS moves with it, in its own adoption.

BLOB_TOPIC = "spatialdds/blob/chunk/v1"
BLOB_PROFILE = sd.dds.registry.profile("GEOM_TILE")


def blob_writer(p: "sd.dds.Participant"):
    """A writer for chunks. A clean stop keeps them: a late reader of a request
    that named a blob still gets its bytes."""
    from spatialdds.dds.types.spatial.core import BlobChunk
    return p.writer(BlobChunk, type_name="blob_chunk", topic=BLOB_TOPIC,
                    qos_profile=BLOB_PROFILE, on_exit="keep")


def blob_reader(p: "sd.dds.Participant", **kw):
    from spatialdds.dds.types.spatial.core import BlobChunk
    return p.reader(BlobChunk, type_name="blob_chunk", topic=BLOB_TOPIC,
                    qos_profile=BLOB_PROFILE, **kw)


# -- lanes outside the scene (opened with topic=) -----------------------------

TOPIC_VPS_QUERY = "spatialdds/vps/query/v1"
TOPIC_VPS_RESULT = "spatialdds/vps/result/v1"
TOPIC_BOOTSTRAP_QUERY = "spatialdds/bootstrap/query/v1"
TOPIC_BOOTSTRAP_RESPONSE = "spatialdds/bootstrap/response/v1"


def reply_topic(asker_id: str) -> str:
    """Where an asker wants its coverage replies: a lane it names for itself."""
    return f"spatialdds/discovery/replies/{asker_id}/v1"


def anchors_delta(set_id: str) -> str:
    """An anchor set's delta lane. The anchor set, not a scene, is its domain."""
    return f"spatialdds/anchors/{set_id}/delta/v1"


# -- the web bridge's provenance labels ----------------------------------------
# Where a topic name the bridge reports came from. Labels, not lanes.

TOPIC_SOURCE_SPEC = "spec"
TOPIC_SOURCE_ANNOUNCE = "announce"
TOPIC_SOURCE_MANIFEST = "manifest"
TOPIC_SOURCE_REQUEST = "request"
TOPIC_SOURCE_ANNOUNCE_PREVIEW = "announce_preview"
TOPIC_SOURCE_FALLBACK = "fallback"
TOPIC_SOURCE_RUNTIME_CUSTOM = "runtime_custom"
