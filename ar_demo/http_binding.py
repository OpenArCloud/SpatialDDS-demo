#!/usr/bin/env python3
"""
SpatialDDS v1.7 HTTP Binding
Provides REST API endpoints for discovery-style registration and search using
discovery.Announce and discovery.CoverageQuery/Response shapes.

1.7 consolidated the well-known namespace to a single RFC 8615 registration:
/.well-known/spatialdds/{bootstrap,resolver,search}. `register` and `list` are
demo-local extensions that live alongside them.

This server is the conformance harness: HTTP plumbing plus an in-memory
registry. The binding semantics — matching, filtering, pagination, manifest
assembly — live in `spatialdds_demo/discovery_http.py`, shared with the web
bridge so the two servers cannot drift.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import json
import os
import uuid
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import sys

from spatialdds_demo.discovery_http import (
    DiscoveryError,
    ServiceRecord,
    bootstrap_manifest,
    record_from_announce,
    record_from_manifest,
    search,
)
from spatialdds_validation import (
    SpatialDDSValidator,
    ValidationError,
)

# Module-level registry, shared across request handlers. The bridge's
# equivalent is its live announce cache; both feed the same core.
_announce_registry: List[ServiceRecord] = []


def _records() -> List[ServiceRecord]:
    return list(_announce_registry)


def _now_ms() -> int:
    return int(time.time() * 1000)


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
        parsed_path, _, raw_query = self.path.partition("?")

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
                        # POST for the full query, GET ?geohash= for the
                        # convenience form; §3.3.0 requires both.
                        "search": "/.well-known/spatialdds/search",
                        # demo-local extensions
                        "register": "/.well-known/spatialdds/register",
                        "list": "/.well-known/spatialdds/list",
                    },
                    "spec": "https://github.com/OpenArCloud/SpatialDDS-spec",
                }
            )
        elif parsed_path == "/.well-known/spatialdds/search":
            self._handle_search_get(raw_query)
        elif parsed_path == "/.well-known/spatialdds/bootstrap":
            self._send_json(bootstrap_manifest())
        elif parsed_path == "/.well-known/spatialdds/list":
            self._send_json(
                {
                    "count": len(self.announce_registry),
                    "announces": [
                        {
                            "kind": r.source,
                            "payload": r.payload,
                            "coverage": r.coverage,
                            "coverage_frame_ref": r.coverage_frame_ref,
                        }
                        for r in self.announce_registry
                    ],
                }
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

    # -- GET handlers ------------------------------------------------------
    def _handle_search_get(self, raw_query: str):
        """
        The GET convenience form.

        §3.3.0 makes it REQUIRED alongside POST — "for interoperability with
        the Geospatial DNS-SD binding" — and defines it as equivalent to a POST
        with `{"geohash": ...}` plus an optional `kind`. Built as exactly that
        body and handed to the same core call, so the two forms cannot answer
        differently.
        """
        params = parse_qs(raw_query)
        geohash = (params.get("geohash") or [""])[0]
        if not geohash:
            self._send_error_json(
                "GET /.well-known/spatialdds/search requires ?geohash=", 400)
            return
        body: Dict[str, Any] = {"geohash": geohash}
        kind = (params.get("kind") or [""])[0]
        if kind:
            body["kind"] = [kind]
        try:
            self._send_json(search(_records(), body))
        except DiscoveryError as exc:
            self._send_error_json(str(exc), exc.status)

    # -- POST handlers -----------------------------------------------------
    def _handle_search(self):
        """
        Handle a discovery search.
        POST /.well-known/spatialdds/search

        Request body: CoverageQuery JSON
        Response: { "results": [ <service manifest>, ... ],
                    "next_page_token": "" }
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_error_json("Empty request body", 400)
                return

            query = json.loads(self.rfile.read(content_length).decode("utf-8"))
            self._send_json(search(_records(), query))

        except DiscoveryError as exc:
            self._send_error_json(str(exc), exc.status)
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

            if _looks_like_manifest(payload):
                # Validate the profile before dispatching, so a retired @-form
                # manifest reports that rather than falling through to the
                # announce path and complaining about a missing service_id.
                SpatialDDSValidator.validate_manifest_profile(payload.get("profile", ""))
                if payload.get("rtype") != "service":
                    raise ValueError(
                        f"Unsupported manifest rtype '{payload.get('rtype')}'; "
                        "this binding registers rtype='service' manifests"
                    )
                record = record_from_manifest(payload)
            else:
                record = record_from_announce(payload)

            self._register_announce(record)

            now_time = SpatialDDSValidator.now_time()
            service_id = record.service_id
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
        except (DiscoveryError, ValidationError, ValueError) as exc:
            # A malformed body is the caller's problem, not ours — 400, not 500.
            self._send_error_json(str(exc), 400)
        except Exception as exc:
            self._send_error_json(f"Internal error: {exc}", 500)

    # -- Helpers -----------------------------------------------------------
    def _search_manifests(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Just the results list, for callers that don't want the envelope."""
        return search(_records(), query)["results"]

    def _register_announce(self, record: ServiceRecord):
        """Latest-wins registration, keyed by service_id."""
        global _announce_registry
        _announce_registry = [
            r for r in _announce_registry if r.service_id != record.service_id
        ]
        _announce_registry.append(record)
        print(f"Registered service: {record.service_id} ({record.source})")


# The registry seam the demo tests drive directly. Aliases onto the shared
# core, so the existing suite exercises exactly the code the server runs.
_normalize_announce = record_from_announce
_normalize_manifest = record_from_manifest


def run_server(port: int = 8080):
    server_address = ("", port)
    httpd = HTTPServer(server_address, SpatialDDSHTTPHandler)
    print(f"Serving SpatialDDS v1.7 HTTP binding on port {port}")
    httpd.serve_forever()


def _looks_like_manifest(payload: Dict[str, Any]) -> bool:
    """A manifest-shaped body, regardless of whether its profile is valid."""
    if not isinstance(payload, dict):
        return False
    profile = payload.get("profile", "")
    return isinstance(profile, str) and profile.startswith("spatial.manifest") and "rtype" in payload


if __name__ == "__main__":
    try:
        port_arg = 8080
        if len(sys.argv) > 1 and sys.argv[1].isdigit():
            port_arg = int(sys.argv[1])
        run_server(port_arg)
    except KeyboardInterrupt:
        print("\nServer interrupted by user")
        sys.exit(0)
