#!/usr/bin/env python3
"""
The spec for examples/world_from_curl.sh: populate, watch, lapse, withdraw.

The example is run as written, against `python -m spatialdds.serve` serving
this repository's OWM 0.2 relationship type (`--extension
spatialdds_demo.lanes`) on a DDS domain of its own, with two watchers: the
example's own WebSocket one (examples/watch_world.py), and a DDS reader in
this process opened the way the venue's consumers open the model lane
(`MODEL_LATCHED`) and the relationship lane (the demo's `Relationship`). What
the curl client wrote has to reach both as ordinary samples, and every
removal has to arrive as the writer's own: a terminal sample with its reason,
then the dispose.

* **populate**: the declared bench and the observed cyclist are accepted, the
  cyclist only with `?refresh=` (without it, `refresh-required`), and the
  relationship's empty asserter comes back as the token's principal;
* **watch**: the socket and the bus each see all three;
* **refresh**: the cyclist, re-PUT inside its interval, is never withdrawn
  while its client is refreshing;
* **lapse**: once the client goes quiet it is withdrawn inside the window,
  RETIRED "refresh lapsed" then its dispose, on the socket and on the bus,
  and the bench under the same silence is still served;
* **withdraw**: each DELETE arrives with its own reason, both places.

The gateway needs spatialdds 0.8.0 or later; this repository pins 0.7.4
(requirements.txt) for its own stack. The gateway's interpreter is
`SPATIALDDS_GATEWAY_PYTHON`, or this one. When it has an older spatialdds the
whole spec skips and says so, and it arms itself on the day the pin moves.
"""
import ast
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import spatialdds as sd  # noqa: E402

from spatialdds_demo.lanes import MODEL_LATCHED, RELATIONSHIP  # noqa: E402
from spatialdds_demo_idl18.oarc_owm import Relationship  # noqa: E402

EXAMPLE = REPO / "examples" / "world_from_curl.sh"
WATCHER = REPO / "examples" / "watch_world.py"
GATEWAY_PY = os.environ.get("SPATIALDDS_GATEWAY_PYTHON") or sys.executable
SCENE = "curl-world"
REFRESH = 2.0
ROUNDS = 3
#: How late a lapse may land and still be "inside the window": the gateway's
#: reaper period plus a margin for a loaded machine.
SLACK = 1.0


def gateway_version() -> str:
    try:
        return subprocess.run(
            [GATEWAY_PY, "-c", "import spatialdds; print(spatialdds.__version__)"],
            capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def at_least_080(v: str) -> bool:
    try:
        return tuple(int(x) for x in v.split(".")[:3]) >= (0, 8, 0)
    except ValueError:
        return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_serving(base: str, proc: subprocess.Popen, timeout: float = 60) -> None:
    from urllib.error import HTTPError
    from urllib.request import urlopen
    end = time.monotonic() + timeout
    while time.monotonic() < end and proc.poll() is None:
        try:
            urlopen(base + "/", timeout=1).close()
            return
        except HTTPError:
            return                       # it answered: it is serving
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"the gateway never served at {base}")


class Bus:
    """The venue's way onto the gateway's domain: one reader per lane."""

    def __init__(self, domain: int) -> None:
        self.p = sd.participant("curl-world-watch", domain=domain)
        self.readers = {
            "entity": self.p.reader("model_entity", qos_profile=MODEL_LATCHED,
                                    topic=sd.dds.topic_name(SCENE, "world",
                                                            "entity")),
            "relationship": self.p.reader(
                Relationship, qos_profile=MODEL_LATCHED,
                topic=sd.dds.topic_name(SCENE, "world", RELATIONSHIP)),
        }
        self.events = []        # (t, lane, kind, key, state, reason)

    def drain(self) -> None:
        for lane, r in self.readers.items():
            for ev in r.take():
                if isinstance(ev, sd.Sample):
                    self.events.append((time.monotonic(), lane, "sample", ev.key,
                                        ev.data.state.name, ev.data.state_reason))
                elif isinstance(ev, sd.Dispose):
                    self.events.append((time.monotonic(), lane, "dispose", ev.key,
                                        "", ev.reason or ""))

    def close(self) -> None:
        self.p.close()


def parse_watch(lines):
    """examples/watch_world.py's lines, as (t, lane, kind, key, state, reason)."""
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) < 4 or parts[2] not in ("sample", "dispose"):
            continue
        t, lane, kind, key = float(parts[0].rstrip("s")), parts[1], parts[2], parts[3]
        rest = line.split(key, 1)[1]
        state = rest.split("state=", 1)[1].split()[0] if "state=" in rest else ""
        reason = ast.literal_eval(rest.split("reason=", 1)[1].split(" asserter=")[0].strip())
        out.append((t, lane, kind, key, state, reason))
    return out


