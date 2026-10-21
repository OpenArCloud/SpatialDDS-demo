"""
§3.3.3's deadline is load-bearing: omitting it silently breaks a match.

Found by the interop probe, and pinned here on its own. It lived in the
probe's driver until 2026-10-21, when that driver was frozen as part of the
cross-era exhibit (tests/cross_era/); this is a property of 1.8 QoS, not of
the exhibit, so it stays an ordinary test that can change with the demo.

Needs a DDS domain, so it skips loudly where CycloneDDS cannot create a
participant. Run it in the demo image:

    docker run --rm -v "$PWD:/app" -w /app -e PYTHONPATH=/app \
        -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \
        python3 -m unittest tests.test_deadline_qos
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DOMAIN = int(os.getenv("SPATIALDDS_INTEROP_DOMAIN", "31"))


def _require_dds():
    """A participant, or a loud skip. Gated on CYCLONEDDS_URI as the probe's
    driver is: a host with no usable networking config fails late otherwise."""
    if not os.getenv("CYCLONEDDS_URI"):
        raise unittest.SkipTest(
            "DDS-UNAVAILABLE: CYCLONEDDS_URI is unset — run this in the "
            "demo image (see the module docstring)")
    try:
        from cyclonedds.domain import DomainParticipant

        return DomainParticipant(DOMAIN)
    except Exception as exc:                           # pragma: no cover
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


class DeadlineIsLoadBearing(unittest.TestCase):
    """
    §3.3.3's deadline column is not advisory, and the spec does not say so.

    The table heads the column "**Typical** Deadline" and the notes add
    "implementations may tune low-level DDS settings, but the profile name
    is canonical". Read plainly, that is permission to leave the deadline
    unset. But Deadline is a *request/offered* QoS in DDS: a reader
    requesting 33 ms does not match a writer that offers none, and the
    failure is silent — no error, no warning, no data.

    Two implementations both following the spec table can therefore fail to
    communicate. The interop probe hit exactly this before its profile table
    included the deadlines. This test pins the behaviour so the finding does
    not get lost.
    """

    def _exchange(self, writer_qos, topic: str) -> int:
        from cyclonedds.domain import DomainParticipant
        from cyclonedds.pub import DataWriter
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic

        from spatialdds_demo import qos_profiles
        from spatialdds_idl18.builtin import Time
        from spatialdds_idl18.spatial.core import CovMatrix, GeoPose

        pub, sub = DomainParticipant(DOMAIN + 1), DomainParticipant(DOMAIN + 1)
        writer = DataWriter(pub, Topic(pub, topic, GeoPose), qos=writer_qos)
        reader = DataReader(sub, Topic(sub, topic, GeoPose),
                            qos=qos_profiles.qos_for("POSE_RT"))
        time.sleep(2.0)
        for _ in range(5):
            writer.write(GeoPose(lat_deg=1.0, lon_deg=2.0, alt_m=3.0,
                                 q=[0.0, 0.0, 0.0, 1.0],
                                 stamp=Time(sec=1, nanosec=0),
                                 cov=CovMatrix(none=0)))
            time.sleep(0.1)
        time.sleep(1.5)
        return len(reader.take(N=32) or [])

    def test_omitting_the_deadline_silently_breaks_the_match(self):
        _require_dds()
        from cyclonedds.core import Policy, Qos

        base = (Policy.Reliability.BestEffort, Policy.Durability.Volatile,
                Policy.History.KeepLast(1))
        without = self._exchange(Qos(*base), "test/interop/no_deadline/v1")
        with_it = self._exchange(Qos(*base, Policy.Deadline(33_000_000)),
                                 "test/interop/deadline/v1")
        self.assertEqual(without, 0,
                         "expected the no-deadline writer to be incompatible")
        self.assertGreater(with_it, 0,
                           "expected the deadline-matching writer to connect")


if __name__ == "__main__":
    unittest.main()
