#!/usr/bin/env python3
"""
SpatialDDS v1.4 HTTP Binding
Provides REST API endpoints for discovery-style registration and search using
discovery.Announce and discovery.CoverageQuery/Response shapes.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, List, Any
import sys

from spatialdds_validation import (
    SpatialDDSValidator,
)

# Module-level registry (shared across all request handlers)
_announce_registry: List[Dict[str, Any]] = []


def _now_ms() -> int:
    return int(time.time() * 1000)


class SpatialDDSHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for SpatialDDS v1.4 endpoints"""

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
        parsed_path = self.path

        if parsed_path == "/":
            self._send_json(
                {
                    "name": "SpatialDDS HTTP Binding",
                    "version": "1.4.0",
                    "endpoints": {
                        "search": "/.well-known/spatialdds/search",
                        "register": "/.well-known/spatialdds/register",
                        "list": "/.well-known/spatialdds/list",
                    },
                    "spec": "https://github.com/OpenArCloud/SpatialDDS-spec",
                }
            )
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
        Handle discovery.Announce registration
        POST /.well-known/spatialdds/register
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_error_json("Empty request body", 400)
                return

            body = self.rfile.read(content_length).decode("utf-8")
            announce = json.loads(body)

            required = ["service_id", "coverage", "coverage_frame_ref", "manifest_uri"]
            for field in required:
                if field not in announce:
                    self._send_error_json(f"Announce missing required '{field}' field", 400)
                    return

            SpatialDDSValidator.validate_frame_ref(announce["coverage_frame_ref"])
            SpatialDDSValidator.validate_coverage(
                announce["coverage"], announce["coverage_frame_ref"]
            )

            now_time = SpatialDDSValidator.now_time()
            announce.setdefault("stamp", now_time)
            announce.setdefault("ttl_sec", 300)

            self._register_announce(announce)

            self._send_json(
                {
                    "status": "registered",
                    "service_id": announce["service_id"],
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
    def _register_announce(self, announce: Dict[str, Any]):
        global _announce_registry
        _announce_registry = [
            a for a in _announce_registry if a.get("service_id") != announce["service_id"]
        ]
        _announce_registry.append(announce)
        print(f"Registered service: {announce['service_id']} ({announce.get('name', 'unnamed')})")

    def _search_announces(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        expr = query.get("expr", "")
        coverage_q = query["coverage"]
        for ann in _announce_registry:
            try:
                if not SpatialDDSValidator.check_coverage_intersection(
                    coverage_q, ann.get("coverage", [])
                ):
                    continue

                if expr and not self._matches_expr(expr, ann):
                    continue

                results.append(ann)
            except Exception:
                # Best-effort: include on validation failure to avoid accidental drops
                results.append(ann)
        return results

    @staticmethod
    def _matches_expr(expr: str, announce: Dict[str, Any]) -> bool:
        """
        Minimal filter: supports expressions like kind=="VPS" or org=="ExampleOrg".
        This is intentionally simple for demo purposes.
        """
        if "==" not in expr:
            return True
        try:
            left, right = expr.split("==")
            left = left.strip()
            right = right.strip().strip('"').strip("'")
            value = str(announce.get(left, ""))
            return value == right
        except Exception:
            return True


def run_server(port: int = 8080):
    server_address = ("", port)
    httpd = HTTPServer(server_address, SpatialDDSHTTPHandler)
    print(f"Serving SpatialDDS v1.4 HTTP binding on port {port}")
    httpd.serve_forever()


if __name__ == "__main__":
    try:
        port_arg = 8080
        if len(sys.argv) > 1 and sys.argv[1].isdigit():
            port_arg = int(sys.argv[1])
        run_server(port_arg)
    except KeyboardInterrupt:
        print("\nServer interrupted by user")
        sys.exit(0)
