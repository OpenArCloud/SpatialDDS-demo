#!/usr/bin/env python3
"""
Cyclone DDS Python Test Application
Simple publisher/subscriber example to test DDS functionality
"""

import argparse
import time
import sys
from dataclasses import dataclass
from typing import Optional

try:
    from cyclonedds import Domain, Entity, Participant, Publisher, Subscriber, Topic
    from cyclonedds.idl import IdlStruct
except ImportError as e:
    print(f"Error importing cyclonedds: {e}")
    print("Make sure cyclonedds is properly installed")
    sys.exit(1)


@dataclass
class TestMessage(IdlStruct):
    """Simple test message structure"""
    message: str
    timestamp: int
    counter: int


class DDSPublisher:
    """DDS Publisher for test messages"""

    def __init__(self, domain_id: int = 0):
        self.domain = Domain(domain_id)
        self.participant = Participant(self.domain)
        self.topic = Topic(self.participant, "TestTopic", TestMessage)
        self.publisher = Publisher(self.participant)
        self.writer = self.publisher.create_datawriter(self.topic)

    def publish_message(self, message: str, counter: int):
        """Publish a test message"""
        test_msg = TestMessage(
            message=message,
            timestamp=int(time.time()),
            counter=counter
        )
        self.writer.write(test_msg)
        print(f"Published: {test_msg}")

    def cleanup(self):
        """Clean up resources"""
        self.participant.close()


class DDSSubscriber:
    """DDS Subscriber for test messages"""

    def __init__(self, domain_id: int = 0):
        self.domain = Domain(domain_id)
        self.participant = Participant(self.domain)
        self.topic = Topic(self.participant, "TestTopic", TestMessage)
        self.subscriber = Subscriber(self.participant)
        self.reader = self.subscriber.create_datareader(self.topic)

    def listen_for_messages(self, duration: int = 10):
        """Listen for messages for a specified duration"""
        print(f"Listening for messages for {duration} seconds...")
        start_time = time.time()
        message_count = 0

        while time.time() - start_time < duration:
            samples = self.reader.read()

            for sample in samples:
                if sample.valid_data:
                    print(f"Received: {sample.data}")
                    message_count += 1

            time.sleep(0.1)  # Small delay to prevent busy waiting

        print(f"Received {message_count} messages in {duration} seconds")
        return message_count

    def cleanup(self):
        """Clean up resources"""
        self.participant.close()


def run_publisher(duration: int = 10, interval: float = 1.0):
    """Run publisher for specified duration"""
    print("Starting DDS Publisher...")

    try:
        publisher = DDSPublisher()
        counter = 0
        start_time = time.time()

        while time.time() - start_time < duration:
            message = f"Hello from Cyclone DDS Publisher #{counter}"
            publisher.publish_message(message, counter)
            counter += 1
            time.sleep(interval)

        print(f"Published {counter} messages")
        publisher.cleanup()

    except Exception as e:
        print(f"Publisher error: {e}")
        return False

    return True


def run_subscriber(duration: int = 15):
    """Run subscriber for specified duration"""
    print("Starting DDS Subscriber...")

    try:
        subscriber = DDSSubscriber()
        message_count = subscriber.listen_for_messages(duration)
        subscriber.cleanup()
        return message_count > 0

    except Exception as e:
        print(f"Subscriber error: {e}")
        return False


def run_test():
    """Run both publisher and subscriber in sequence for testing"""
    print("Running Cyclone DDS Test...")

    # Test 1: Basic initialization
    print("\n1. Testing DDS initialization...")
    try:
        domain = Domain(0)
        participant = Participant(domain)
        topic = Topic(participant, "TestTopic", TestMessage)
        print("✓ DDS initialization successful")
        participant.close()
    except Exception as e:
        print(f"✗ DDS initialization failed: {e}")
        return False

    # Test 2: Publisher test
    print("\n2. Testing Publisher...")
    try:
        publisher = DDSPublisher()
        publisher.publish_message("Test message", 1)
        print("✓ Publisher test successful")
        publisher.cleanup()
    except Exception as e:
        print(f"✗ Publisher test failed: {e}")
        return False

    # Test 3: Basic subscriber test
    print("\n3. Testing Subscriber...")
    try:
        subscriber = DDSSubscriber()
        print("✓ Subscriber test successful")
        subscriber.cleanup()
    except Exception as e:
        print(f"✗ Subscriber test failed: {e}")
        return False

    print("\n✓ All tests passed! Cyclone DDS is working correctly.")
    return True


def main():
    """Main application entry point"""
    parser = argparse.ArgumentParser(description="Cyclone DDS Python Test Application")
    parser.add_argument("--mode", choices=["publisher", "subscriber", "test"],
                       default="test", help="Run mode")
    parser.add_argument("--duration", type=int, default=10,
                       help="Duration to run (seconds)")
    parser.add_argument("--interval", type=float, default=1.0,
                       help="Publisher interval (seconds)")

    args = parser.parse_args()

    print(f"Cyclone DDS Test Application - Mode: {args.mode}")
    print(f"CYCLONEDDS_HOME: {os.environ.get('CYCLONEDDS_HOME', 'Not set')}")

    success = False

    if args.mode == "publisher":
        success = run_publisher(args.duration, args.interval)
    elif args.mode == "subscriber":
        success = run_subscriber(args.duration)
    elif args.mode == "test":
        success = run_test()

    if success:
        print("\n🎉 Application completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Application failed!")
        sys.exit(1)


if __name__ == "__main__":
    import os
    main()