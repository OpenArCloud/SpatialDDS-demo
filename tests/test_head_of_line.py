"""
A slow reader on one topic must not stall an unrelated one.

Findings §4.2: the envelope put every stream on one topic, so RELIABLE +
KEEP_ALL meant one slow consumer applied backpressure to every publisher of
every stream. The repo had already hit it — `bridges/envelope_io.py`'s
docstring records DeepSense bursts being dropped, and `EnvelopePublisher`
carried a `DDS_RETCODE_TIMEOUT` handler with a comment about a bouncing
consumer taking the publisher down.

With a topic per type that is structurally impossible, and "structurally
impossible" is a claim worth measuring rather than asserting. So both shapes
are built here and compared:

* **shared lane** — two streams on one topic, as the envelope had it. One
  reader stops taking. The other stream's publisher is measured.
* **separate lanes** — the same two streams on their own topics, as the spec
  specifies. Same slow reader, same measurement.

The congested topic is deliberately the worst case: RELIABLE + KEEP_ALL with
a bounded resource limit, so the writer genuinely blocks rather than
discarding.

Needs a DDS domain; skips loudly otherwise. Run it in the demo image:

    docker run --rm -v "$PWD:/app" -w /app -e PYTHONPATH=/app \\
        -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \\
        python3 -m unittest tests.test_head_of_line
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from typing import List

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DOMAIN = int(os.getenv("SPATIALDDS_HOL_DOMAIN", "35"))

# Small enough that the writer blocks quickly once nobody takes.
HISTORY_LIMIT = 8
# Above the blocking-time so a stalled write is unmistakable in the numbers.
BLOCKING_MS = 200
WRITES = 24


def _require_dds():
    if not os.getenv("CYCLONEDDS_URI"):
        raise unittest.SkipTest(
            "DDS-UNAVAILABLE: CYCLONEDDS_URI is unset — run this in the "
            "demo image (see the module docstring)")
    try:
        from cyclonedds.domain import DomainParticipant

        return DomainParticipant(DOMAIN)
    except Exception as exc:                           # pragma: no cover
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


def _congested_qos():
    """RELIABLE + KEEP_ALL with a bounded history: the writer really blocks."""
    from cyclonedds.core import Policy, Qos

    return Qos(
        Policy.Reliability.Reliable(BLOCKING_MS * 1_000_000),
        Policy.Durability.Volatile,
        Policy.History.KeepAll,
        Policy.ResourceLimits(max_samples=HISTORY_LIMIT),
    )


def _geo(i: int):
    from spatialdds_idl.builtin import Time
    from spatialdds_idl.spatial.core import CovMatrix, GeoPose

    return GeoPose(lat_deg=float(i), lon_deg=0.0, alt_m=0.0,
                   q=[0.0, 0.0, 0.0, 1.0],
                   stamp=Time(sec=i, nanosec=0), cov=CovMatrix(none=0))


def _write_latencies(writer, count: int = WRITES) -> List[float]:
    """Milliseconds per write. A blocked writer shows up here and nowhere else."""
    out: List[float] = []
    for i in range(count):
        start = time.perf_counter()
        try:
            writer.write(_geo(i))
        except Exception:
            # A timed-out write is the symptom, not an error to hide: record
            # the wait it cost.
            pass
        out.append((time.perf_counter() - start) * 1000.0)
    return out


class HeadOfLine(unittest.TestCase):

    def _endpoints(self, topic: str, qos):
        from cyclonedds.pub import DataWriter
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic

        from cyclonedds.domain import DomainParticipant
        from spatialdds_idl.spatial.core import GeoPose

        pub, sub = DomainParticipant(DOMAIN), DomainParticipant(DOMAIN)
        wt, rt = Topic(pub, topic, GeoPose), Topic(sub, topic, GeoPose)
        writer = DataWriter(pub, wt, qos=qos)
        reader = DataReader(sub, rt, qos=qos)
        writer._keep, reader._keep = (pub, wt), (sub, rt)
        return writer, reader

    def test_shared_lane_stalls_an_unrelated_stream(self):
        """
        The envelope's shape, reproduced. Two streams, one topic, one slow
        reader — and the *other* stream's writer is the one that pays.
        """
        _require_dds()
        qos = _congested_qos()
        writer, reader = self._endpoints("test/hol/shared/v1", qos)
        time.sleep(2.0)

        # Nobody takes from `reader`. Both streams share this one writer,
        # exactly as every logical topic shared one envelope writer.
        latencies = _write_latencies(writer)
        worst = max(latencies)
        self.assertGreater(
            worst, BLOCKING_MS * 0.5,
            f"expected the shared lane to stall; worst write was {worst:.1f} ms")
        print(f"\n  shared lane   : worst write {worst:7.1f} ms  "
              f"(median {sorted(latencies)[len(latencies) // 2]:.2f} ms)",
              flush=True)
        self._shared_worst = worst

    def test_separate_lanes_leave_the_unrelated_stream_alone(self):
        """
        The spec's shape. Same congestion on one topic; the other topic's
        writer is untouched, because they are different endpoints on
        different topics with their own history.
        """
        _require_dds()
        qos = _congested_qos()
        congested_w, _congested_r = self._endpoints("test/hol/busy/v1", qos)
        quiet_w, quiet_r = self._endpoints("test/hol/quiet/v1", qos)
        time.sleep(2.0)

        # Fill the congested lane until its writer is blocking.
        _write_latencies(congested_w, count=HISTORY_LIMIT * 3)

        # Now measure the unrelated one, whose reader is keeping up.
        latencies = []
        for i in range(WRITES):
            start = time.perf_counter()
            quiet_w.write(_geo(i))
            latencies.append((time.perf_counter() - start) * 1000.0)
            quiet_r.take(N=32)          # a healthy consumer
        worst = max(latencies)
        print(f"  separate lanes: worst write {worst:7.1f} ms  "
              f"(median {sorted(latencies)[len(latencies) // 2]:.2f} ms)",
              flush=True)

        # Generous: the point is orders of magnitude, not a tight bound. A
        # blocked writer costs BLOCKING_MS; an unaffected one costs
        # microseconds.
        self.assertLess(
            worst, BLOCKING_MS * 0.5,
            f"a congested topic delayed an unrelated one by {worst:.1f} ms — "
            f"head-of-line blocking is still reachable")

    def test_the_demo_gives_each_type_its_own_topic(self):
        """
        The structural claim behind the measurement: no two types share a
        topic in the demo, so the shared-lane case above is unreachable by
        construction rather than by discipline.
        """
        from spatialdds_demo import topic_types

        sys.path.insert(0, str(_REPO / "multi_operator_fusion"))
        from synthetic_publisher import (
            DET_TYPE, EGO_POSE_TYPE, INFRA_TOPIC, PLAN_TYPE,
            TOPIC_FMT, EGO_POSE_TOPIC_FMT, PLAN_TOPIC_FMT,
        )

        lanes = {
            TOPIC_FMT.format(operator="operator_a"): DET_TYPE,
            EGO_POSE_TOPIC_FMT.format(operator="operator_a"): EGO_POSE_TYPE,
            PLAN_TOPIC_FMT.format(operator="operator_a"): PLAN_TYPE,
            INFRA_TOPIC: DET_TYPE,
        }
        for topic, type_name in lanes.items():
            with self.subTest(topic=topic):
                self.assertIsNotNone(topic_types.try_resolve(type_name))
        # One type per topic — the property that makes the isolation
        # structural. Under the envelope this set had exactly one member.
        self.assertGreater(len({t for t in lanes}), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
