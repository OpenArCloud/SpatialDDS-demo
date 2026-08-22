#!/bin/bash
# SpatialDDS v1.7 - Run All Tests
# This script runs the complete test suite for v1.7 implementation

set -e  # Exit on error

# Run from the directory containing this script so relative paths work
# regardless of where the user invokes it from. Shared modules
# (spatialdds_test.py, spatialdds_validation.py, spatialdds_demo/) live at
# the repo root one level up; add it to PYTHONPATH so imports resolve.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SCRIPT_DIR}"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║              SPATIALDDS v1.7 - COMPREHENSIVE TEST SUITE                      ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Test 1: Validation utilities
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " TEST 1: Validation Utilities"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 -m spatialdds_validation
if [ $? -eq 0 ]; then
    echo "✅ Validation utilities: PASSED"
else
    echo "❌ Validation utilities: FAILED"
    exit 1
fi
echo ""

# Test 2: Protocol test (summary mode)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " TEST 2: SpatialDDS Protocol (Summary Mode)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 -m spatialdds_test --summary-only
if [ $? -eq 0 ]; then
    echo "✅ Protocol test (summary): PASSED"
else
    echo "❌ Protocol test (summary): FAILED"
    exit 1
fi
echo ""

# Test 3: Demo topic + manifest checks
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " TEST 3: Demo Topic + Manifest Checks"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 spatialdds_demo_tests.py
if [ $? -eq 0 ]; then
    echo "✅ Demo topic + manifest checks: PASSED"
else
    echo "❌ Demo topic + manifest checks: FAILED"
    exit 1
fi
echo ""

# Test 4: HTTP binding (unit test style)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " TEST 4: HTTP Binding (Logic Test)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 -c "
from http_binding import SpatialDDSHTTPHandler, _normalize_announce
from spatialdds_validation import create_coverage_bbox_earth_fixed, SpatialDDSValidator
import uuid

handler = SpatialDDSHTTPHandler

frame_ref, cov_elem = create_coverage_bbox_earth_fixed(-122.5, 37.7, -122.3, 37.8)

announce = {
    'service_id': 'svc:vps:test/001',
    'name': 'Test Service',
    'kind': 'VPS',
    'coverage': [cov_elem],
    'coverage_frame_ref': frame_ref,
    'manifest_uri': 'spatialdds://test.com/zone:test/manifest:svc',
    'caps': {},
    'topics': [],
    'stamp': SpatialDDSValidator.now_time(),
    'ttl_sec': 60
}

# Match how POST /register normalizes the body before registering
handler._register_announce(handler, _normalize_announce(announce))

frame_ref_q, cov_elem_q = create_coverage_bbox_earth_fixed(-122.45, 37.75, -122.4, 37.8)

# 1.7: 'expr' is gone; 'filter' (CoverageFilter) is the only query form.
query = {
    'query_id': str(uuid.uuid4()),
    'coverage': [cov_elem_q],
    'coverage_frame_ref': frame_ref_q,
    'has_filter': True,
    'filter': {'type_in': [], 'qos_profile_in': [], 'module_id_in': []}
}

results = handler._search_announces(handler, query)

# 1.7: results are compact ServiceSummary rows, never full announces.
ok = len(results) == 1
if ok:
    row = results[0]
    SpatialDDSValidator.validate_service_summary(row)
    ok = row['service_id'] == 'svc:vps:test/001' and not (set(row) & {'caps', 'topics'})

if ok:
    print('✅ HTTP binding logic: PASSED')
    print('   - Registration: OK')
    print(f'   - Search: OK ({len(results)} ServiceSummary row found)')
    exit(0)
else:
    print('❌ HTTP binding logic: FAILED')
    exit(1)
"
if [ $? -eq 0 ]; then
    echo ""
else
    echo "❌ HTTP binding test: FAILED"
    exit 1
fi

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " TEST SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✅ All tests passed successfully!"
echo ""
echo "Test Coverage:"
echo "  ✅ FrameRef/Time validation"
echo "  ✅ CoverageElement presence flags"
echo "  ✅ Quaternion normalization (x,y,z,w)"
echo "  ✅ CoverageQuery/Response flow"
echo "  ✅ HTTP binding registration and search"
echo "  ✅ GeoPose + AnchorDelta demo"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 See README.md for usage and protocol notes"
echo ""
