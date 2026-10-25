"""
Bidirectional interoperability between a 1.7 participant and the 1.8 demo.

The driver of the cross-era exhibit; see README.md beside it. Drives
`interop_probe.py` — built only from the 1.7 `spatialdds_idl` subset in this
directory, the spec's topic names and the §3.3.3 profile table, with no demo
code — against the demo, both ways:

* the probe reads the demo's announces and one announced data lane;
* the demo discovers the probe, refuses the lane it announces by rule and
  records why, and sees it depart. The probe's lane name was never a §3.3.1
  name (README.md, "The lane's name").

Needs a DDS domain, so it skips loudly where CycloneDDS cannot create a
participant. Run it in the demo image:

    docker run --rm -v "$PWD:/app" -w /app -e PYTHONPATH=/app \\
        -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \\
        python3 -m unittest tests.cross_era.interop_driver
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

EXHIBIT = Path(__file__).resolve().parent
PROBE = EXHIBIT / "interop_probe.py"
DOMAIN = int(os.getenv("SPATIALDDS_INTEROP_DOMAIN", "31"))


def _require_dds():
    """
    An SDK participant, or a loud skip.

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
        import spatialdds as sd

        return sd.participant("cross-era", domain=DOMAIN)
    except Exception as exc:                           # pragma: no cover
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


def _subprocess_env() -> dict:
    """The demo's side runs as its own process; it needs the repo importable."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (f"{_REPO}{os.pathsep}{existing}" if existing
                         else str(_REPO))
    return env


def _probe_env() -> dict:
    """
    The probe's path is this directory and nothing else.

    So `spatialdds_idl` is the frozen 1.7 subset beside it, and anything the
    probe tried to take from the demo would fail to import rather than quietly
    succeed. "No demo code" is enforced by the import system, not by a
    convention the probe's author has to keep.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(EXHIBIT)
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
        _require_dds().close()      # only the skip: this half is two processes
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
                cwd=str(EXHIBIT), capture_output=True, text=True, timeout=120,
                env=_probe_env())
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
        The demo discovers a service it has never heard of, refuses the lane
        that service announces because its name is not a §3.3.1 name, says
        so, and learns the service left.

        Until the SDK adoption the demo read that lane: its old transport
        opened any name. The SDK holds names to the grammar, and this one is
        six segments with no §2 template, so it was never valid in 1.7 or
        1.8. The refusal must name the grammar, not the type: the type it
        announces still resolves.
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
                cwd=str(EXHIBIT), capture_output=True, text=True, timeout=120,
                env=_probe_env())
            time.sleep(2.0)
        finally:
            stop.set()
            thread.join(timeout=3)

        self.assertEqual(probe.returncode, 0,
                         f"probe failed:\n{probe.stdout}\n{probe.stderr}")
        self.assertIn("svc:interop-probe", services,
                      "demo did not discover the probe")
        import spatialdds as sd

        lane = "spatialdds/interop_probe/geo/ego/pose/v1"
        self.assertIn(lane, sub.skipped,
                      f"demo did not record refusing the probe's lane; "
                      f"skipped={sub.skipped}")
        self.assertIn("not a §3.3.1 name", sub.skipped[lane])
        self.assertIsNotNone(sd.dds.class_for("geopose"),
                             "the probe's announced type should resolve")
        self.assertFalse([s for s in samples if "interop_probe" in s[1]],
                         "demo read a lane the SDK should have refused")
        self.assertIn("svc:interop-probe", departed,
                      "demo did not see the probe depart")
        participant.close()


if __name__ == "__main__":
    unittest.main()
