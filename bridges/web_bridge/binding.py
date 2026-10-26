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
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from aiohttp import web                                        # noqa: E402

from spatialdds.server import (                                # noqa: E402
    commands, gateway, gw_http, pin, publish, webjson, xcdr2)

import spatialdds as sd                                        # noqa: E402

from spatialdds_demo.lanes import (                            # noqa: E402
    MODEL_COMMAND, MODEL_FAST, MODEL_LATCHED, RELATIONSHIP,
    RELATIONSHIP_EXTENSION, RELATIONSHIP_STREAM, SCENE)
from spatialdds.dds.types.spatial.core import FrameTransform        # noqa: E402

#: Who this bridge is when it writes: the command lane is its one writer.
SOURCE_ID = "svc:bridge:demo/owm-binding"

#: How often the pump says what it has moved. See `Pump.run`.
HEARTBEAT_S = 5.0

#: What this venue's model lanes describe, and therefore how they are served.
#:
#: **The endpoint classifies, which is N.4's instruction and not a liberty
#: taken here:** "An endpoint SHOULD classify by what a resource describes
#: rather than by who asks: configured geometry --- zones, crossing lines,
#: frames, georeferences --- is ordinarily public, and observations of people
#: ordinarily are not."
#:
#: The package's `gateway.DURABILITY` defaults `spatial.owm.Entity` to
#: operator-scoped by *type*, which is the right default for a type that can
#: carry anything. What this venue's lanes actually carry is a fountain, a
#: pond, declared bounds and a frame --- configured geometry, which N.4 calls
#: ordinarily public, and which the same table already serves as public for
#: `SpatialZone`, `GeoAnchor` and `FrameTransform` --- plus a simulated robot
#: and three rubber ducks. Nothing here is an observation of a person.
#:
#: **The tension is real and is N.4's own.** A per-type access class cannot
#: say "this instance is configuration and that one is an observation", which
#: is exactly the zone-versus-detection case N.4 works through: a zone is
#: public and a detection inside it is not. This override is a judgement about
#: *this venue's* data, not a claim that `spatial.owm.Entity` is public in
#: general. A deployment whose model carried people would not get this
#: override, and the per-instance question stays open upstream.
#:
#: And it is what makes the browser lane possible at all. Operator-scoped
#: responses carry `Cache-Control: no-store, Vary: Authorization` and **no
#: `Access-Control-Allow-Origin`**, so a page on :5173 cannot read :8089 even
#: holding a valid token; and a browser cannot put a credential on a
#: WebSocket handshake, which N.4 requires and forbids every alternative to.
#: Both blockers are measured in `docs/browser-credential-finding.md`, which
#: routes them apart --- the handshake to the specification, the CORS half to
#: the gateway's own queue --- and stays filed because reclassification
#: dissolves the problem only where the data happens to be public.
#:
#: The cost, stated: public means a positive `max-age`, 300s from the
#: package's default, so a shared cache may hand a new client a venue up to
#: five minutes old. Bounded in practice --- the N.3 fetch is an opening
#: snapshot and the N.5 replay that follows comes from live state, not the
#: cache, so the first live event corrects it.
ACCESS: dict[str, str] = {
    "spatial.owm.Entity": gateway.PUBLIC,
    "spatial.owm.ModelPose": gateway.PUBLIC,
    # The command lane stays operator-scoped. It is the write surface: a POST
    # here moves a robot, and "who may read the venue" is a different question
    # from "who may drive it". N.4's advice is about what a resource
    # describes, and what this one describes is an instruction.
    "spatial.owm.ModelCommand": gateway.OPERATOR,
    # The OWM 0.2 prototype's edges, public for the reason the entities are:
    # what they describe is a fountain, its parts, a simulated robot and
    # three rubber ducks, never a person. The judgement is this venue's, as
    # above. An edge about a person would be an observation of one, and a
    # deployment carrying those would not get this row.
    "oarc_owm.Relationship": gateway.PUBLIC,
}

