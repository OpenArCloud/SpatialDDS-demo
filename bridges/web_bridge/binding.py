#!/usr/bin/env python3
"""The web bridge's 1.8 lane: the live DDS bus, served over the web binding.

    SPATIALDDS_TRANSPORT=dds CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
    SPATIALDDS_DDS_DOMAIN=1 python3 -m bridges.web_bridge.binding --port 8089

**This is not a second implementation of Appendix N.** The binding is
`spatialdds[server]` 0.5.0 from PyPI, embedded: it owns the routes, the
canonical JSON, the ETags, the access classes, the N.5 replay-then-live
discipline and the N.6 command surface. This module owns the DDS leg and
nothing else --- reading the bus, handing samples in, and turning a command
back into a `ModelCommand` on the wire.

That split is the whole design, and it is the reason this file is short. The
first attempt at this batch stopped twice rather than reimplement a piece of
the binding here: once because there was no N.6 surface to use at all, and
once because only half the live-backend contract was public. 0.4.0 and 0.5.0
closed both, and what is left is composition.

**The five seams, all public, all used as published:**

| seam | what it does here |
| --- | --- |
| `pin.model_from_records` | builds the wire model from *this repository's* `idl/v1.8`, because `spatial.owm` is provisional and the package rightly does not vendor it |
| `publish.apply_sample` / `apply_dispose` | what a new subscriber replays and what the collection GET returns |
| `gw_http.notify` | wakes the subscribers already connected --- the half that was missing before 0.5.0 |
| `commands.Registry` | the upstream lane: a POST becomes a verb this module publishes onto DDS |
| `make_app(publish_enabled=False, live_subscriptions=True)` | the live lane with no write surface, which is exactly what a backend that owns its own data wants |

**Why the samples go out and back through XCDR2.** A DDS sample arrives as a
CycloneDDS dataclass; the binding wants canonical JSON. Rather than project it
here --- which would be a second implementation of the lift, and the one thing
this module must not contain --- each sample is re-serialized by CycloneDDS
and read back by the binding's own `xcdr2.Decoder` and `webjson.Projector`.
Two implementations of XCDR2 therefore have to agree about every member of
every record on every sample, which is the property that found a dead base64
rule in the mapping table once already. It costs a serialize and a parse per
sample at duck-mover rates, and buys an agreement check nobody has to
remember to run.

**What this module does not do.** It does not serve the AR demo's seven
routes --- localize, catalogue, frames, keepout, health, discovery search ---
which have no Appendix N equivalent because N has no RPC surface. Those stay
on `server.py`'s own surface, and the bridge census records why
(`docs/bridge-1.8-census.md`, finding 1). This is the OWM lane only.
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from aiohttp import web                                        # noqa: E402

from spatialdds.server import (                                # noqa: E402
    commands, gateway, gw_http, pin, publish, webjson, xcdr2)

from spatialdds_demo import typed_transport as tt              # noqa: E402
from spatialdds_demo.dds_transport import require_dds_env      # noqa: E402
from spatialdds_demo.qos_profiles import (                     # noqa: E402
    MODEL_COMMAND, MODEL_FAST, MODEL_LATCHED)
from spatialdds_demo.topics import (                           # noqa: E402
    SCENE, TOPIC_MODEL_COMMAND_V1, TOPIC_MODEL_ENTITY_V1,
    TOPIC_MODEL_POSE_V1)
from spatialdds_idl18.spatial.owm import (                      # noqa: E402
    Entity, ModelCommand, ModelPose)

IDL_DIR = REPO / "idl" / "v1.8"
SPEC_COMMIT = "9c3e1f2"

#: How often the pump says what it has moved. See `Pump.run`.
HEARTBEAT_S = 5.0

#: The closure each type needs, in dependency order. Same list the recorder
#: embeds in an MCAP, for the same reason: a reader parses one schema record
#: as a single translation unit.
CLOSURE = ["types.idl", "core.idl", "provisional/owm.idl"]

#: Topic, python type, QoS profile, and the type's FQN in both spellings.
LANES: list[tuple[str, Any, Any, str, str]] = [
    (TOPIC_MODEL_ENTITY_V1, Entity, MODEL_LATCHED,
     "spatial::owm::Entity", "spatial.owm.Entity"),
    (TOPIC_MODEL_POSE_V1, ModelPose, MODEL_FAST,
     "spatial::owm::ModelPose", "spatial.owm.ModelPose"),
    (TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND,
     "spatial::owm::ModelCommand", "spatial.owm.ModelCommand"),
]


def build_state() -> gateway.State:
    """A live registry: no recording, types from this repository's own pin.

    The package's vendored IDL carries `core`, `discovery` and `types` --- the
    three publishable types an empty registry has to know. `spatial.owm` is
    provisional and independently versioned, so the package does not vendor it
    and should not. This repository does, at `idl/v1.8`, from spec commit
    9c3e1f2, which is the same commit the package's own artifacts came from;
    a bridge whose types came from a different commit than the binding
    serving them would be two type systems in one process.
    """
    flat = pin.flatten(IDL_DIR, CLOSURE).encode()
    # One record per type, all with the same bytes, which is what the closure
    # being shared means. `model_from_records` keeps every record rather than
    # deduping, so each type is registered under its own name.
    model, idl = pin.model_from_records(
        [(colons, flat) for _t, _c, _q, colons, _fqn in LANES],
        whence=f"{IDL_DIR.relative_to(REPO)} (SpatialDDS-spec {SPEC_COMMIT})")
    st = gateway.State(
        source="live DDS",
        model=model,
        idl=idl,
        type_source=f"this repository's vendored IDL, {IDL_DIR.name} "
                    f"(SpatialDDS-spec {SPEC_COMMIT})")
    for topic, _cls, _qos, _colons, fqn in LANES:
        _scene, stream, type_seg, version = topic.split("/")[1:]
        latched, access = gateway.DURABILITY.get(
            fqn, (False, gateway.OPERATOR))
        agg = model.get(fqn)
        keys = [m.name for m in (agg.members if agg else []) if m.is_key]
        res = gateway.Resource(SCENE, stream, type_seg, version, topic, fqn,
                               keys[0] if keys else "", access, latched)
        st.resources[res.path] = res
        st.latched.setdefault(topic, {})
        st.live.setdefault(topic, [])
    st.scenes = sorted({r.scene for r in st.resources.values()})
    return st


class Pump:
    """The DDS leg: bus in, binding out.

    One thread, polling every lane. Threads rather than an asyncio DDS
    integration because CycloneDDS's Python reader is blocking and
    `gw_http.notify` is synchronous precisely so this is allowed to be a
    thread --- its docstring says so, which is the seam being used as
    designed rather than worked around.
    """

    def __init__(self, st: gateway.State, app: web.Application,
                 domain_id: int) -> None:
        from cyclonedds.domain import DomainParticipant

        self.st = st
        self.app = app
        self.participant = DomainParticipant(domain_id)
        self.dec = xcdr2.Decoder(st.model)
        self.proj = webjson.Projector(st.model, webjson.tables())
        self.readers = [
            (topic, tt.make_reader(self.participant, topic, cls, qos.name),
             fqn)
            for topic, cls, qos, _colons, fqn in LANES]
        #: instance handle -> key, per topic. A dispose arrives carrying only
        #: a handle, so the only way to name what went is to have seen it
        #: alive. Same rule the recorder follows, and for the same reason.
        self.keys: dict[tuple[str, int], str] = {}
        self.counts: dict[str, int] = {t: 0 for t, *_ in LANES}
        self.removals = 0
        self._stop = threading.Event()

    def _project(self, fqn: str, sample: Any) -> dict:
        """A CycloneDDS sample as the binding's canonical JSON.

        Out through CycloneDDS's serializer and back through the binding's
        own reader, so the two implementations have to agree. See the module
        docstring; this is the one line that buys the agreement check.
        """
        return self.proj.project(fqn, self.dec.decode(fqn, sample.serialize()))

    def poll_once(self) -> int:
        moved = 0
        for topic, reader, fqn in self.readers:
            res = self.st.resources[f"/{SCENE}/" + "/".join(topic.split("/")[2:])]
            for s in tt.take_with_state(reader) or []:
                if s.data is None:
                    # A removal, and only on a latched lane: an instance
                    # ending on a VOLATILE lane had no late-joiner state to
                    # stop being the answer for, and a writer's exit disposes
                    # its instances by default. Same rule as the recorder.
                    if not (s.was_disposed and res.latched):
                        continue
                    key = self.keys.get((topic, s.instance_handle))
                    if key is None:
                        continue              # never seen alive: unnameable
                    publish.apply_dispose(self.st, topic, key)
                    gw_http.notify(self.app, topic,
                                   {"event": "dispose", "topic": topic,
                                    "key": key, "reason": "disposed on the bus"})
                    self.removals += 1
                    moved += 1
                    continue
                doc = self._project(fqn, s.data)
                key = str(doc.get(res.key, "")) if res.key else ""
                if s.instance_handle is not None and key:
                    self.keys[(topic, s.instance_handle)] = key
                if res.latched:
                    publish.apply_sample(self.st, topic, res, key, doc)
                else:
                    self.st.counts[topic] = self.st.counts.get(topic, 0) + 1
                gw_http.notify(self.app, topic,
                               {"event": "sample", "topic": topic,
                                "key": key, "data": doc})
                self.counts[topic] += 1
                moved += 1
        return moved

    def run(self) -> None:
        """Poll the bus, and say so periodically.

        The heartbeat is not decoration. A bridge whose pump is alive but
        delivering nothing looks identical from outside to one that is
        working on a quiet venue: the collection GET answers, the
        subscription stays open, and every HTTP check passes. The counts are
        the one thing that distinguishes "nothing is happening" from
        "nothing is getting through", and this batch spent an hour on the
        wrong side of that ambiguity for want of them.
        """
        last = time.time()
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:                        # pragma: no cover
                print(f"bridge: pump error {type(e).__name__}: {e}",
                      flush=True)
            now = time.time()
            if now - last >= HEARTBEAT_S:
                last = now
                per = "  ".join(
                    f"{t.rsplit('/', 2)[-2]}={n}"
                    for t, n in self.counts.items())
                print(f"bridge: pumped {sum(self.counts.values())} "
                      f"({per})  removals={self.removals}  "
                      f"subscribers={sum(len(q) for q in self.app['subs'].values())}",
                      flush=True)
            time.sleep(0.02)

    def stop(self) -> None:
        self._stop.set()


class CommandLane:
    """The upstream lane: an N.6 POST becomes a `ModelCommand` on the bus.

    The binding has already validated the body against the published schema
    and lifted it to canonical JSON by the time this sees it, so what is left
    is the one thing this module exists for: writing it to DDS.

    It declines rather than accepts when it cannot write, because M.1 makes a
    decline a first-class observable outcome and a bridge that swallowed a
    command would be the absence M.1 forbids.
    """

    def __init__(self, participant) -> None:
        self.writer = tt.make_writer(participant, TOPIC_MODEL_COMMAND_V1,
                                     ModelCommand, MODEL_COMMAND.name)
        self.sent = 0

    def handle(self, record: dict) -> commands.Outcome:
        from spatialdds_idl18.builtin import Time
        from spatialdds_idl18.spatial.core import Aabb3, PoseSE3
        try:
            pose = record.get("pose") or {}
            extent = record.get("extent") or {}
            stamp = record.get("stamp") or {}
            cmd = ModelCommand(
                command_id=record["command_id"],
                verb=record["verb"],
                subject_id=record.get("subject_id", ""),
                reason=record.get("reason", ""),
                requester_id=record.get("requester_id", ""),
                has_pose=bool(record.get("has_pose")),
                pose=PoseSE3(t=[float(v) for v in pose.get("t", [0, 0, 0])],
                             q=[float(v) for v in pose.get("q", [0, 0, 0, 1])]),
                has_extent=bool(record.get("has_extent")),
                extent=Aabb3(
                    min_xyz=[float(v) for v in extent.get("min_xyz", [0, 0, 0])],
                    max_xyz=[float(v) for v in extent.get("max_xyz", [0, 0, 0])]),
                # §N.1: an int64 is a decimal string on the wire, so `sec`
                # arrives as a string and has to go back to an integer here.
                stamp=Time(sec=int(stamp.get("sec", 0)),
                           nanosec=int(stamp.get("nanosec", 0))),
            )
        except (KeyError, TypeError, ValueError) as e:
            return commands.Outcome.decline(
                f"the command could not be built for the bus: "
                f"{type(e).__name__}: {e}")
        try:
            self.writer.write(cmd)
        except Exception as e:                            # pragma: no cover
            return commands.Outcome.decline(
                f"the bus would not take it: {type(e).__name__}: {e}")
        self.sent += 1
        return commands.Outcome.accept(
            f"published {cmd.verb!r} for {cmd.subject_id!r} on "
            f"{TOPIC_MODEL_COMMAND_V1}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="the OWM lane of the web bridge, over the 1.8 web binding")
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--domain", type=int, default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--public-url", default=None)
    args = ap.parse_args()

    import secrets
    sys.stdout.reconfigure(line_buffering=True)
    domain_id = require_dds_env() if args.domain is None else args.domain
    token = args.token or secrets.token_urlsafe(24)
    base = args.public_url or f"http://{args.host}:{args.port}"

    st = build_state()
    registry = commands.Registry()
    # publish_enabled=False: this bridge owns its data and wants no PUT
    # surface. live_subscriptions=True: subscriptions stay open for `notify`.
    # Both were one flag before 0.5.0, and this is the combination that was
    # unreachable then.
    app = gw_http.make_app(st, base, token, publish_enabled=False,
                           live_subscriptions=True,
                           command_registry=registry)
    pump = Pump(st, app, domain_id)
    lane = CommandLane(pump.participant)
    registry.register("spatial.owm.ModelCommand", lane.handle)

    print(f"bridge: domain {domain_id}, scene {SCENE}")
    print(f"bridge: binding  spatialdds[server] "
          f"{__import__('spatialdds').__version__} from PyPI, embedded")
    print(f"bridge: types    {st.type_source}")
    for topic, _c, _q, _colons, fqn in LANES:
        latched, access = gateway.DURABILITY.get(fqn,
                                                 (False, gateway.OPERATOR))
        print(f"bridge: lane     {topic}  "
              f"{'TRANSIENT_LOCAL' if latched else 'VOLATILE':16} {access}")
    print(f"bridge: commands {sorted(registry.types())}")
    print(f"bridge: serving  {base}")
    print(f"bridge: token    {token}")
    print("bridge: write surface OFF; subscriptions stay open for notify")

    thread = threading.Thread(target=pump.run, daemon=True)
    thread.start()

    def _stop(_signum, _frame):
        pump.stop()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        web.run_app(app, host=args.host, port=args.port, print=None)
    except KeyboardInterrupt:
        pass
    finally:
        pump.stop()
        print(f"bridge: pumped {sum(pump.counts.values())} sample(s), "
              f"{pump.removals} removal(s); sent {lane.sent} command(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
