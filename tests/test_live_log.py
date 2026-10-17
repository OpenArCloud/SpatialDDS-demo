"""A long-lived demo process must be readable while it is still running.

The defect this guards was found by running the demo the way the docs say to
run it, in the demo image with stdout captured, rather than in a terminal.
`python3 -m spatialdds_demo.model_service` emitted **zero bytes for its entire
life**: Python block-buffers stdout whenever it is not a TTY, so the banner
naming the domain, the type source, the scene and every topic -- and every
line of the command lane after it -- arrived only when the process was killed.
Measured at 0 bytes after 8 seconds, 1354 after SIGTERM.

In a terminal it looked perfect, because an interactive stdout is
line-buffered already. That is the whole hazard: the failure was invisible in
the shape a developer checks and total in the shape the demo ships in.

These tests use a pipe and no DDS, so they run wherever tests run rather than
only in the image where the defect was found. A test that can only run in the
environment that is hardest to reach is a test that stops running.
"""
from __future__ import annotations

import select
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# How long a reader should have to wait for a line from a process that is
# still running. Generous against a loaded machine; the defect this catches
# was unbounded.
PATIENCE = 6.0
# How long the subprocess stays alive after printing. Must be comfortably
# longer than PATIENCE, or "the line arrived in time" and "the process
# exited and flushed" stop being distinguishable -- which is how the first
# version of this file fooled itself.
SLEEP = 30


def _first_line_while_running(setup: str) -> tuple[str, bool, float]:
    """Run `setup`, print a line, then sleep. Return the line, liveness, wait.

    The sleep is the point: the process must still be alive when the line is
    read. Reading output from a process that has exited proves nothing, since
    exit flushes the buffer -- which is exactly how the original defect hid.

    **The deadline is `select`, not a loop around `readline`.** The first
    version of this helper looped on `readline()` with a wall-clock check,
    which is not a timeout at all: a pipe read blocks, so it sat through the
    subprocess's whole 30-second sleep and collected the line at exit. Both
    tests below then passed, one of them for precisely the reason it exists to
    rule out. `select` answers the question actually being asked -- is there
    anything to read *yet* -- and the elapsed time is returned so the
    assertion can be about promptness rather than arrival.
    """
    code = (f"import sys, time\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            f"{setup}\n"
            f"print('BANNER: the service is up')\n"
            f"time.sleep({SLEEP})\n")
    proc = subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True)
    try:
        started = time.time()
        ready, _, _ = select.select([proc.stdout], [], [], PATIENCE)
        waited = time.time() - started
        line = proc.stdout.readline() if ready else ""
        alive = proc.poll() is None
        return line.strip(), alive, waited
    finally:
        proc.kill()
        proc.wait(timeout=10)


class LiveLog(unittest.TestCase):
    def test_a_banner_is_readable_before_the_process_ends(self):
        line, alive, waited = _first_line_while_running(
            "from spatialdds_demo.model_service import live_log; live_log()")
        self.assertTrue(alive, "the process should still be running")
        self.assertEqual(line, "BANNER: the service is up")
        # Promptly, not eventually. Anything near SLEEP means the line was
        # flushed by the process ending, which is the defect, not the fix.
        self.assertLess(waited, PATIENCE)
        print(f"\n  live_log: banner readable after {waited:.3f}s, "
              f"with the writer still alive and {SLEEP}s left to sleep")

    def test_without_it_the_same_line_is_invisible(self):
        """The injection test: the fix has to be the reason, not the harness.

        Without `live_log()` this reads nothing at all, which is the defect
        reproduced. If this test ever passes an assertion saying the line
        *arrived*, the mechanism being tested is no longer the one described.
        """
        line, alive, waited = _first_line_while_running("pass")
        self.assertTrue(alive, "the process should still be running")
        self.assertEqual(line, "", "a block-buffered pipe should yield nothing "
                                   "while the writer is alive -- if this got a "
                                   "line, stdout was already line-buffered and "
                                   "the other test proves nothing")
        self.assertGreaterEqual(waited, PATIENCE,
                                "select should have waited out the full "
                                "deadline with nothing to read")
        print(f"  live_log: without it, nothing in {waited:.1f}s — "
              f"defect reproduced")

    def test_it_reports_whether_it_changed_anything(self):
        from spatialdds_demo.model_service import live_log
        # Under a pipe or a captured stdout it has work to do; called twice,
        # the second call has nothing left to change. Both answers are
        # truthful, which is what lets a caller log the difference.
        first = live_log()
        second = live_log()
        self.assertFalse(second, "the second call changed nothing, and says so")
        self.assertIsInstance(first, bool)

    def test_every_long_lived_owm_entry_point_calls_it(self):
        """The three processes that stay alive, by name rather than by habit.

        A fix applied to one of three is the same defect with a smaller
        footprint, and the next long-lived entry point is the one that gets
        forgotten. `robot_bridge.py` is deliberately absent: it is still
        pre-1.8 and belongs to a later batch, which is where its own fix goes.
        """
        for mod, func in (("model_service", "def run_server("),
                          ("pond_watch", "def run("),
                          ("duck_mover", "def run(")):
            src = (REPO / "spatialdds_demo" / f"{mod}.py").read_text()
            start = src.index(func)
            # the first 12 lines of the entry point, where setup belongs
            head = "\n".join(src[start:].splitlines()[:12])
            self.assertIn("live_log()", head,
                          f"{mod}.py's long-lived entry point does not make "
                          f"its stdout readable while it runs")


if __name__ == "__main__":
    unittest.main()
