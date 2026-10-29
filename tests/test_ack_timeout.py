"""A clean stop with a peer that cannot ack finishes, on the SDK alone.

cyclonedds-python 11.0.1's `DataWriter.wait_for_acks` raises AttributeError
where it is documented to return False (`pub.py:325`), so the SDK's
`close()`, which awaits acks after each dispose and Depart, exited with a
traceback and skipped its remaining flushes. This repository patched
cyclonedds in `spatialdds_demo.lanes` until spatialdds 0.7.4, which reads
that AttributeError as the timeout it is (spatialdds-py S17). The patch is
gone, and these hold the SDK to the fix: nothing here patches cyclonedds,
and an unacked flush is False and `close()` finishes. The second needs a bus
and a reader that cannot ack, so it runs in the demo image (CYCLONEDDS_URI
set) and skips elsewhere.
"""

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
    def test_nothing_here_patches_cyclonedds(self):
        from cyclonedds.pub import DataWriter

        from spatialdds_demo import lanes  # noqa: F401  -- what used to patch

        self.assertFalse(hasattr(DataWriter.wait_for_acks, "_oarc_workaround"),
                         "cyclonedds is patched again: the SDK owns this "
                         "(spatialdds 0.7.4, S17)")

    def test_a_close_with_a_reader_that_cannot_ack_finishes(self):
        if not os.getenv("CYCLONEDDS_URI"):
            raise unittest.SkipTest("DDS-UNAVAILABLE: run in the demo image")
        import spatialdds as sd

        from spatialdds_demo import lanes  # noqa: F401  -- as every process does

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
            p.close(flush_s=0.5)          # raised AttributeError until 0.7.4
        finally:
            child.kill()
            child.wait()


if __name__ == "__main__":
    unittest.main()