#: Lanes the package's `gateway.DURABILITY` has no row for, by what their
#: writers do. The table knows the pinned types; an extension falls through
#: to "not latched", which for the relationship lane would serve no snapshot
#: to a late joiner while the bus hands it every edge. MODEL_LATCHED is what
#: every asserter opens it with (`spatialdds_demo/claims.py`).
LATCHED: dict[str, bool] = {
    "oarc_owm.Relationship": True,
}

#: Each lane by what a reader opens it with, its profile, and its type's FQN.
#: Topics come from the SDK's registry for SCENE.
LANES: list[tuple[dict, Any, str]] = [
    (dict(type_="model_entity"), MODEL_LATCHED, "spatial.owm.Entity"),
    (dict(type_="model_pose"), MODEL_FAST, "spatial.owm.ModelPose"),
    (dict(type_="model_command"), MODEL_COMMAND, "spatial.owm.ModelCommand"),
    # The scene's georeference. No ACCESS row below and none wanted: the
    # package already defaults `spatial.core.FrameTransform` to latched and
    # `public-cacheable`, which is right under N.4 --- a georeference is
    # configured geometry --- and it is also the only classification a
    # cross-origin page can read at all, since a credential would force a
    # preflight the gateway does not answer (blocker B). FrameTransform has
    # no §3.3.2 row at v1.8, so the lane is the deployment's own, under the
    # name model_service writes it on.
    (dict(type_=FrameTransform, type_name="frame_transform", stream="frames"),
     MODEL_LATCHED, "spatial.core.FrameTransform"),
    # The OWM 0.2 relationship prototype: a demo-owned extension, so its
    # schema comes from its registration rather than from the pin (below).
    (dict(type_=RELATIONSHIP, stream=RELATIONSHIP_STREAM),
     MODEL_LATCHED, "oarc_owm.Relationship"),
]


def schema_record(fqn: str) -> tuple[str, bytes]:
    """The IDL a lane's type is defined by: the pin's, or an extension's own.

    An extension registered with `sd.dds.extend()` carries the record a
    recording embeds for it, its includes' closure and then its own body,
    which is exactly what the gateway's model needs. The pin does not have
    it, and asked for it would refuse.
    """
    if fqn == RELATIONSHIP_EXTENSION.fqn:
        return fqn.replace(".", "::"), RELATIONSHIP_EXTENSION.record
    return pin.schema_record(fqn.replace(".", "::"))


def lane_topic(spec: dict) -> str:
    return sd.dds.topic_name(SCENE, spec.get("stream"),
                             spec.get("type_name") or spec["type_"])


