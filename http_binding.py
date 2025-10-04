#!/usr/bin/env python3
"""
SpatialDDS v1.3 HTTP Binding
Provides REST API endpoint for ContentQuery/ContentAnnounce discovery
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse, parse_qs
import sys

try:
    from spatialdds_validation import (
        SpatialDDSValidator,
        create_spatial_uri,
        create_coverage_bbox_earth_fixed
    )
except ImportError:
    print("Warning: spatialdds_validation module not found. Validation will be limited.")
    SpatialDDSValidator = None


# Module-level registry (shared across all request handlers)
_content_registry: List[Dict[str, Any]] = []


class SpatialDDSHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for SpatialDDS v1.3 endpoints"""

    @property
    def content_registry(self):
        """Access module-level registry"""
        return _content_registry

    def _set_headers(self, status_code: int = 200, content_type: str = 'application/json'):
        """Set response headers"""
        self.send_response(status_code)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _send_json(self, data: Any, status_code: int = 200):
        """Send JSON response"""
        self._set_headers(status_code)
        self.wfile.write(json.dumps(data, indent=2).encode())

    def _send_error_json(self, message: str, status_code: int = 400):
        """Send JSON error response"""
        self._send_json({
            'error': message,
            'status': status_code,
            'timestamp': int(time.time() * 1000)
        }, status_code)

    def do_OPTIONS(self):
        """Handle CORS preflight"""
        self._set_headers(204)

    def do_GET(self):
        """Handle GET requests"""
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/':
            # Root endpoint - API info
            self._send_json({
                'name': 'SpatialDDS HTTP Binding',
                'version': '1.3.0',
                'endpoints': {
                    'search': '/.well-known/spatialdds/search',
                    'register': '/.well-known/spatialdds/register',
                    'list': '/.well-known/spatialdds/list'
                },
                'spec': 'https://github.com/OpenArCloud/SpatialDDS-spec'
            })

        elif parsed_path.path == '/.well-known/spatialdds/list':
            # List all registered content
            self._send_json({
                'count': len(self.content_registry),
                'content': self.content_registry,
                'timestamp': int(time.time() * 1000)
            })

        else:
            self._send_error_json(f"Endpoint not found: {parsed_path.path}", 404)

    def do_POST(self):
        """Handle POST requests"""
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/.well-known/spatialdds/search':
            self._handle_search()

        elif parsed_path.path == '/.well-known/spatialdds/register':
            self._handle_register()

        else:
            self._send_error_json(f"Endpoint not found: {parsed_path.path}", 404)

    def _handle_search(self):
        """
        Handle ContentQuery search
        POST /.well-known/spatialdds/search

        Request body: ContentQuery JSON
        Response: Array of ContentAnnounce JSON objects
        """
        try:
            # Read and parse request body
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_error_json("Empty request body", 400)
                return

            body = self.rfile.read(content_length).decode('utf-8')
            query = json.loads(body)

            # Validate ContentQuery structure
            if 'rtype' not in query:
                self._send_error_json("ContentQuery missing required 'rtype' field", 400)
                return

            if 'volume' not in query:
                self._send_error_json("ContentQuery missing required 'volume' field", 400)
                return

            # Validate volume structure if validator is available
            # v1.3: volume is a single CoverageElement, but accept legacy array format
            if SpatialDDSValidator:
                try:
                    volume = query['volume']
                    if isinstance(volume, dict):
                        if 'elements' in volume:
                            # Legacy format: {elements: [...]}
                            SpatialDDSValidator.validate_coverage(volume)
                        else:
                            # v1.3 format: single CoverageElement
                            SpatialDDSValidator.validate_coverage_element(volume)
                    else:
                        self._send_error_json("Invalid volume format", 400)
                        return
                except Exception as e:
                    self._send_error_json(f"Invalid volume: {e}", 400)
                    return

            # Search for matching content
            results = self._search_content(query)

            now_ms = int(time.time() * 1000)
            now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            # Return announces as array of ContentAnnounce objects
            self._send_json({
                'query_id': query.get('query_id', str(uuid.uuid4())),
                'announces': results,  # v1.3: renamed from 'results' to 'announces'
                'count': len(results),
                'timestamp': now_ms,
                'stamp': now_iso  # v1.3: ISO8601 mirror for readability
            })

        except json.JSONDecodeError as e:
            self._send_error_json(f"Invalid JSON: {e}", 400)
        except Exception as e:
            self._send_error_json(f"Internal error: {e}", 500)

    def _handle_register(self):
        """
        Handle ContentAnnounce registration
        POST /.well-known/spatialdds/register

        Request body: ContentAnnounce JSON
        Response: Registration confirmation
        """
        try:
            # Read and parse request body
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_error_json("Empty request body", 400)
                return

            body = self.rfile.read(content_length).decode('utf-8')
            announce = json.loads(body)

            # Validate ContentAnnounce structure
            # v1.3: self_uri is canonical, accept either content_id or _legacy_id
            required_fields = ['self_uri', 'rtype']
            for field in required_fields:
                if field not in announce:
                    self._send_error_json(f"ContentAnnounce missing required '{field}' field", 400)
                    return

            # v1.3: Accept either 'bounds' (slim) or 'coverage' (legacy)
            if 'bounds' not in announce and 'coverage' not in announce:
                self._send_error_json("ContentAnnounce missing required 'bounds' or 'coverage' field", 400)
                return

            # Validate URI if validator is available
            if SpatialDDSValidator:
                try:
                    SpatialDDSValidator.validate_spatial_uri(announce['self_uri'])
                    # Validate bounds or coverage
                    if 'bounds' in announce:
                        SpatialDDSValidator.validate_coverage_element(announce['bounds'])
                    elif 'coverage' in announce:
                        SpatialDDSValidator.validate_coverage(announce['coverage'])
                except Exception as e:
                    self._send_error_json(f"Validation error: {e}", 400)
                    return

            # Add timestamp if not present
            now_ms = int(time.time() * 1000)
            now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            if 'timestamp' not in announce:
                announce['timestamp'] = now_ms
            if 'stamp' not in announce:
                announce['stamp'] = now_iso  # v1.3: ISO8601 mirror

            # Store in registry (use self_uri as key)
            self._register_content(announce)

            self._send_json({
                'status': 'registered',
                'self_uri': announce['self_uri'],  # v1.3: URI is canonical
                'timestamp': now_ms,
                'stamp': now_iso
            }, 201)

        except json.JSONDecodeError as e:
            self._send_error_json(f"Invalid JSON: {e}", 400)
        except Exception as e:
            self._send_error_json(f"Internal error: {e}", 500)

    def _search_content(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Search registered content based on query

        Args:
            query: ContentQuery dict

        Returns:
            List of matching ContentAnnounce objects
        """
        results = []
        rtype_filter = query.get('rtype')
        tags_filter = query.get('tags', [])
        class_filter = query.get('class_id')
        query_volume = query.get('volume')

        for content in self.content_registry:
            # Filter by rtype
            if rtype_filter and content.get('rtype') != rtype_filter:
                continue

            # Filter by tags
            if tags_filter:
                content_tags = content.get('tags', [])
                if not any(tag in content_tags for tag in tags_filter):
                    continue

            # Filter by class_id
            if class_filter and content.get('class_id') != class_filter:
                continue

            # Check spatial intersection if validator available
            if SpatialDDSValidator and query_volume:
                try:
                    # Normalize query volume to coverage format
                    if 'elements' in query_volume:
                        # Legacy format: {elements: [...]}
                        query_coverage = query_volume
                    else:
                        # v1.3 format: single CoverageElement
                        query_coverage = {'elements': [query_volume]}

                    # Normalize content bounds/coverage to coverage format
                    content_coverage = None
                    if 'bounds' in content:
                        # v1.3: bounds is single CoverageElement
                        content_coverage = {'elements': [content['bounds']]}
                    elif 'coverage' in content:
                        # Legacy: full coverage
                        content_coverage = content['coverage']

                    if content_coverage and not SpatialDDSValidator.check_coverage_intersection(
                        query_coverage, content_coverage
                    ):
                        continue
                except Exception:
                    # If intersection check fails, include the result
                    pass

            results.append(content)

        return results

    def _register_content(self, announce: Dict[str, Any]):
        """Register or update ContentAnnounce"""
        global _content_registry
        # v1.3: Use self_uri as canonical identifier
        self_uri = announce['self_uri']

        # Remove existing entry with same self_uri
        _content_registry = [
            c for c in _content_registry
            if c.get('self_uri') != self_uri
        ]

        # Add new entry
        _content_registry.append(announce)

        # Log registration
        print(f"Registered: {announce.get('rtype')}/{self_uri} - {announce.get('title', 'Untitled')}")

    def log_message(self, format, *args):
        """Override to customize logging"""
        print(f"[{self.log_date_time_string()}] {format % args}")


def run_server(port: int = 8080, host: str = '0.0.0.0'):
    """
    Start HTTP binding server

    Args:
        port: Port number to listen on
        host: Host address to bind to
    """
    server_address = (host, port)
    httpd = HTTPServer(server_address, SpatialDDSHTTPHandler)

    print("=" * 80)
    print("🌐 SpatialDDS v1.3 HTTP Binding Server")
    print("=" * 80)
    print(f"Listening on: http://{host}:{port}")
    print()
    print("Endpoints:")
    print(f"  • POST /.well-known/spatialdds/search    - Search for content (ContentQuery)")
    print(f"  • POST /.well-known/spatialdds/register  - Register content (ContentAnnounce)")
    print(f"  • GET  /.well-known/spatialdds/list      - List all registered content")
    print(f"  • GET  /                                  - API information")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 80)
    print()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        httpd.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SpatialDDS v1.3 HTTP Binding Server")
    parser.add_argument('--port', type=int, default=8080, help='Port to listen on (default: 8080)')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')

    args = parser.parse_args()

    run_server(port=args.port, host=args.host)