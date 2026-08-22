#!/usr/bin/env python3
"""
SpatialDDS v1.7 HTTP Binding
Provides REST API endpoints for discovery-style registration and search using
discovery.Announce and discovery.CoverageQuery/Response shapes.

1.7 consolidated the well-known namespace to a single RFC 8615 registration:
/.well-known/spatialdds/{bootstrap,resolver,search}. `register` and `list` are
demo-local extensions that live alongside them.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, List, Any
import sys

from spatialdds_validation import (
    SpatialDDSValidator,
)

# Module-level registry (shared across all request handlers)
# Each entry: { "kind": "announce"|"manifest", "payload": dict,
#               "coverage": List[CoverageElement], "coverage_frame_ref": FrameRef }
_announce_registry: List[Dict[str, Any]] = []


def _now_ms() -> int:
    return int(time.time() * 1000)


def bootstrap_manifest() -> Dict[str, Any]:
    """
    Bootstrap manifest served at /.well-known/spatialdds/bootstrap (spec 5.x).

    Mirrors the site table spatialdds_bootstrap_server.py serves over the bus,
    so the HTTPS and DDS bootstrap paths agree. 1.7 replaced the `auth` object
    (with its method enum) with an optional `auth_hint` string; the demo omits
    it entirely rather than advertising a fake one.
    """
    site = os.getenv("SPATIALDDS_BOOTSTRAP_SITE", "sf-downtown")
    peers = [
        peer.strip()
        for peer in os.getenv("SPATIALDDS_BOOTSTRAP_PEERS", "udpv4://127.0.0.1:7400").split(",")
        if peer.strip()
    ]
    manifest: Dict[str, Any] = {
        "spatialdds_bootstrap": "1.7",
        "domain_id": int(os.getenv("SPATIALDDS_BOOTSTRAP_DOMAIN", "1")),
        "initial_peers": peers,
        "discovery_topic": "spatialdds/discovery/announce/v1",
        "site": site,
    }
    manifest_uris = [
        item.strip()
        for item in os.getenv(
            "SPATIALDDS_BOOTSTRAP_MANIFESTS",
            "spatialdds://vps.example.com/zone:sf-downtown/manifest:vps",
        ).split(",")
        if item.strip()
    ]
    if manifest_uris:
        manifest["manifest_uri"] = manifest_uris[0]
    auth_hint = os.getenv("SPATIALDDS_BOOTSTRAP_AUTH_HINT", "")
    if auth_hint:
        manifest["auth_hint"] = auth_hint
    return manifest


class SpatialDDSHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for SpatialDDS v1.7 endpoints"""

    @property
    def announce_registry(self):
        return _announce_registry

    def _set_headers(self, status_code: int = 200, content_type: str = "application/json"):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_json(self, data: Any, status_code: int = 200):
        self._set_headers(status_code)
        self.wfile.write(json.dumps(data, indent=2, default=str).encode())

    def _send_error_json(self, message: str, status_code: int = 400):
        self._send_json(
            {"error": message, "status": status_code, "timestamp": _now_ms()}, status_code
        )

    def do_OPTIONS(self):
        self._set_headers(204)

    def do_GET(self):
        parsed_path = self.path.split("?", 1)[0]

        if parsed_path == "/":
            self._send_json(
                {
                    "name": "SpatialDDS HTTP Binding",
                    "version": "1.7.0",
                    "endpoints": {
                        # Consolidated well-known namespace (one RFC 8615
                        # registration). `resolver` is not served here — this
                        # binding has no manifests of its own to resolve.
                        "bootstrap": "/.well-known/spatialdds/bootstrap",
                        "search": "/.well-known/spatialdds/search",
                        # demo-local extensions
                        "register": "/.well-known/spatialdds/register",
                        "list": "/.well-known/spatialdds/list",
                    },
                    "spec": "https://github.com/OpenArCloud/SpatialDDS-spec",
                }
            )
        elif parsed_path == "/.well-known/spatialdds/bootstrap":
            self._send_json(bootstrap_manifest())
        elif parsed_path == "/.well-known/spatialdds/list":
            self._send_json(
                {"count": len(self.announce_registry), "announces": self.announce_registry}
            )
        else:
            self._send_error_json(f"Endpoint not found: {parsed_path}", 404)

    def do_POST(self):
        parsed_path = self.path
        if parsed_path == "/.well-known/spatialdds/search":
            self._handle_search()
        elif parsed_path == "/.well-known/spatialdds/register":
            self._handle_register()
        else:
            self._send_error_json(f"Endpoint not found: {parsed_path}", 404)

    # -- POST handlers -----------------------------------------------------
    def _handle_search(self):
        """
        Handle CoverageQuery search
        POST /.well-known/spatialdds/search

        Request body: CoverageQuery JSON
        Response: CoverageResponse JSON
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_error_json("Empty request body", 400)
                return

            body = self.rfile.read(content_length).decode("utf-8")
            query = json.loads(body)

            # Validate required fields
            if "coverage" not in query or "coverage_frame_ref" not in query:
                self._send_error_json("CoverageQuery requires coverage[] and coverage_frame_ref", 400)
                return

            # Hard cutover: 1.7 deleted CoverageQuery.expr (and Appendix F.X).
            # Reject rather than silently ignoring a filter the caller believes
            # is being applied.
            if "expr" in query:
                self._send_error_json(
                    "CoverageQuery.expr was removed in SpatialDDS 1.7; use the "
                    "structured 'filter' (CoverageFilter) instead",
                    400,
                )
                return

            try:
                SpatialDDSValidator.validate_frame_ref(query["coverage_frame_ref"])
                SpatialDDSValidator.validate_coverage(query["coverage"], query["coverage_frame_ref"])
            except Exception as exc:
                self._send_error_json(f"Invalid coverage: {exc}", 400)
                return

            results = self._search_announces(query)

            response = {
                "query_id": query.get("query_id", str(uuid.uuid4())),
                "results": results,
                "next_page_token": "",
                "stamp": SpatialDDSValidator.now_time(),
            }
            self._send_json(response)

        except json.JSONDecodeError as exc:
            self._send_error_json(f"Invalid JSON: {exc}", 400)
        except Exception as exc:
            self._send_error_json(f"Internal error: {exc}", 500)

    def _handle_register(self):
        """
        Handle discovery.Announce or service manifest registration
        POST /.well-known/spatialdds/register
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_error_json("Empty request body", 400)
                return

            body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(body)

            if _is_service_manifest(payload):
                entry = _normalize_manifest(payload)
            else:
                entry = _normalize_announce(payload)

            self._register_announce(entry)

            now_time = SpatialDDSValidator.now_time()
            service_id = _entry_service_id(entry)
            self._send_json(
                {
                    "status": "registered",
                    "service_id": service_id,
                    "count": len(self.announce_registry),
                    "stamp": now_time,
                },
                201,
            )

        except json.JSONDecodeError as exc:
            self._send_error_json(f"Invalid JSON: {exc}", 400)
        except Exception as exc:
            self._send_error_json(f"Internal error: {exc}", 500)

    # -- Helpers -----------------------------------------------------------
    def _register_announce(self, entry: Dict[str, Any]):
        global _announce_registry
        service_id = _entry_service_id(entry)
        _announce_registry = [
            a for a in _announce_registry if _entry_service_id(a) != service_id
        ]
        _announce_registry.append(entry)
        label = "manifest" if entry.get("kind") == "manifest" else "announce"
        print(f"Registered service: {service_id} ({label})")

    def _search_announces(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Match registered services against a CoverageQuery and return
        ServiceSummary rows.

        1.7 changed CoverageResponse.results from sequence<Announce> to
        sequence<ServiceSummary>: callers can no longer pull caps/topics out
        of a search result. They rank on the summary, then resolve
        manifest_uri (or read the retained Announce off the bus) for detail.
        """
        results: List[Dict[str, Any]] = []
        has_filter = bool(query.get("has_filter"))
        filter_obj = query.get("filter", {}) if has_filter else {}
        coverage_q = query["coverage"]
        for ann in _announce_registry:
            try:
                if not SpatialDDSValidator.check_coverage_intersection(
                    coverage_q, ann.get("coverage", [])
                ):
                    continue

                if has_filter and not self._matches_filter(filter_obj, ann):
                    continue

                results.append(_to_service_summary(ann))
            except Exception:
                # Best-effort: include on validation failure to avoid accidental drops
                results.append(_to_service_summary(ann))
        return results

    @staticmethod
    def _matches_filter(filter_obj: Dict[str, Any], announce: Dict[str, Any]) -> bool:
        if not isinstance(filter_obj, dict):
            return True
        type_in = filter_obj.get("type_in") or []
        qos_in = filter_obj.get("qos_profile_in") or []
        module_id_in = filter_obj.get("module_id_in") or []

        payload = announce.get("payload", {}) if isinstance(announce, dict) else {}
        topics = payload.get("topics", [])
        if not topics and isinstance(payload.get("service", {}), dict):
            topics = payload.get("service", {}).get("topics", [])
        topic_match = True
        if type_in or qos_in:
            topic_match = False
            for topic in topics:
                if type_in and topic.get("type") not in type_in:
                    continue
                if qos_in and topic.get("qos_profile") not in qos_in:
                    continue
                topic_match = True
                break

        module_match = True
        if module_id_in:
            module_match = False
            caps = payload.get("caps", {}) if isinstance(payload, dict) else {}
            supported = caps.get("supported_profiles", []) if isinstance(caps, dict) else []
            for profile in supported:
                name = profile.get("name")
                major = profile.get("major")
                max_minor = profile.get("max_minor")
                if not name or major is None or max_minor is None:
                    continue
                # 1.7: ProfileSupport.name already carries the module family
                # ("spatial.core"), so it is NOT re-prefixed here — doing so
                # would produce "spatial.spatial.core/1.7".
                candidate = f"{name}/{int(major)}.{int(max_minor)}"
                if candidate in module_id_in:
                    module_match = True
                    break

        return topic_match and module_match


def run_server(port: int = 8080):
    server_address = ("", port)
    httpd = HTTPServer(server_address, SpatialDDSHTTPHandler)
    print(f"Serving SpatialDDS v1.7 HTTP binding on port {port}")
    httpd.serve_forever()


def _to_service_summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Project a registered announce/manifest onto a disco::ServiceSummary row.

    Deliberately narrow: caps, topics and transforms are NOT carried — that is
    the whole point of the 1.7 change. Consumers resolve manifest_uri (or read
    the retained Announce) when they need detail.
    """
    payload = entry.get("payload", {}) if isinstance(entry, dict) else {}
    if entry.get("kind") == "manifest":
        service = payload.get("service", {}) if isinstance(payload, dict) else {}
        service_id = service.get("service_id", "")
        kind = service.get("kind", "OTHER")
        name = service.get("name", "")
        manifest_uri = payload.get("id", "")
    else:
        service_id = payload.get("service_id", "")
        kind = payload.get("kind", "OTHER")
        name = payload.get("name", "")
        manifest_uri = payload.get("manifest_uri", "")

    summary = {
        "service_id": service_id,
        "kind": kind,
        "name": name,
        "manifest_uri": manifest_uri,
        "coverage": entry.get("coverage", []),
        "coverage_frame_ref": entry.get("coverage_frame_ref"),
        "stamp": payload.get("stamp") or SpatialDDSValidator.now_time(),
        "ttl_sec": int(payload.get("ttl_sec", 300) or 300),
    }
    return summary


def _is_service_manifest(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    profile = payload.get("profile", "")
    rtype = payload.get("rtype")
    # 1.7 retired the `spatial.manifest@1.x` form — slash form only.
    return isinstance(profile, str) and profile.startswith("spatial.manifest/1.") and rtype == "service"


def _normalize_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    # Hard cutover: @-form profiles and pre-1.7 minors are rejected outright.
    SpatialDDSValidator.validate_manifest_profile(manifest.get("profile", ""))

    service = manifest.get("service", {})
    if not isinstance(service, dict) or not service.get("service_id"):
        raise ValueError("Service manifest missing service.service_id")

    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("Service manifest missing coverage object")

    coverage_frame_ref = coverage.get("frame_ref")
    if not coverage_frame_ref:
        raise ValueError("Service manifest coverage.frame_ref is required")

    element = dict(coverage)
    element.pop("frame_ref", None)
    element["has_frame_ref"] = False

    SpatialDDSValidator.validate_frame_ref(coverage_frame_ref)
    SpatialDDSValidator.validate_coverage([element], coverage_frame_ref)

    manifest.setdefault("stamp", SpatialDDSValidator.now_time())
    manifest.setdefault("ttl_sec", 300)

    return {
        "kind": "manifest",
        "payload": manifest,
        "coverage": [element],
        "coverage_frame_ref": coverage_frame_ref,
    }


def _normalize_announce(announce: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(announce, dict):
        raise ValueError("Announce must be an object")

    required = ["service_id", "coverage", "coverage_frame_ref", "manifest_uri"]
    for field in required:
        if field not in announce:
            raise ValueError(f"Announce missing required '{field}' field")

    SpatialDDSValidator.validate_frame_ref(announce["coverage_frame_ref"])
    SpatialDDSValidator.validate_coverage(
        announce["coverage"], announce["coverage_frame_ref"]
    )

    now_time = SpatialDDSValidator.now_time()
    announce.setdefault("stamp", now_time)
    announce.setdefault("ttl_sec", 300)

    return {
        "kind": "announce",
        "payload": announce,
        "coverage": announce.get("coverage", []),
        "coverage_frame_ref": announce.get("coverage_frame_ref"),
    }


def _entry_service_id(entry: Dict[str, Any]) -> str:
    if not isinstance(entry, dict):
        return ""
    payload = entry.get("payload", {})
    if entry.get("kind") == "manifest":
        service = payload.get("service", {})
        if isinstance(service, dict):
            return service.get("service_id", "")
    return payload.get("service_id", "")


if __name__ == "__main__":
    try:
        port_arg = 8080
        if len(sys.argv) > 1 and sys.argv[1].isdigit():
            port_arg = int(sys.argv[1])
        run_server(port_arg)
    except KeyboardInterrupt:
        print("\nServer interrupted by user")
        sys.exit(0)
