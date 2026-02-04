#!/usr/bin/env python3
"""SpatialDDS HTTP-to-DDS bridge for the web demo."""

import json
import os
import queue
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from spatialdds_demo.dds_transport import DDSTransport, require_dds_env
from spatialdds_demo.topics import (
    TOPIC_CATALOG_QUERY_V1,
    TOPIC_CATALOG_REPLIES,
    TOPIC_VPS_LOCALIZE_REQUEST_V1,
)
from spatialdds_validation import SpatialDDSValidator, create_coverage_bbox_earth_fixed
from spatialdds_test import MockSensorData

DEFAULT_LAT = float(os.getenv("SPATIALDDS_BRIDGE_DEFAULT_LAT", "30.284996"))
DEFAULT_LON = float(os.getenv("SPATIALDDS_BRIDGE_DEFAULT_LON", "-97.739494"))
DEFAULT_ALT = float(os.getenv("SPATIALDDS_BRIDGE_DEFAULT_ALT", "18"))
ANNOUNCE_TTL_SEC = int(os.getenv("SPATIALDDS_BRIDGE_ANNOUNCE_TTL", "300"))


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
        return (time.time() - stamp_time) <= float(ttl_sec)

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
                TOPIC_VPS_LOCALIZE_REQUEST_V1,
                "LOCALIZE_REQUEST",
                json.dumps(request),
                request.get("request_id", ""),
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
        expr: str = "kind==\"overlay\" OR kind==\"poi\" OR kind==\"mesh\"",
        limit: int = 20,
    ) -> Dict[str, Any]:
        _lock(self._request_lock)
        try:
            self.ensure_transport()
            if not self._transport:
                raise RuntimeError("DDS transport not initialized")

            client_id = f"bridge-{uuid.uuid4().hex[:6]}"
            reply_topic = TOPIC_CATALOG_REPLIES(client_id)
            query = self._create_catalog_query(geopose, reply_topic, limit=limit, expr=expr)
            self._transport.publish(
                TOPIC_CATALOG_QUERY_V1,
                "CATALOG_QUERY",
                json.dumps(query),
                query.get("query_id", ""),
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
            "sensor_pose": {"t": [0.0, 0.0, 0.0], "q_xyzw": [0.0, 0.0, 0.0, 1.0]},
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
        expr: str = "",
    ) -> Dict[str, Any]:
        query_id = str(uuid.uuid4())
        padding = 0.005
        coverage_frame_ref, coverage_elem = create_coverage_bbox_earth_fixed(
            geopose.get("lon_deg", DEFAULT_LON) - padding,
            geopose.get("lat_deg", DEFAULT_LAT) - padding,
            geopose.get("lon_deg", DEFAULT_LON) + padding,
            geopose.get("lat_deg", DEFAULT_LAT) + padding,
        )
        return {
            "query_id": query_id,
            "reply_topic": reply_topic,
            "coverage": [coverage_elem],
            "coverage_frame_ref": coverage_frame_ref,
            "has_coverage_eval_time": False,
            "expr": expr,
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
        "q_xyzw": [0.4967, -0.0336, -0.0585, 0.8653],
        "frame_kind": "ENU",
        "frame_ref": SpatialDDSValidator.create_frame_ref("earth.enu"),
        "stamp": stamp,
        "cov": "COV_NONE",
    }


def _env_domain_id() -> Optional[int]:
    value = os.getenv("SPATIALDDS_DDS_DOMAIN", "")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


@app.on_event("startup")
def on_startup() -> None:
    require_dds_env()
    domain_id = _env_domain_id()
    if domain_id is None:
        raise RuntimeError("SPATIALDDS_DDS_DOMAIN is required")
    bridge._domain_id = domain_id
    bridge._start_transport(domain_id)


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
    expr = payload.get("expr", "kind==\"overlay\" OR kind==\"poi\" OR kind==\"mesh\"")
    limit = int(payload.get("limit", 20) or 20)
    try:
        return bridge.catalog_query(geopose, expr=expr, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8088, reload=False)
