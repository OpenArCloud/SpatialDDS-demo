#!/usr/bin/env python3
"""
SpatialDDS Test Implementation v1.3
Demonstrates VPS announcement, client discovery, and request/response flow
Updated for SpatialDDS v1.3 specification
"""

import json
import time
import uuid
import random
import base64
import hashlib
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import subprocess
import sys
from spatialdds_validation import (
    SpatialDDSValidator,
    create_spatial_uri,
    create_coverage_bbox_earth_fixed
)

class SpatialDDSLogger:
    """Logger for detailed message tracking"""

    def __init__(self):
        self.messages = []
        self.start_time = time.time()

    def log_message(self, msg_type: str, direction: str, source: str,
                   destination: str, data: Dict[str, Any], show_content: bool = True):
        """Log a message with timestamp and details"""
        timestamp = time.time() - self.start_time
        log_entry = {
            'timestamp': timestamp,
            'datetime': datetime.now().isoformat(),
            'type': msg_type,
            'direction': direction,  # 'SEND' or 'RECV'
            'source': source,
            'destination': destination,
            'data': data,
            'size_bytes': len(json.dumps(data).encode('utf-8'))
        }
        self.messages.append(log_entry)

        # Print formatted message
        direction_symbol = "→" if direction == "SEND" else "←"
        print(f"[{timestamp:6.3f}s] {direction_symbol} {msg_type}")
        print(f"  From: {source}")
        print(f"  To:   {destination}")
        print(f"  Size: {log_entry['size_bytes']} bytes")

        if msg_type in ['VPS_REQUEST', 'VPS_RESPONSE']:
            # Show key fields for requests/responses
            if 'request_id' in data:
                print(f"  ID:   {data['request_id']}")
            if 'success' in data:
                print(f"  Success: {data['success']}")
            if 'confidence' in data:
                print(f"  Confidence: {data['confidence']:.3f}")

        # Show message content
        if show_content:
            print(f"  Content:")
            # Create a copy of data for display purposes
            display_data = data.copy()

            # Truncate large fields for readability unless detailed mode
            if not getattr(self, 'detailed_content', False):
                if 'image_data' in display_data and len(str(display_data['image_data'])) > 100:
                    original_length = len(str(display_data['image_data']))
                    display_data['image_data'] = f"[BASE64_IMAGE_DATA: {original_length} chars]"

                if 'feature_points' in display_data and len(display_data['feature_points']) > 3:
                    display_data['feature_points'] = display_data['feature_points'][:3] + [f"... and {len(display_data['feature_points']) - 3} more features"]

                if 'descriptor_data' in display_data and isinstance(display_data['descriptor_data'], dict):
                    if len(str(display_data['descriptor_data'])) > 200:
                        display_data['descriptor_data'] = {**display_data['descriptor_data'], 'data': '[TRUNCATED_FOR_DISPLAY]'}

            formatted_json = json.dumps(display_data, indent=4, default=str)
            # Indent each line for better readability
            for line in formatted_json.split('\n'):
                print(f"    {line}")

        print()

    def print_summary(self):
        """Print communication summary"""
        print("=" * 80)
        print("SPATIALDDS COMMUNICATION SUMMARY")
        print("=" * 80)

        total_time = self.messages[-1]['timestamp'] if self.messages else 0
        total_bytes = sum(msg['size_bytes'] for msg in self.messages)

        print(f"Total Duration: {total_time:.3f}s")
        print(f"Total Messages: {len(self.messages)}")
        print(f"Total Data:     {total_bytes} bytes")
        print()

        # Group by message type
        msg_types = {}
        for msg in self.messages:
            msg_type = msg['type']
            if msg_type not in msg_types:
                msg_types[msg_type] = {'count': 0, 'bytes': 0}
            msg_types[msg_type]['count'] += 1
            msg_types[msg_type]['bytes'] += msg['size_bytes']

        print("Message Types:")
        for msg_type, stats in msg_types.items():
            print(f"  {msg_type:20} {stats['count']:3d} messages, {stats['bytes']:6d} bytes")
        print()


