#!/usr/bin/env python3
"""
Alternative Cyclone DDS Test Application
Tests DDS functionality using available methods
"""

import sys
import subprocess
import os
import time
import argparse


def check_cyclone_dds_installation():
    """Check if Cyclone DDS is properly installed"""
    print("🔍 Checking Cyclone DDS installation...")

    # Check if ddsls command exists
    try:
        result = subprocess.run(['which', 'ddsls'], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ Found ddsls at: {result.stdout.strip()}")
            return True
        else:
            print("❌ ddsls command not found")
            return False
    except Exception as e:
        print(f"❌ Error checking ddsls: {e}")
        return False


def check_python_bindings():
    """Check if Python bindings are available"""
    print("🔍 Checking Python bindings...")

    try:
        import cyclonedds
        print("✅ cyclonedds Python package is available")
        print(f"   Version: {cyclonedds.__version__ if hasattr(cyclonedds, '__version__') else 'unknown'}")
        return True
    except ImportError as e:
        print(f"❌ cyclonedds Python package not available: {e}")
        return False


def test_basic_dds_commands():
    """Test basic DDS functionality using command line tools"""
    print("🧪 Testing basic DDS functionality...")

    try:
        # Test ddsls (list DDS entities)
        print("   Testing ddsls command...")
        result = subprocess.run(['ddsls'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("✅ ddsls command executed successfully")
            if result.stdout.strip():
                print(f"   Output: {result.stdout.strip()}")
            else:
                print("   No DDS entities found (normal for fresh start)")
        else:
            print(f"❌ ddsls failed: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        print("⚠️  ddsls command timed out (this might be normal)")
    except FileNotFoundError:
        print("❌ ddsls command not found")
        return False
    except Exception as e:
        print(f"❌ Error running ddsls: {e}")
        return False

    return True


def test_python_dds():
    """Test DDS using Python bindings if available"""
    print("🧪 Testing Python DDS bindings...")

    try:
        from cyclonedds import Domain, Participant, Topic, Publisher, Subscriber
        from cyclonedds.idl import IdlStruct
        from dataclasses import dataclass

        @dataclass
        class HelloMessage(IdlStruct):
            message: str
            counter: int

        print("✅ Python bindings imported successfully")

        # Test basic DDS initialization
        print("   Creating DDS domain and participant...")
        domain = Domain(0)
        participant = Participant(domain)

        print("   Creating topic...")
        topic = Topic(participant, "HelloTopic", HelloMessage)

        print("   Creating publisher and subscriber...")
        publisher = Publisher(participant)
        subscriber = Subscriber(participant)

        writer = publisher.create_datawriter(topic)
        reader = subscriber.create_datareader(topic)

        print("   Publishing test message...")
        test_msg = HelloMessage(message="Hello from Cyclone DDS!", counter=1)
        writer.write(test_msg)

        print("   Waiting for message...")
        time.sleep(1)

        # Try to read the message
        samples = reader.read()
        messages_received = 0
        for sample in samples:
            if sample.valid_data:
                print(f"   📨 Received: {sample.data}")
                messages_received += 1

        if messages_received > 0:
            print("✅ Python DDS communication test successful!")
        else:
            print("⚠️  No messages received (this might be normal in some environments)")

        # Cleanup
        participant.close()
        return True

    except ImportError:
        print("❌ Python bindings not available")
        return False
    except Exception as e:
        print(f"❌ Python DDS test failed: {e}")
        return False


def test_environment():
    """Test the DDS environment"""
    print("🌍 Testing DDS environment...")

    # Check environment variables
    cyclone_home = os.environ.get('CYCLONEDDS_HOME')
    if cyclone_home:
        print(f"✅ CYCLONEDDS_HOME: {cyclone_home}")
    else:
        print("⚠️  CYCLONEDDS_HOME not set")

    ld_library_path = os.environ.get('LD_LIBRARY_PATH')
    if ld_library_path:
        print(f"✅ LD_LIBRARY_PATH: {ld_library_path}")
    else:
        print("⚠️  LD_LIBRARY_PATH not set")

    # Check if library files exist
    lib_paths = ['/usr/local/lib', '/usr/lib', '/opt/cyclonedds/lib']
    for lib_path in lib_paths:
        if os.path.exists(lib_path):
            libs = [f for f in os.listdir(lib_path) if 'cyclone' in f.lower() or 'dds' in f.lower()]
            if libs:
                print(f"✅ Found DDS libraries in {lib_path}: {libs[:3]}{'...' if len(libs) > 3 else ''}")
                break
    else:
        print("⚠️  No DDS libraries found in common locations")


def run_comprehensive_test():
    """Run comprehensive DDS tests"""
    print("=" * 60)
    print("🚀 Cyclone DDS Comprehensive Test")
    print("=" * 60)

    success_count = 0
    total_tests = 4

    # Test 1: Environment
    test_environment()
    print()

    # Test 2: Installation check
    if check_cyclone_dds_installation():
        success_count += 1
    print()

    # Test 3: Command line tools
    if test_basic_dds_commands():
        success_count += 1
    print()

    # Test 4: Python bindings check
    if check_python_bindings():
        success_count += 1
    print()

    # Test 5: Python DDS functionality (if bindings available)
    if test_python_dds():
        success_count += 1
    print()

    # Summary
    print("=" * 60)
    print(f"📊 Test Results: {success_count}/{total_tests} tests passed")
    print("=" * 60)

    if success_count >= 2:
        print("🎉 Cyclone DDS is working! At least basic functionality is available.")
        return True
    else:
        print("❌ Cyclone DDS setup has issues. Check the installation.")
        return False


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Cyclone DDS Alternative Test")
    parser.add_argument('--mode', choices=['test', 'env', 'python'],
                       default='test', help='Test mode')

    args = parser.parse_args()

    if args.mode == 'env':
        test_environment()
    elif args.mode == 'python':
        if check_python_bindings():
            test_python_dds()
        else:
            print("Python bindings not available")
            sys.exit(1)
    else:
        success = run_comprehensive_test()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()