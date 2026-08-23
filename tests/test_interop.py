"""
Bidirectional interoperability with an independently-built participant.

Drives `tests/interop_probe.py` — which is built only from `spatialdds_idl`,
the spec's topic names and the §3.3.3 profile table, with no demo transport
code — against the demo, both ways:

* the probe reads the demo's announces and one announced data lane;
* the demo discovers the probe, resolves its announced type, reads its data,
  and sees it depart.

Needs a DDS domain, so it skips loudly where CycloneDDS cannot create a
participant. Run it in the demo image:

    docker run --rm -v "$PWD:/app" -w /app -e PYTHONPATH=/app \\
        -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \\
        python3 -m unittest tests.test_interop
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

PROBE = _REPO / "tests" / "interop_probe.py"
DOMAIN = int(os.getenv("SPATIALDDS_INTEROP_DOMAIN", "31"))


def _require_dds():
    """
    A participant, or a loud skip.

    Gated on ``CYCLONEDDS_URI`` as well as on the import: cyclonedds will
    happily build a participant on a host with no usable networking config,
    and these tests then fail late and confusingly rather than skipping. The
    demo image sets it; a developer laptop does not.
    """
    if not os.getenv("CYCLONEDDS_URI"):
        raise unittest.SkipTest(
            "DDS-UNAVAILABLE: CYCLONEDDS_URI is unset — run this in the "
            "demo image (see the module docstring)")
    try:
        from cyclonedds.domain import DomainParticipant

        return DomainParticipant(DOMAIN)
    except Exception as exc:                           # pragma: no cover
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


def _subprocess_env() -> dict:
    """The probe runs as its own process; it needs the repo importable."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (f"{_REPO}{os.pathsep}{existing}" if existing
                         else str(_REPO))
    return env


class Interop(unittest.TestCase):
    """
    Findings §4.1 said the demo could not interoperate with any conformant
    implementation. These convert that from inferred to measured, and the
    direction that matters is that they could fail: the probe shares no
    transport code with the demo, so a passing run means the *spec* is what
    the two have in common.
    """

    def test_probe_reads_the_demo(self):
        _require_dds()
        publisher = subprocess.Popen(
            [sys.executable, "-m", "multi_operator_fusion.synthetic_publisher",
             "--domain", str(DOMAIN), "--operators", "2", "--rate", "5",
             "--max-frames", "300", "--quiet"],
            cwd=str(_REPO), stderr=subprocess.DEVNULL,
            env=_subprocess_env())
        try:
            time.sleep(4.0)
            probe = subprocess.run(
                [sys.executable, str(PROBE), "--domain", str(DOMAIN),
                 "--receive-only", "--timeout", "15"],
                cwd=str(_REPO), capture_output=True, text=True, timeout=120,
                env=_subprocess_env())
        finally:
            publisher.terminate()
            try:
                publisher.wait(timeout=8)
            except Exception:
                publisher.kill()

        self.assertEqual(probe.returncode, 0,
                         f"probe failed:\n{probe.stdout}\n{probe.stderr}")
        self.assertIn("received", probe.stdout)
        self.assertIn("read", probe.stdout)

    def test_demo_reads_the_probe(self):
        """
        The demo discovers a service it has never heard of, resolves the
        type that service announces, opens a reader and reads its data —
        then learns it left.
        """
        participant = _require_dds()
        from spatialdds_demo.stream import StreamSubscriber

        services, samples, departed = [], [], []
        sub = StreamSubscriber(
            participant,
            lambda t, topic, p, ns: samples.append((t, topic, p)),
            on_announce=lambda sid, a: services.append(sid),
            on_depart=lambda sid: departed.append(sid),
        )
        stop = threading.Event()

        def pump():
            while not stop.is_set():
                sub.poll()
                stop.wait(0.02)

        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        try:
            time.sleep(2.0)
            probe = subprocess.run(
                [sys.executable, str(PROBE), "--domain", str(DOMAIN),
                 "--publish-only", "--publish-seconds", "4"],
                cwd=str(_REPO), capture_output=True, text=True, timeout=120,
                env=_subprocess_env())
            time.sleep(2.0)
        finally:
            stop.set()
            thread.join(timeout=3)

        self.assertEqual(probe.returncode, 0,
                         f"probe failed:\n{probe.stdout}\n{probe.stderr}")
        self.assertIn("svc:interop-probe", services,
                      "demo did not discover the probe")
        probe_samples = [s for s in samples if "interop_probe" in s[1]]
        self.assertTrue(probe_samples, "demo read no data from the probe")
        self.assertEqual(probe_samples[0][0], "geopose")
        self.assertAlmostEqual(probe_samples[0][2]["lon_deg"], -122.4194,
                               places=4)
        self.assertIn("svc:interop-probe", departed,
                      "demo did not see the probe depart")


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
        from spatialdds_idl.builtin import Time
        from spatialdds_idl.spatial.core import CovMatrix, GeoPose

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
