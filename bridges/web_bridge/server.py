#!/usr/bin/env python3
"""SpatialDDS HTTP/WebSocket bridge.

Two generations of endpoints live in this server:

  Legacy (kept for the Cesium web demo)
  ─────────────────────────────────────
    GET  /health                   — service announcement + DDS domain
    POST /v1/localize              — Phase 3 localize one-shot
    POST /v1/catalog/query         — Phase 4 catalog one-shot
    WS   /v1/stream                — every received envelope, no filtering

  Generic protocol (new, for arbitrary browser consumers)
  ────────────────────────────────────────────────────────
    WS   /ws                       — subscribe/publish/list_topics/ping
    GET  /api/topics               — REST topic discovery
    GET  /api/stats                — bridge-level counters

The generic side reuses the existing DDS transport: each envelope received
on the bus is fanned out to BOTH the legacy broadcaster (raw, all clients)
and the new ClientManager (per-client topic-pattern + msg_type filtering,
optional rate limiting). Browser-to-DDS publishing goes through the shared
``bridges/envelope_io.EnvelopePublisher`` (RELIABLE+KEEP_ALL).
"""

import json
import os
import queue
import sys
import time
import uuid
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from spatialdds_demo.dds_transport import DDSTransport, require_dds_env
from spatialdds_demo.topics import (
    TOPIC_CATALOG_QUERY_V1,
    TOPIC_CATALOG_REPLIES,
    TOPIC_VPS_QUERY_V1,
)
from spatialdds_validation import SpatialDDSValidator, create_coverage_bbox_earth_fixed
from spatialdds_test import MockSensorData

# Sibling modules (router + client manager) and the shared envelope_io
_HERE = Path(__file__).resolve().parent
_BRIDGES = _HERE.parent
for p in (str(_HERE), str(_BRIDGES)):
    if p not in sys.path:
        sys.path.insert(0, p)
from topic_router import TopicRouter  # noqa: E402
from client_manager import ClientManager  # noqa: E402

DEFAULT_LAT = float(os.getenv("SPATIALDDS_BRIDGE_DEFAULT_LAT", "30.284996"))
DEFAULT_LON = float(os.getenv("SPATIALDDS_BRIDGE_DEFAULT_LON", "-97.739494"))
DEFAULT_ALT = float(os.getenv("SPATIALDDS_BRIDGE_DEFAULT_ALT", "18"))
ANNOUNCE_TTL_SEC = int(os.getenv("SPATIALDDS_BRIDGE_ANNOUNCE_TTL", "300"))
# Demo-local catalog filter default (the catalog protocol is not a spec
# surface). Replaces the old expr string now that CoverageQuery.expr is gone.
DEFAULT_CATALOG_KINDS = ["overlay", "poi", "mesh"]


