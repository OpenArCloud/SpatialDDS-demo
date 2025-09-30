#!/bin/bash
# SpatialDDS v1.3 - Run All Tests
# This script runs the complete test suite for v1.3 implementation

set -e  # Exit on error

echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║              SPATIALDDS v1.3 - COMPREHENSIVE TEST SUITE                      ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Test 1: Validation utilities
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " TEST 1: Validation Utilities"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 spatialdds_validation.py
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
python3 spatialdds_test.py --summary-only
if [ $? -eq 0 ]; then
    echo "✅ Protocol test (summary): PASSED"
else
    echo "❌ Protocol test (summary): FAILED"
    exit 1
fi
echo ""

# Test 3: HTTP binding (unit test style)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " TEST 3: HTTP Binding (Logic Test)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 -c "
from http_binding import SpatialDDSHTTPHandler
from spatialdds_validation import create_coverage_bbox_earth_fixed

# Test registration and search
handler = SpatialDDSHTTPHandler

announce = {
    'content_id': 'test-001',
    'self_uri': 'spatialdds://test.com/zone:test/service:test-001',
    'rtype': 'service',
    'title': 'Test Service',
    'coverage': create_coverage_bbox_earth_fixed(-122.5, 37.7, -122.3, 37.8),
    'tags': ['vps'],
    'class_id': 'spatial.service.vps'
}

handler.content_registry.append(announce)

query = {
    'rtype': 'service',
    'volume': create_coverage_bbox_earth_fixed(-122.45, 37.75, -122.4, 37.8),
    'tags': ['vps']
}

results = handler._search_content(handler, query)

if len(results) == 1:
    print('✅ HTTP binding logic: PASSED')
    print(f'   - Registration: OK')
    print(f'   - Search: OK ({len(results)} result found)')
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
echo "  ✅ URI validation and parsing"
echo "  ✅ Coverage model with CoverageElement"
echo "  ✅ Quaternion normalization"
echo "  ✅ Coverage intersection detection"
echo "  ✅ ContentQuery/ContentAnnounce protocol"
echo "  ✅ HTTP binding registration and search"
echo "  ✅ Frame-aware poses"
echo "  ✅ Anchor updates with v1.3 format"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 For detailed results, see: TEST_REPORT.md"
echo "📋 For migration guide, see: MIGRATION_GUIDE.md"
echo "📋 For usage examples, see: README.md"
echo ""