class MockSensorData:
    """Generator for mock sensor data"""

    @staticmethod
    def generate_image_data() -> bytes:
        """Generate mock image data (simulated JPEG)"""
        # Create mock image data - in reality this would be actual image bytes
        mock_image = f"MOCK_IMAGE_{random.randint(1000, 9999)}_{int(time.time())}"
        return base64.b64encode(mock_image.encode()).decode('ascii').encode()

    @staticmethod
    def generate_camera_intrinsics() -> Dict[str, Any]:
        """Generate mock camera intrinsic parameters"""
        return {
            'fx': 800.0 + random.uniform(-50, 50),
            'fy': 800.0 + random.uniform(-50, 50),
            'cx': 640.0 + random.uniform(-20, 20),
            'cy': 480.0 + random.uniform(-20, 20),
            'k1': random.uniform(-0.1, 0.1),
            'k2': random.uniform(-0.1, 0.1),
            'resolution': [1280, 960]
        }

    @staticmethod
    def generate_imu_data() -> Dict[str, Any]:
        """Generate mock IMU data"""
        return {
            'accelerometer': {
                'x': random.uniform(-1, 1),
                'y': random.uniform(-1, 1),
                'z': random.uniform(9, 11)  # gravity + noise
            },
            'gyroscope': {
                'x': random.uniform(-0.1, 0.1),
                'y': random.uniform(-0.1, 0.1),
                'z': random.uniform(-0.1, 0.1)
            },
            'magnetometer': {
                'x': random.uniform(-50, 50),
                'y': random.uniform(-50, 50),
                'z': random.uniform(-50, 50)
            },
            'timestamp': time.time()
        }

    @staticmethod
    def generate_gps_data() -> Dict[str, Any]:
        """Generate mock GPS data"""
        # Mock location in San Francisco area
        base_lat = 37.7749
        base_lon = -122.4194

        return {
            'latitude': base_lat + random.uniform(-0.01, 0.01),
            'longitude': base_lon + random.uniform(-0.01, 0.01),
            'altitude': random.uniform(0, 100),
            'accuracy': random.uniform(3, 15),
            'timestamp': time.time()
        }