class SpatialDDSBridge:
    def __init__(self) -> None:
        self._domain_id: Optional[int] = None
        self._transport: Optional[DDSTransport] = None
        self._announce_reader: Optional[object] = None
        self._inbox: queue.Queue = queue.Queue()
        self._last_announce: Optional[Dict[str, Any]] = None
        self._client_frame_ref = SpatialDDSValidator.create_frame_ref("client/handset")
        self._stream_ref = SpatialDDSValidator.create_frame_ref("rig/front_cam")
        self._frame_seq = 1
        self._request_lock = queue.Queue(maxsize=1)
        self._request_lock.put(True)
    def ensure_transport(self) -> int:
        if self._domain_id is not None:
            return self._domain_id
        domain_id = _env_domain_id()
        if domain_id is None:
            raise RuntimeError("SPATIALDDS_DDS_DOMAIN is required")
        self._domain_id = domain_id
        self._start_transport(domain_id)
        return domain_id

    def _start_transport(self, domain_id: int) -> None:
        # The legacy DDSTransport's on_message has two jobs:
        #   1. feed the request-response correlator (self._inbox) used by
        #      /v1/localize and /v1/catalog/query
        #   2. broadcast envelope events to /v1/stream and /ws
        #
        # Job (1) only cares about specific reply messages; QoS doesn't
        # matter much because each request/response pair is a single
        # message. Job (2) cares deeply about QoS — the default
        # BEST_EFFORT + KEEP_LAST(1) reader silently drops samples when
        # the publisher emits multiple writes within one ~10ms poll
        # interval, which collapses the per-operator rates the dashboard
        # shows. We split the two: DDSTransport handles job (1); a
        # separate RELIABLE+KEEP_ALL EnvelopeSubscriber (the same one
        # the MCAP / ROS 2 / MQTT bridges use) handles job (2).
        def on_message(envelope: object) -> None:
            self._inbox.put(envelope)

        self._transport = DDSTransport(
            on_message_callback=on_message,
            domain_id=domain_id,
            local_sender_id=self._client_frame_ref["fqn"],
        )
        self._transport.start()
        self._announce_reader = self._transport.create_announce_reader(ANNOUNCE_TTL_SEC)

    def _announce_fresh(self, announce: Dict[str, Any]) -> bool:
        ttl_sec = announce.get("ttl_sec")
        stamp = announce.get("stamp")
        if not ttl_sec or not stamp:
            return True
        try:
            stamp_time = float(stamp.get("sec", 0)) + float(stamp.get("nanosec", 0)) / 1_000_000_000.0
        except (TypeError, ValueError):
            return True
        return (time.time() - stamp_time) <= float(ttl_sec) * 2.0

    def latest_announce(self, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        if self._last_announce and self._announce_fresh(self._last_announce):
            return self._last_announce
        if not self._announce_reader:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            samples = self._announce_reader.take()
            if samples:
                for sample in samples:
                    if not sample or sample.msg_type != "ANNOUNCE":
                        continue
                    candidate = json.loads(sample.payload_json)
                    if self._announce_fresh(candidate):
                        self._last_announce = candidate
                        return candidate
            time.sleep(0.05)
        return self._last_announce

    def localize(
        self,
        prior_geopose: Optional[Dict[str, Any]] = None,
        service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        _lock(self._request_lock)
        try:
            self.ensure_transport()
            if not service_id:
                announce = self.latest_announce(timeout=6.0)
                if not announce:
                    raise RuntimeError("No ANNOUNCE received")
                service_id = announce.get("service_id", "")
            if not self._transport:
                raise RuntimeError("DDS transport not initialized")

            request = self._create_localize_request(service_id, prior_geopose)
            self._transport.publish(
                TOPIC_VPS_QUERY_V1,
                "LOCALIZE_REQUEST",
                json.dumps(request),
                request.get("request_id", ""),
            )
            _emit_dds_event(
                {
                    "ts": time.time(),
                    "dir": "tx",
                    "domain": self._domain_id,
                    "msg_type": "LOCALIZE_REQUEST",
                    "logical_topic": TOPIC_VPS_QUERY_V1,
                    "request_id": request.get("request_id", ""),
                    "payload": request,
                }
            )

            env = _wait_for(self._inbox, "LOCALIZE_RESPONSE", timeout=8)
            if not env:
                raise RuntimeError("LOCALIZE_RESPONSE timeout")
            return json.loads(env.payload_json)
        finally:
            _unlock(self._request_lock)

    def catalog_query(
        self,
        geopose: Dict[str, Any],
        kind_in: Optional[List[str]] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        _lock(self._request_lock)
        try:
            self.ensure_transport()
            if not self._transport:
                raise RuntimeError("DDS transport not initialized")

            client_id = f"bridge-{uuid.uuid4().hex[:6]}"
            reply_topic = TOPIC_CATALOG_REPLIES(client_id)
            query = self._create_catalog_query(
                geopose, reply_topic, limit=limit, kind_in=kind_in
            )
            self._transport.publish(
                TOPIC_CATALOG_QUERY_V1,
                "CATALOG_QUERY",
                json.dumps(query),
                query.get("query_id", ""),
            )
            _emit_dds_event(
                {
                    "ts": time.time(),
                    "dir": "tx",
                    "domain": self._domain_id,
                    "msg_type": "CATALOG_QUERY",
                    "logical_topic": TOPIC_CATALOG_QUERY_V1,
                    "request_id": query.get("query_id", ""),
                    "payload": query,
                }
            )

            env = _wait_for(self._inbox, "CATALOG_RESPONSE", timeout=6)
            if not env:
                raise RuntimeError("CATALOG_RESPONSE timeout")
            return json.loads(env.payload_json)
        finally:
            _unlock(self._request_lock)

    def _create_localize_request(
        self, service_id: str, prior_geopose: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        request_id = str(uuid.uuid4())
        prior = prior_geopose or _default_prior_geopose()
        request = {
            "request_id": request_id,
            "client_frame_ref": self._client_frame_ref,
            "service_id": service_id,
            "prior_geopose": prior,
            "vision_frame": self._vision_frame(),
            "stamp": SpatialDDSValidator.now_time(),
            "quality_requirements": {"max_rmse_m": 0.2, "min_confidence": 0.6},
        }
        self._frame_seq += 1
        return request

    def _vision_frame(self) -> Dict[str, Any]:
        stamp = SpatialDDSValidator.now_time()
        payload = f"MOCK_IMAGE_{self._frame_seq}".encode("utf-8")
        blob = MockSensorData.blob_ref("image/jpeg", payload)
        hdr = {
            "stream_id": self._stream_ref["fqn"],
            "frame_seq": self._frame_seq,
            "t_start": stamp,
            "t_end": stamp,
            "has_sensor_pose": True,
            "sensor_pose": {"t": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]},
            "blobs": [blob],
        }
        return {
            "stream_id": self._stream_ref["fqn"],
            "frame_seq": self._frame_seq,
            "hdr": hdr,
            "codec": "JPEG",
            "pix": "RGB8",
            "color": "SRGB",
            "has_line_readout_us": False,
            "rectified": True,
            "quality": {
                "has_snr_db": True,
                "snr_db": 28.0,
                "percent_valid": 99.0,
                "health": "OK",
                "note": "synthetic frame",
            },
        }

    def _create_catalog_query(
        self,
        geopose: Dict[str, Any],
        reply_topic: str,
        limit: int = 20,
        page_token: str = "",
        kind_in: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Build a demo-local catalog query.

        The catalog protocol is demo-specific, not a spec surface, but it used
        to carry an `expr` string mirroring the (now deleted)
        CoverageQuery.expr. It now carries a structured filter in the same
        has_filter + `*_in` style discovery uses.
        """
        query_id = str(uuid.uuid4())
        padding = 0.005
        coverage_frame_ref, coverage_elem = create_coverage_bbox_earth_fixed(
            geopose.get("lon_deg", DEFAULT_LON) - padding,
            geopose.get("lat_deg", DEFAULT_LAT) - padding,
            geopose.get("lon_deg", DEFAULT_LON) + padding,
            geopose.get("lat_deg", DEFAULT_LAT) + padding,
        )
        kinds = list(kind_in if kind_in is not None else DEFAULT_CATALOG_KINDS)
        return {
            "query_id": query_id,
            "reply_topic": reply_topic,
            "coverage": [coverage_elem],
            "coverage_frame_ref": coverage_frame_ref,
            "has_coverage_eval_time": False,
            "has_filter": bool(kinds),
            "filter": {"kind_in": kinds},
            "limit": limit,
            "page_token": page_token,
            "stamp": SpatialDDSValidator.now_time(),
            "ttl_sec": 30,
        }


def _default_prior_geopose() -> Dict[str, Any]:
    stamp = SpatialDDSValidator.now_time()
    return {
        "lat_deg": DEFAULT_LAT,
        "lon_deg": DEFAULT_LON,
        "alt_m": DEFAULT_ALT,
        "q": [0.4967, -0.0336, -0.0585, 0.8653],
        # 1.7 removed GeoPose.frame_kind / frame_ref: orientation is fixed to
        # the local ENU tangent frame at the encoded position.
        "stamp": stamp,
        "cov": "COV_NONE",
    }


def _env_domain_id() -> Optional[int]:
    value = os.getenv("SPATIALDDS_DDS_DOMAIN", "")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_json(payload_json: str) -> Any:
    try:
        return json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return payload_json


class DDSEventBroadcaster:
    def __init__(self) -> None:
        self._queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self._clients: set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit(self, event: Dict[str, Any]) -> None:
        if not self._loop:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def run(self) -> None:
        while True:
            event = await self._queue.get()
            dead: list[WebSocket] = []
            for ws in self._clients:
                try:
                    await ws.send_json(event)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)


def _emit_dds_event(event: Dict[str, Any]) -> None:
    broadcaster.emit(event)


def _wait_for(queue_obj: queue.Queue, msg_type: str, timeout: float) -> Optional[object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        try:
            envelope = queue_obj.get(timeout=remaining)
        except queue.Empty:
            continue
        if envelope.msg_type == msg_type:
            return envelope
    return None


def _lock(lock_queue: queue.Queue) -> None:
    lock_queue.get()


def _unlock(lock_queue: queue.Queue) -> None:
    lock_queue.put(True)


app = FastAPI(title="SpatialDDS Bridge", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bridge = SpatialDDSBridge()
broadcaster = DDSEventBroadcaster()
client_mgr = ClientManager(TopicRouter())
_event_loop: Optional[asyncio.AbstractEventLoop] = None
_publisher = None  # bridges/envelope_io.EnvelopePublisher (lazy)
_envelope_sub = None  # bridges/envelope_io.EnvelopeSubscriber (lossless reader)
_total_dispatched = 0
_start_time_ns = time.time_ns()
ALLOW_PUBLISH = os.getenv("SPATIALDDS_BRIDGE_ALLOW_PUBLISH", "1") not in ("0", "false", "no")
STATIC_DIR = os.getenv("SPATIALDDS_BRIDGE_STATIC_DIR", str(_HERE / "static"))


def _dispatch_to_clients(envelope) -> None:
    """Schedule ClientManager.dispatch on the event loop. Called from the
    sync DDS poll thread (DDSTransport's on_message), so we hop threads via
    ``call_soon_threadsafe``."""
    global _event_loop, _total_dispatched
    if _event_loop is None:
        return
    try:
        payload = json.loads(envelope.payload_json or "{}")
    except (json.JSONDecodeError, AttributeError):
        return
    msg_type = getattr(envelope, "msg_type", "") or ""
    topic = getattr(envelope, "logical_topic", "") or ""
    stamp_ns = int(getattr(envelope, "stamp_ns", time.time_ns()) or 0)

    async def _go():
        global _total_dispatched
        sent = await client_mgr.dispatch(topic, msg_type, payload, stamp_ns)
        _total_dispatched += sent

    _event_loop.call_soon_threadsafe(asyncio.ensure_future, _go())


# Hook the new dispatcher into the same global ``_emit_dds_event`` the
# legacy broadcaster already uses. The lossless envelope subscriber set
# up in ``on_startup`` calls this for every received envelope; we fan
# out to BOTH the legacy /v1/stream broadcaster AND the per-client
# ``ClientManager.dispatch`` for /ws subscribers.
_legacy_emit = _emit_dds_event


def _emit_dds_event(event: Dict[str, Any]) -> None:  # noqa: F811 — intentional override
    _legacy_emit(event)
    msg_type = event.get("msg_type") or ""
    topic = event.get("logical_topic") or ""
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"_raw": payload}
    payload = payload if isinstance(payload, dict) else {}
    if _event_loop is None or not topic:
        return
    stamp_ns = int(event.get("ts", time.time()) * 1_000_000_000)

    async def _go():
        global _total_dispatched
        sent = await client_mgr.dispatch(topic, msg_type, payload, stamp_ns)
        _total_dispatched += sent

    _event_loop.call_soon_threadsafe(asyncio.ensure_future, _go())


def _envelope_callback(msg_type: str, logical_topic: str,
                        payload: Dict[str, Any], stamp_ns: int) -> None:
    """RELIABLE+KEEP_ALL ``EnvelopeSubscriber`` callback. Synthesizes the
    same dict-shaped event the legacy ``DDSTransport.on_message`` used to
    emit so /v1/stream and /ws downstream keep working unchanged.
    """
    _emit_dds_event({
        "ts": stamp_ns / 1_000_000_000.0 if stamp_ns else time.time(),
        "dir": "rx",
        "domain": bridge._domain_id,
        "msg_type": msg_type,
        "logical_topic": logical_topic,
        "request_id": "",
        "payload": payload,
    })


def _ensure_publisher():
    """Lazily build the RELIABLE+KEEP_ALL writer for browser→DDS publishing."""
    global _publisher
    if _publisher is not None:
        return _publisher
    if _event_loop is None:
        return None
    domain_id = bridge.ensure_transport()
    from envelope_io import EnvelopePublisher  # type: ignore
    _publisher = EnvelopePublisher(domain_id)
    return _publisher


@app.on_event("startup")
def on_startup() -> None:
    global _event_loop, _envelope_sub
    require_dds_env()
    domain_id = _env_domain_id()
    if domain_id is None:
        raise RuntimeError("SPATIALDDS_DDS_DOMAIN is required")
    bridge._domain_id = domain_id
    bridge._start_transport(domain_id)
    loop = asyncio.get_event_loop()
    _event_loop = loop
    broadcaster.attach_loop(loop)
    loop.create_task(broadcaster.run())

    # Lossless envelope reader — feeds /v1/stream + /ws via _emit_dds_event.
    # DDSTransport already handles legacy localize/catalog request-response;
    # this is purely the streaming-side fanout, sized to never drop a
    # burst when a publisher emits multiple operators back-to-back.
    from envelope_io import EnvelopeSubscriber  # type: ignore
    _envelope_sub = EnvelopeSubscriber(domain_id, _envelope_callback)
    _envelope_sub.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    global _envelope_sub
    if _envelope_sub is not None:
        try:
            _envelope_sub.stop()
        except Exception:
            pass
        _envelope_sub = None


@app.get("/health")
def health() -> Dict[str, Any]:
    domain_id = bridge.ensure_transport()
    announce = bridge.latest_announce(timeout=1.0)
    return {
        "status": "ok",
        "dds_domain": domain_id,
        "announce": announce,
    }


@app.post("/v1/localize")
def localize(payload: Dict[str, Any]) -> Dict[str, Any]:
    prior = payload.get("prior_geopose") if isinstance(payload, dict) else None
    service_id = payload.get("service_id") if isinstance(payload, dict) else None
    try:
        return bridge.localize(prior, service_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/v1/catalog/query")
def catalog_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    geopose = payload.get("geopose") if isinstance(payload, dict) else None
    if not geopose:
        raise HTTPException(status_code=400, detail="geopose required")
    if "expr" in payload:
        raise HTTPException(
            status_code=400,
            detail=(
                "'expr' was removed in SpatialDDS 1.7; pass 'kind_in': "
                "[\"overlay\", \"poi\", \"mesh\"] instead"
            ),
        )
    kind_in = payload.get("kind_in")
    limit = int(payload.get("limit", 20) or 20)
    try:
        return bridge.catalog_query(geopose, kind_in=kind_in, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.websocket("/v1/stream")
async def stream(websocket: WebSocket) -> None:
    await broadcaster.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.disconnect(websocket)


# ─── Generic protocol (new) ──────────────────────────────────────────────────


@app.get("/api/topics")
def api_topics(stale_threshold_s: float = 30.0) -> Dict[str, Any]:
    """Topics seen on the bus within ``stale_threshold_s`` seconds."""
    topics = client_mgr.router.get_topics(stale_threshold_s=stale_threshold_s)
    return {
        "topics": [
            {
                "logical_topic": t.logical_topic,
                "msg_type": t.msg_type,
                "last_seen_ns": t.last_seen_ns,
                "rate_hz": round(t.rate_hz, 2),
                "message_count": t.message_count,
            }
            for t in topics
        ],
    }


@app.get("/api/stats")
def api_stats() -> Dict[str, Any]:
    return {
        "uptime_s": round((time.time_ns() - _start_time_ns) / 1e9, 1),
        "clients_connected": client_mgr.client_count,
        "total_dispatched": _total_dispatched,
        "topics_active": len(client_mgr.router.get_topics()),
        "dds_domain": bridge._domain_id,
        "publish_enabled": ALLOW_PUBLISH,
    }


@app.websocket("/ws")
async def ws_generic(websocket: WebSocket) -> None:
    """Subscribe-based WebSocket protocol. See README for the message schema."""
    await websocket.accept()

    async def send_json(payload: dict) -> None:
        await websocket.send_json(payload)

    session = client_mgr.add_client(send_json)
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError("expected JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                await send_json({"type": "error", "message": f"Invalid JSON: {exc}"})
                continue

            mtype = str(msg.get("type") or "").lower()
            if mtype == "subscribe":
                resp = client_mgr.handle_subscribe(session, msg)
                await send_json(resp)
            elif mtype == "unsubscribe":
                resp = client_mgr.handle_unsubscribe(session, msg)
                await send_json(resp)
            elif mtype == "ping":
                await send_json({
                    "type": "pong",
                    "server_time_ns": time.time_ns(),
                    "clients_connected": client_mgr.client_count,
                    "messages_dispatched": _total_dispatched,
                })
            elif mtype == "list_topics":
                topics = client_mgr.router.get_topics()
                await send_json({
                    "type": "topics",
                    "topics": [
                        {
                            "logical_topic": t.logical_topic,
                            "msg_type": t.msg_type,
                            "last_seen_ns": t.last_seen_ns,
                            "rate_hz": round(t.rate_hz, 2),
                            "message_count": t.message_count,
                        }
                        for t in topics
                    ],
                })
            elif mtype == "publish":
                if not ALLOW_PUBLISH:
                    await send_json({
                        "type": "error",
                        "ref_id": msg.get("id", ""),
                        "message": "Publishing disabled (SPATIALDDS_BRIDGE_ALLOW_PUBLISH=0)",
                    })
                    continue
                logical_topic = str(msg.get("logical_topic") or "")
                msg_type_str = str(msg.get("msg_type") or "")
                payload = msg.get("payload") or {}
                if not logical_topic or not msg_type_str:
                    await send_json({
                        "type": "error",
                        "ref_id": msg.get("id", ""),
                        "message": "publish requires logical_topic and msg_type",
                    })
                    continue
                pub = _ensure_publisher()
                if pub is None:
                    await send_json({
                        "type": "error",
                        "ref_id": msg.get("id", ""),
                        "message": "Publisher not initialized",
                    })
                    continue
                try:
                    pub.publish(logical_topic, msg_type_str, payload)
                    session.messages_received += 1
                    await send_json({
                        "type": "published",
                        "msg_type": msg_type_str,
                        "logical_topic": logical_topic,
                        "status": "ok",
                    })
                except Exception as exc:
                    await send_json({
                        "type": "error",
                        "ref_id": msg.get("id", ""),
                        "message": f"publish failed: {exc}",
                    })
            else:
                await send_json({
                    "type": "error",
                    "message": f"Unknown message type: {mtype!r}",
                })
    finally:
        client_mgr.remove_client(session.client_id)


# Mount static dashboard if a directory exists.
if Path(STATIC_DIR).is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    _static_dir = Path(STATIC_DIR)

    # Landing page: 2D canvas top-down intersection viz. The old
    # topic-list debug page lives at /debug.
    @app.get("/")
    def _root_index():
        return FileResponse(_static_dir / "index.html")

    @app.get("/debug")
    def _debug_index():
        return FileResponse(_static_dir / "debug.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8088, reload=False)