def build_state() -> gateway.State:
    """A live registry: no recording, types from the SDK's own pin.

    One pin for the bus and the binding: the IDL the installed `spatialdds`
    ships, at SpatialDDS-spec's `v1.8` tag, `spatial.owm` included. Until the
    SDK adoption this was this repository's vendored `idl/v1.8` at 9c3e1f2,
    because the package's server half did not carry `spatial.owm`; a bridge
    whose types came from a different commit than the binding serving them
    would be two type systems in one process.
    """
    model, idl = pin.model_from_records(
        [schema_record(fqn) for _s, _q, fqn in LANES],
        whence=f"spatialdds {sd.__version__} (SpatialDDS-spec {sd.SPEC_TAG})")
    st = gateway.State(
        source="live DDS",
        model=model,
        idl=idl,
        type_source=f"the IDL spatialdds {sd.__version__} ships "
                    f"(SpatialDDS-spec {sd.SPEC_TAG}, {sd.SPEC_COMMIT})")
    for spec, _qos, fqn in LANES:
        topic = lane_topic(spec)
        _scene, stream, type_seg, version = topic.split("/")[1:]
        latched, _default_access = gateway.DURABILITY.get(
            fqn, (LATCHED.get(fqn, False), gateway.OPERATOR))
        # Durability from the package's table, access from this endpoint's
        # own classification. See ACCESS above for why the two come from
        # different places.
        access = ACCESS.get(fqn, _default_access)
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
                 domain_id: Optional[int]) -> None:
        self.st = st
        self.app = app
        self.participant = sd.participant(SCENE, domain=domain_id,
                                          service_id=SOURCE_ID)
        self.dec = xcdr2.Decoder(st.model)
        self.proj = webjson.Projector(st.model, webjson.tables())
        # The SDK's readers name the key of every removal, which is what this
        # pump used to keep a handle-to-key map for.
        self.readers = []
        for spec, qos, fqn in LANES:
            kw = {k: v for k, v in spec.items() if k != "type_"}
            self.readers.append((self.participant.reader(
                spec["type_"], qos_profile=qos, **kw), fqn))
        self.counts: dict[str, int] = {r.topic: 0 for r, _f in self.readers}
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
        for reader, fqn in self.readers:
            topic = reader.topic
            res = self.st.resources[f"/{SCENE}/" + "/".join(topic.split("/")[2:])]
            for ev in reader.take():
                if isinstance(ev, sd.Dispose):
                    # A removal, and only on a latched lane: an instance
                    # ending on a VOLATILE lane had no late-joiner state to
                    # stop being the answer for. Same rule as the recorder.
                    if not res.latched or not ev.key:
                        continue
                    publish.apply_dispose(self.st, topic, ev.key)
                    gw_http.notify(self.app, topic,
                                   {"event": "dispose", "topic": topic,
                                    "key": ev.key,
                                    "reason": ev.reason or "disposed on the bus"})
                    self.removals += 1
                    moved += 1
                    continue
                if not isinstance(ev, sd.Sample):
                    continue                  # liveliness, the replay marker
                doc = self._project(fqn, ev.data)
                key = str(doc.get(res.key, "")) if res.key else ""
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

    def __init__(self, participant: "sd.dds.Participant") -> None:
        self.writer = participant.writer("model_command",
                                         qos_profile=MODEL_COMMAND)
        self.sent = 0

    def handle(self, record: dict) -> commands.Outcome:
        # The record is already N.1, so the SDK writer takes it as it is: one
        # lift, the gateway's own, where a ModelCommand used to be rebuilt
        # here member by member.
        try:
            cmd = self.writer.write(record)
        except Exception as e:
            return commands.Outcome.decline(
                f"the command could not be put on the bus: "
                f"{type(e).__name__}: {e}")
        self.sent += 1
        return commands.Outcome.accept(
            f"published {cmd.verb!r} for {cmd.subject_id!r} on "
            f"{self.writer.topic}")


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
    pump = Pump(st, app, args.domain)
    lane = CommandLane(pump.participant)
    registry.register("spatial.owm.ModelCommand", lane.handle)

    print(f"bridge: domain {pump.participant.domain}, scene {SCENE}")
    print(f"bridge: binding  spatialdds[server] "
          f"{__import__('spatialdds').__version__} from PyPI, embedded")
    print(f"bridge: types    {st.type_source}")
    # Read off the resources actually built, not recomputed from the
    # package's table: the banner said operator-scoped for all three lanes
    # for as long as it consulted DURABILITY directly, while the endpoint was
    # serving two of them public. A banner that reports what it would have
    # done instead of what it did is worse than none.
    for res in sorted(st.resources.values(), key=lambda r: r.topic):
        print(f"bridge: lane     {res.topic}  "
              f"{'TRANSIENT_LOCAL' if res.latched else 'VOLATILE':16} "
              f"{res.access}")
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
        pump.participant.close(reason="the web bridge's binding stopped")
        print(f"bridge: pumped {sum(pump.counts.values())} sample(s), "
              f"{pump.removals} removal(s); sent {lane.sent} command(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