class Journey(unittest.TestCase):
    """One run of the example; each test reads one stage of it."""

    #: Whether the gateway serves this repository's claim type.
    CLAIMS = True

    @classmethod
    def setUpClass(cls) -> None:
        v = gateway_version()
        if not at_least_080(v):
            raise unittest.SkipTest(
                f"the example needs a spatialdds>=0.8.0 gateway; {GATEWAY_PY} "
                f"has {v or 'none'} (requirements.txt pins 0.7.4). Set "
                f"SPATIALDDS_GATEWAY_PYTHON to an interpreter with 0.8.")
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        made = subprocess.run(
            [GATEWAY_PY, "-m", "spatialdds.tokens", "--file", str(tmp / "t.json"),
             "add", "curl-spec", "--provider", "example.org", "--scene", SCENE,
             "--may-create-scenes"], capture_output=True, text=True, env=env,
            timeout=60)
        token = next((w for w in made.stdout.split() if len(w) >= 30), "")
        if not token:
            raise RuntimeError(f"no token issued: {made.stderr[-300:]}")
        cls.principal = "curl-spec"
        domain = random.randint(150, 229)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        cls.gw = subprocess.Popen(
            [GATEWAY_PY, "-m", "spatialdds.serve", "--publish", "--tokens",
             str(tmp / "t.json"), "--dds-domain", str(domain), "--no-journal",
             "--port", str(port)]
            + (["--extension", "spatialdds_demo.lanes"] if cls.CLAIMS else []),
            stdout=open(tmp / "serve.log", "w"), stderr=subprocess.STDOUT,
            env=env)
        cls.bus = None
        cls.watch = None
        try:
            wait_serving(base, cls.gw)
            cls.bus = Bus(domain)
            run_env = {**os.environ, "SPATIALDDS_ENDPOINT": base,
                       "SPATIALDDS_TOKEN": token, "WORLD_SCENE": SCENE,
                       "WORLD_REFRESH": f"{REFRESH:g}",
                       "WORLD_ROUNDS": str(ROUNDS)}
            span = 2 + REFRESH / 2 * ROUNDS + REFRESH + 2 + 4
            cls.watch = subprocess.Popen(
                [sys.executable, str(WATCHER), f"{span:g}"], env=run_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(0.5)
            ex = subprocess.Popen(["bash", str(EXAMPLE)], env=run_env,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
            while ex.poll() is None:
                cls.bus.drain()
                time.sleep(0.05)
            cls.example = ex.stdout.read().splitlines()
            for _ in range(20):
                cls.bus.drain()
                time.sleep(0.05)
            out, _ = cls.watch.communicate(timeout=span + 30)
            # The watcher's clock starts at its own launch, the bus's is
            # this process's: each is read as intervals, never mixed.
            cls.socket = parse_watch(out.splitlines())
        finally:
            if cls.watch is not None and cls.watch.poll() is None:
                cls.watch.kill()
                cls.watch.wait()
            if cls.bus is not None:
                cls.bus.close()
            cls.gw.send_signal(signal.SIGTERM)
            try:
                cls.gw.wait(timeout=20)
            except subprocess.TimeoutExpired:
                cls.gw.kill()
                cls.gw.wait()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    # -- what the example said ------------------------------------------------

    def line(self, step: str, what: str) -> list:
        for ln in self.example:
            if ln.startswith(step) and what in ln:
                return ln[len(step):].replace(what, "", 1).split()
        self.fail(f"no {step!r} line for {what!r} in:\n" + "\n".join(self.example))

    def events(self, where: list, key: str) -> list:
        return [e for e in where if e[3] == key]


class WorldFromCurl(Journey):
    """The whole example, claims included."""

    def test_populate(self):
        self.assertEqual(self.line("populate", "bench (DECLARED)")[0], "201")
        no_refresh = self.line("populate", "cyclist (OBSERVED), no ?refresh=")
        self.assertEqual(no_refresh, ["400",
                                      "urn:spatialdds-web:problem:refresh-required"])
        self.assertEqual(self.line("populate",
                                   f"cyclist (OBSERVED), ?refresh={REFRESH:g}")[0],
                         "201")
        put = self.line("populate", "near: cyclist -> bench")
        self.assertEqual(put, ["201", f"asserter={self.principal!r}"])
        self.assertEqual(self.line("populate", "the relationship, as served"),
                         ["200", f"asserter={self.principal!r}"])

    def test_watch(self):
        for name, where in (("socket", self.socket), ("bus", self.bus.events)):
            for lane, key, state in (("entity", "ent:bench", "ACTIVE"),
                                     ("entity", "ent:cyclist", "ACTIVE"),
                                     ("relationship", "near-1", "CREATED")):
                with self.subTest(where=name, key=key):
                    self.assertIn((lane, "sample", key, state, ""),
                                  [e[1:] for e in where],
                                  f"{name} never saw {key} {state}")

    def test_refresh(self):
        for name, where in (("socket", self.socket), ("bus", self.bus.events)):
            with self.subTest(where=name):
                cyc = self.events(where, "ent:cyclist")
                active = [e for e in cyc if e[2] == "sample" and e[4] == "ACTIVE"]
                # The first PUT and every refresh round, none withdrawn between.
                self.assertGreaterEqual(len(active), 1 + ROUNDS, cyc)
                first_out = next((e[0] for e in cyc if e[4] == "RETIRED"
                                  or e[2] == "dispose"), None)
                self.assertTrue(first_out is None or first_out > active[-1][0],
                                f"withdrawn while refreshing: {cyc}")

    def test_lapse(self):
        for name, where in (("socket", self.socket), ("bus", self.bus.events)):
            with self.subTest(where=name):
                cyc = self.events(where, "ent:cyclist")
                last = [e for e in cyc if e[2] == "sample" and e[4] == "ACTIVE"][-1]
                term = next(e for e in cyc if e[4] == "RETIRED")
                disp = next(e for e in cyc if e[2] == "dispose")
                late = term[0] - last[0]
                self.assertEqual(term[5], "refresh lapsed")
                self.assertEqual(disp[5], "refresh lapsed")
                self.assertGreaterEqual(disp[0], term[0])
                self.assertTrue(REFRESH - 0.3 <= late <= REFRESH + SLACK,
                                f"{name}: withdrawn {late:.2f} s after the last "
                                f"refresh, interval {REFRESH:g} s")
        self.assertEqual(self.line("lapse", "cyclist, after its interval")[0], "404")
        self.assertEqual(self.line("lapse", "bench, under the same silence")[0], "200")

    def test_withdraw(self):
        self.assertEqual(self.line("withdraw", "near-1")[0], "204")
        self.assertEqual(self.line("withdraw", "bench")[0], "204")
        self.assertEqual(self.line("withdraw", "bench, after")[0], "404")
        for name, where in (("socket", self.socket), ("bus", self.bus.events)):
            for key, state, reason in (
                    ("near-1", "RETIRED", "cyclist left"),
                    ("ent:bench", "RETIRED", "bench removed for repainting")):
                with self.subTest(where=name, key=key):
                    ev = self.events(where, key)
                    self.assertIn(("sample", key, state, reason),
                                  [e[2:] for e in ev], ev)
                    self.assertIn(("dispose", key, "", reason),
                                  [e[2:] for e in ev], ev)



class EntitiesOnly(Journey):
    """A gateway serving no claim type, as demo.spatialdds.org does on 0.8.1.

    The example says the claim is skipped and runs the rest: an endpoint
    without a claim lane is a configuration, not a failure.
    """
    CLAIMS = False

    def test_claim_skipped_and_said(self):
        self.assertEqual(self.line("populate", "near: cyclist -> bench"),
                         ["405", "urn:spatialdds-web:problem:not-publishable"])
        self.assertIn("populate  ... this endpoint serves no oarc.relationship "
                      "lane; the claim is skipped", self.example)
        self.assertFalse([ln for ln in self.example
                          if ln.startswith(("withdraw  near-1",
                                            "populate  the relationship"))])

    def test_entities_unchanged(self):
        self.assertEqual(self.line("populate", "bench (DECLARED)")[0], "201")
        self.assertEqual(self.line("lapse", "cyclist, after its interval")[0], "404")
        self.assertEqual(self.line("lapse", "bench, under the same silence")[0], "200")
        self.assertEqual(self.line("withdraw", "bench")[0], "204")
        for name, where in (("socket", self.socket), ("bus", self.bus.events)):
            with self.subTest(where=name):
                cyc = self.events(where, "ent:cyclist")
                self.assertIn(("sample", "ent:cyclist", "RETIRED", "refresh lapsed"),
                              [e[2:] for e in cyc])
                self.assertIn(("dispose", "ent:cyclist", "", "refresh lapsed"),
                              [e[2:] for e in cyc])
                self.assertFalse(self.events(where, "near-1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
