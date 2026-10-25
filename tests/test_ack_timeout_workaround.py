"""The cyclonedds `wait_for_acks` workaround in `spatialdds_demo.lanes`, held to its reason.

Two checks. The bug is still in the installed cyclonedds, so the workaround
still has a reason to exist (when this fails, delete the workaround and this
file). And with it in place, a timeout is False, as documented, so the SDK's
`close()` finishes. The second needs a bus and a reader that cannot ack, so it
runs in the demo image (CYCLONEDDS_URI set) and skips elsewhere.
"""

import inspect
import os
import signal
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class AckTimeout(unittest.TestCase):
    def test_the_bug_is_still_there(self):
        from cyclonedds.pub import DataWriter

        from spatialdds_demo import lanes

        self.assertTrue(lanes.ACK_TIMEOUT_WORKAROUND)
        source = inspect.getsource(DataWriter.wait_for_acks._oarc_broken)
        self.assertIn("Exception.DDS_RETCODE_TIMEOUT", source,
                      "cyclonedds no longer has the wait_for_acks bug: remove "
                      "the workaround in spatialdds_demo/lanes.py and this test")

    def test_a_close_with_a_reader_that_cannot_ack_finishes(self):
        if not os.getenv("CYCLONEDDS_URI"):
            raise unittest.SkipTest("DDS-UNAVAILABLE: run in the demo image")
        import spatialdds as sd

        from spatialdds_demo import lanes  # noqa: F401  -- the workaround

        domain = 44
        topic = "spatialdds/ack/x/geopose/v1"
        child = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
            import time, spatialdds as sd
            p = sd.participant('ack', domain={domain})
            p.reader('geopose', topic='{topic}',
                     qos_profile=sd.dds.registry.profile('MAP_META'))
            print('ready', flush=True)
            time.sleep(60)
        """)], stdout=subprocess.PIPE, text=True)
        try:
            child.stdout.readline()
            p = sd.participant("ack", domain=domain, service_id="svc:ack-test")
            w = p.writer("geopose", topic=topic,
                         qos_profile=sd.dds.registry.profile("MAP_META"))
            self.assertTrue(w.wait_matched(timeout=10))
            os.kill(child.pid, signal.SIGSTOP)
            for i in range(200):
                w.write(sd.geopose(30.0 + i * 1e-4, -97.0))
            self.assertFalse(w._flush(0.5), "an unacked flush is False")
            p.close(flush_s=0.5)          # raised AttributeError without it
        finally:
            child.kill()
            child.wait()


if __name__ == "__main__":
    unittest.main()