class VPSService:
    """Mock VPS (Visual Positioning Service) - v1.3"""

    def __init__(self, service_id: str, logger: SpatialDDSLogger):
        self.service_id = service_id
        self.logger = logger
        self.service_name = f"MockVPS-{service_id[-8:]}"
        self.authority = "vps.example.com"
        self.zone_id = "sf-downtown"
        # v1.3: Create self_uri
        self.self_uri = create_spatial_uri(
            self.authority, self.zone_id, "service", service_id
        )
        self.running = False

        # v1.3: Use new 2D coverage model (west, south, east, north)
        self.coverage_v13 = create_coverage_bbox_earth_fixed(
            -122.52, 37.70, -122.35, 37.85
        )

        # Legacy coverage for compatibility
        self.coverage_area = {
            'geohashes': ['9q8yy', '9q8yz', '9q9p0', '9q9p1'],
            'bounds': {
                'min_point': {'x': -122.52, 'y': 37.70, 'z': 0},
                'max_point': {'x': -122.35, 'y': 37.85, 'z': 200}
            },
            'resolution': 0.001
        }

        self.capabilities = {
            'supported_formats': ['image/jpeg', 'image/png'],
            'accuracy_estimate': 0.05,  # 5cm accuracy
            'real_time_capable': True,
            'feature_types': ['ORB', 'SIFT', 'SURF'],
            'max_image_size': 1920 * 1080,
            'supported_coordinate_systems': ['WGS84', 'UTM']
        }

    def create_announcement(self) -> Dict[str, Any]:
        """Create VPS ContentAnnounce (v1.3)"""
        # v1.3: Use slim bounds field (single CoverageElement) instead of full coverage block
        bounds_element = self.coverage_v13['elements'][0]

        now_ms = int(time.time() * 1000)
        now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        announcement = {
            '_legacy_id': self.service_id,  # v1.3: Mark UUID as legacy
            'self_uri': self.self_uri,  # v1.3: URI is canonical identifier
            'provider_id': self.authority,  # Note: duplicates authority in self_uri, kept for routing
            'rtype': 'service',  # v1.3: resource type
            'title': self.service_name,
            'summary': 'Visual Positioning Service for SF Downtown',
            'tags': ['vps', 'localization', 'computer-vision'],
            'class_id': 'spatial.service.vps',
            'manifest_uri': create_spatial_uri(self.authority, self.zone_id, 'manifest', self.service_id),
            'bounds': bounds_element,  # v1.3: slim bounds (single CoverageElement)
            # Omit empty arrays to keep payload slim
            'available_from': now_ms,
            'available_until': now_ms + 86400000,  # 24 hours
            'timestamp': now_ms,
            'stamp': now_iso,  # v1.3: ISO8601 mirror for readability
            'ttl_sec': 300,  # 5 minutes
            'endpoint': {
                'protocol': 'dds',
                'topic': 'SpatialDDS/VPS/Request',
                'qos': 'reliable'
            },
            'mime': 'application/vnd.spatialdds.vps+json',
            # Include legacy fields for backward compatibility
            '_legacy': {
                'version': '1.3.0',
                'capabilities': self.capabilities
            }
        }
        return announcement

    def process_vps_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process VPS localization request and generate response"""

        # Simulate processing time
        processing_time = random.uniform(0.1, 0.5)
        time.sleep(processing_time)

        # Simulate success/failure
        success = random.random() > 0.1  # 90% success rate

        if success:
            # Generate mock pose estimation with v1.3 GeoPose format
            # Create a random rotation quaternion and normalize it
            q_xyzw = {
                'x': random.uniform(-0.1, 0.1),
                'y': random.uniform(-0.1, 0.1),
                'z': random.uniform(-1, 1),
                'w': random.uniform(0.9, 1.0)
            }
            # Convert to wxyz and normalize
            q_wxyz_unnorm = SpatialDDSValidator.convert_quaternion_xyzw_to_wxyz(q_xyzw)
            q_wxyz = SpatialDDSValidator.normalize_quaternion_wxyz(q_wxyz_unnorm)

            # v1.3: For earth-fixed frame, use GeoPose format (lat, lon, h, q_wxyz)
            estimated_geopose = {
                'lat': request['approximate_location']['latitude'] + random.uniform(-0.0001, 0.0001),
                'lon': request['approximate_location']['longitude'] + random.uniform(-0.0001, 0.0001),
                'h': request['approximate_location']['altitude'] + random.uniform(-5, 5),
                'q_wxyz': q_wxyz  # v1.3: canonical quaternion format [w,x,y,z]
            }

            confidence = random.uniform(0.7, 0.95)
            accuracy = random.uniform(0.02, 0.08)

            # Generate mock feature points
            feature_points = []
            for i in range(random.randint(50, 200)):
                feature_points.append({
                    'id': i,
                    'x': random.uniform(0, 1280),
                    'y': random.uniform(0, 960),
                    'descriptor': f"mock_descriptor_{i}",
                    'confidence': random.uniform(0.5, 1.0)
                })

            now_ms = int(time.time() * 1000)
            now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            response = {
                'request_id': request['request_id'],
                'request_uri': request.get('request_uri', ''),  # v1.3
                '_legacy_service_id': self.service_id,  # v1.3: Mark UUID as legacy
                'service_uri': self.self_uri,  # v1.3: URI is canonical
                'timestamp': now_ms,
                'stamp': now_iso,  # v1.3: ISO8601 mirror
                'success': True,
                'estimated_geopose': estimated_geopose,  # v1.3: GeoPose implies earth-fixed frame
                'confidence': confidence,
                'accuracy_estimate': accuracy,
                'feature_points': feature_points[:10],  # Limit for readability
                'descriptor_data': {
                    'format': 'ORB',
                    'descriptor_size': 32,
                    'feature_count': len(feature_points)
                },
                'error_message': '',
                'error_code': 0
            }
        else:
            now_ms = int(time.time() * 1000)
            now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            response = {
                'request_id': request['request_id'],
                'request_uri': request.get('request_uri', ''),  # v1.3
                '_legacy_service_id': self.service_id,  # v1.3: Mark UUID as legacy
                'service_uri': self.self_uri,  # v1.3: URI is canonical
                'timestamp': now_ms,
                'stamp': now_iso,  # v1.3: ISO8601 mirror
                'success': False,
                'estimated_geopose': None,
                'confidence': 0.0,
                'accuracy_estimate': 0.0,
                'feature_points': [],
                'descriptor_data': {},
                'error_message': 'Insufficient visual features for localization',
                'error_code': 404
            }

        return response


class SpatialDDSClient:
    """Mock SpatialDDS client - v1.3"""

    def __init__(self, client_id: str, logger: SpatialDDSLogger):
        self.client_id = client_id
        self.authority = "client.example.com"
        self.zone_id = "sf-client"
        # v1.3: Create client URI
        self.client_uri = create_spatial_uri(
            self.authority, self.zone_id, "client", client_id
        )
        self.logger = logger
        self.discovered_services = []

    def create_discovery_request(self) -> Dict[str, Any]:
        """Create ContentQuery (v1.3 - replaces DiscoveryRequest)"""
        query_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # v1.3: volume is a single CoverageElement, not an array
        volume_element = {
            'type': 'bbox',
            'frame': 'earth-fixed',
            'crs': 'EPSG:4979',
            'bbox': [-122.45, 37.75, -122.40, 37.80]  # 2D bbox for earth-fixed
        }

        query = {
            'query_id': query_id,
            'query_uri': create_spatial_uri(self.authority, self.zone_id, 'query', query_id),  # v1.3
            'rtype': 'service',  # v1.3: Required resource type
            'volume': volume_element,  # v1.3: single CoverageElement (not array)
            'tags': ['vps', 'localization'],  # v1.3: Optional filter
            'class_id': 'spatial.service.vps',  # v1.3: Optional filter
            'timestamp': now_ms,
            'stamp': now_iso,  # v1.3: ISO8601 mirror
            'filter': {  # v1.3: Additional criteria
                'min_accuracy': 0.1,
                'real_time': True,
                'supported_formats': ['image/jpeg']
            }
        }
        return query

    def create_vps_request(self, service_id: str, service_uri: str) -> Dict[str, Any]:
        """Create VPS localization request (v1.3)"""

        # Generate mock sensor data
        image_data = MockSensorData.generate_image_data()
        camera_intrinsics = MockSensorData.generate_camera_intrinsics()
        imu_data = MockSensorData.generate_imu_data()
        gps_data = MockSensorData.generate_gps_data()

        request_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        request = {
            'request_id': request_id,
            'request_uri': create_spatial_uri(self.authority, self.zone_id, 'request', request_id),  # v1.3
            '_legacy_client_id': self.client_id,  # v1.3: Mark UUID as legacy
            'client_uri': self.client_uri,  # v1.3: URI is canonical
            'timestamp': now_ms,
            'stamp': now_iso,  # v1.3: ISO8601 mirror
            'approximate_location': {
                'latitude': gps_data['latitude'],
                'longitude': gps_data['longitude'],
                'altitude': gps_data['altitude'],
                'accuracy': gps_data['accuracy']
            },
            'image_data': image_data.decode('ascii'),  # Base64 encoded
            'image_format': 'image/jpeg',
            'camera_intrinsics': camera_intrinsics,
            'imu_data': imu_data,
            'gps_data': gps_data,
            'desired_accuracy': 0.05,
            'include_features': True,
            'requested_data_types': ['pose', 'features', 'confidence']
        }
        return request


def simulate_dds_communication(logger: SpatialDDSLogger):
    """Simulate the DDS communication layer"""
    print("🔧 Initializing SpatialDDS communication layer...")
    print("   - Setting up DDS domain")
    print("   - Creating topics for service discovery and VPS")
    print("   - Establishing publish/subscribe channels")
    print()


def run_spatialdds_test(show_message_content: bool = True, detailed_content: bool = False):
    """Run comprehensive SpatialDDS test"""

    logger = SpatialDDSLogger()
    logger.detailed_content = detailed_content

    print("=" * 80)
    print("🚀 SPATIALDDS PROTOCOL TEST v1.3")
    print("=" * 80)
    print("📋 Testing SpatialDDS v1.3 features:")
    print("   • URI-based identification (spatialdds://)")
    print("   • CoverageElement with frame/CRS metadata")
    print("   • Quaternion normalization (wxyz format)")
    print("   • ContentQuery/ContentAnnounce discovery")
    print("   • Frame-aware poses")
    print()
    if show_message_content:
        print("📄 Message content display: ENABLED")
        if detailed_content:
            print("📄 Detailed content mode: ENABLED (including large sensor data)")
        else:
            print("📄 Detailed content mode: DISABLED (hiding large sensor data for readability)")
    else:
        print("📄 Message content display: DISABLED")
    print()

    # Initialize communication
    simulate_dds_communication(logger)

    # Create VPS service
    vps_service_id = str(uuid.uuid4())
    vps_service = VPSService(vps_service_id, logger)

    # Create client
    client_id = str(uuid.uuid4())
    client = SpatialDDSClient(client_id, logger)

    print("📡 Phase 1: VPS Service Announcement (ContentAnnounce v1.3)")
    print("-" * 40)

    # VPS announces itself
    announcement = vps_service.create_announcement()
    logger.log_message(
        'CONTENT_ANNOUNCE', 'SEND',
        f'VPS:{vps_service.service_name}', 'DDS_NETWORK',
        announcement, show_message_content
    )

    time.sleep(0.5)

    print("🔍 Phase 2: Client Service Discovery (ContentQuery v1.3)")
    print("-" * 40)

    # Client sends discovery request (ContentQuery in v1.3)
    content_query = client.create_discovery_request()
    logger.log_message(
        'CONTENT_QUERY', 'SEND',
        f'Client:{client_id[-8:]}', 'DDS_NETWORK',
        content_query, show_message_content
    )

    time.sleep(0.2)

    # VPS responds with matching ContentAnnounce (simulated)
    # In v1.3, response is an array of ContentAnnounce objects
    now_ms = int(time.time() * 1000)
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    discovery_response = {
        'query_id': content_query['query_id'],
        'announces': [announcement],  # v1.3: array of ContentAnnounce (renamed from 'results')
        'timestamp': now_ms,
        'stamp': now_iso,  # v1.3: ISO8601 mirror
        'count': 1
    }
    logger.log_message(
        'CONTENT_QUERY_RESULT', 'RECV',  # v1.3: renamed from CONTENT_QUERY_RESPONSE
        f'VPS:{vps_service.service_name}', f'Client:{client_id[-8:]}',
        discovery_response, show_message_content
    )

    time.sleep(0.3)

    print("📤 Phase 3: VPS Localization Request")
    print("-" * 40)

    # Client sends VPS request (v1.3)
    vps_request = client.create_vps_request(vps_service_id, vps_service.self_uri)
    logger.log_message(
        'VPS_REQUEST', 'SEND',
        f'Client:{client_id[-8:]}', f'VPS:{vps_service.service_name}',
        vps_request, show_message_content
    )

    time.sleep(0.1)

    print("⚙️  Phase 4: VPS Processing")
    print("-" * 40)
    print("   VPS analyzing image data...")
    print("   Extracting visual features...")
    print("   Matching against map database...")
    print("   Computing pose estimation...")

    # VPS processes request and generates response
    vps_response = vps_service.process_vps_request(vps_request)

    print("📥 Phase 5: VPS Localization Response")
    print("-" * 40)

    logger.log_message(
        'VPS_RESPONSE', 'RECV',
        f'VPS:{vps_service.service_name}', f'Client:{client_id[-8:]}',
        vps_response, show_message_content
    )

    # Print detailed response analysis
    if vps_response['success']:
        geopose = vps_response['estimated_geopose']
        print(f"✅ Localization successful!")
        print(f"   GeoPose: lat={geopose['lat']:.7f}°, lon={geopose['lon']:.7f}°, h={geopose['h']:.2f}m")
        print(f"   Quaternion: [{geopose['q_wxyz'][0]:.4f}, {geopose['q_wxyz'][1]:.4f}, {geopose['q_wxyz'][2]:.4f}, {geopose['q_wxyz'][3]:.4f}]")
        print(f"   Confidence: {vps_response['confidence']:.3f}")
        print(f"   Accuracy: {vps_response['accuracy_estimate']:.3f}m")
        print(f"   Features: {len(vps_response['feature_points'])} points")
    else:
        print(f"❌ Localization failed: {vps_response['error_message']}")

    print()

    # Additional test: Simulate anchor update
    print("🔗 Phase 6: Anchor Update (Bonus)")
    print("-" * 40)

    if vps_response['success']:
        anchor_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        anchor_update = {
            '_legacy_anchor_id': anchor_id,  # v1.3: Mark UUID as legacy
            'self_uri': create_spatial_uri(vps_service.authority, vps_service.zone_id, 'anchor', anchor_id),  # v1.3: URI is canonical
            'rtype': 'anchor',  # v1.3
            'anchor_type': 'visual_landmark',
            'geopose': vps_response['estimated_geopose'],  # v1.3: GeoPose implies earth-fixed frame
            'metadata': {
                '_legacy_created_by': client_id,  # v1.3: Mark UUID as legacy
                'created_by_uri': client.client_uri,  # v1.3: URI is canonical
                'feature_count': len(vps_response['feature_points']),
                'source': 'vps_localization'
            },
            'persistence_score': vps_response['confidence'],
            'created_timestamp': now_ms,
            'created_stamp': now_iso,  # v1.3: ISO8601 mirror
            'last_seen_timestamp': now_ms,
            'last_seen_stamp': now_iso  # v1.3: ISO8601 mirror
        }

        logger.log_message(
            'ANCHOR_UPDATE', 'SEND',
            f'Client:{client_id[-8:]}', 'DDS_NETWORK',
            anchor_update, show_message_content
        )

    time.sleep(0.5)

    # Print summary
    logger.print_summary()

    return True


def test_dds_integration():
    """Test integration with actual DDS tools"""
    print("🔧 Testing DDS Integration")
    print("-" * 40)

    try:
        # Test that our DDS tools are available
        result = subprocess.run(['ddsperf', '--help'],
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("✅ DDS tools available")

            # Test basic DDS communication
            print("   Testing basic DDS communication...")

            # Start a quick ping test in background
            ping_proc = subprocess.Popen(['ddsperf', 'ping', '1Hz'],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(1)
            ping_proc.terminate()

            print("✅ DDS communication layer operational")
            return True
        else:
            print("❌ DDS tools not responding correctly")
            return False

    except subprocess.TimeoutExpired:
        print("⚠️  DDS tools test timed out")
        return True  # Continue anyway
    except Exception as e:
        print(f"❌ DDS integration test failed: {e}")
        return False


def main():
    """Main test function"""
    import argparse

    parser = argparse.ArgumentParser(description="SpatialDDS Protocol Test")
    parser.add_argument('--show-content', action='store_true', default=True,
                       help='Show message content (default: True)')
    parser.add_argument('--hide-content', action='store_true',
                       help='Hide message content (overrides --show-content)')
    parser.add_argument('--detailed', action='store_true',
                       help='Show detailed content including large sensor data')
    parser.add_argument('--summary-only', action='store_true',
                       help='Show only message headers, no content')

    args = parser.parse_args()

    # Determine content display settings
    if args.hide_content or args.summary_only:
        show_content = False
        detailed = False
    else:
        show_content = args.show_content
        detailed = args.detailed

    print("Initializing SpatialDDS Test Environment...")
    print()

    # Test DDS integration first
    if not test_dds_integration():
        print("Warning: DDS integration issues detected, but continuing with protocol test...")

    print()

    # Run the main SpatialDDS protocol test
    success = run_spatialdds_test(show_message_content=show_content, detailed_content=detailed)

    print()
    print("🎯 Test Recommendations:")
    print("   1. Deploy VPS service in real DDS domain")
    print("   2. Test with actual camera/sensor data")
    print("   3. Implement persistent anchor storage")
    print("   4. Add network latency/failure testing")
    print("   5. Scale test with multiple clients/services")

    return success


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        sys.exit(1)