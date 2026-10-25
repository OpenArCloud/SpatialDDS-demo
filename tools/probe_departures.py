#!/usr/bin/env python3
"""Three departure defects, re-run by the probes that found them.

    SPATIALDDS_DDS_DOMAIN=97 python3 tools/probe_departures.py

The native-SDK census found three ways this repository's discovery readers
got a service's or an item's ending wrong (`docs/native-sdk-census-findings.md`
findings 2-4). The SDK adoption claims all three fixed, by construction of the
SDK's reader. This re-runs each finding's probe with two subjects side by side:

* **shipped:** the web bridge's own `SpatialDDSBridge`, the consumer the
  browser and the gateway read discovery through, exercised through its own
  readers and its own `AnnounceCache.apply_events` / `content_items`;
* **control:** the same input, read with the finding's defect planted back
  in. A probe that reported "fixed" with nothing able to fail would prove
  nothing, so each arm shows it still tells the defect from the fix:
  - arm 1: the bridge's own `AnnounceCache`, fed the same events with the
    `Dispose` dropped, which is finding 3 (a dispose that arrives on a valid
    sample read as the sample alone);
  - arm 2: a Depart reader with the TRANSIENT_LOCAL QoS the census found,
    raw cyclonedds, which is finding 4;
  - arm 3: a second inventory reader that purges on lost liveliness, which is
    finding 2.
  Until `discovery_bus` was retired the control was its readers, the ones the
  census measured. Keeping that stack alive only to show old defects would
  have been two stacks in the tree; the planted controls show the same three,
  and each is the shipped code but for the one thing the finding is about.

The arms:

1. **The valid-sample departure** (finding 3). A participant announces, the
   subjects see it alive, then it stops cleanly: a terminal announce and its
   dispose, written before either subject takes again. Under KEEP_LAST(1) the
   dispose arrives as the terminal sample's own instance state.
2. **The Depart lane** (finding 4). The same clean stop also writes `Depart`
   on the spec's VOLATILE lane. Measured as the census did: how many writers
   each Depart reader matched before the close, and whether a Depart arrived.
3. **The crash** (findings 1 and 2). The real catalogue server publishes its
   inventory, and is `kill -9`ed. Items must stay present and the writer must
   be reported lost. A clean SIGTERM stop is the other door: each item
   withdrawn, as its writer's `on_exit="withdraw"` declares.

Every subject runs in this process; each venue participant runs in its own.
Exit status is 0 only if every shipped verdict holds *and* every control
reproduced its defect.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for path in (REPO, REPO / "bridges" / "web_bridge"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import spatialdds as sd  # noqa: E402

from spatialdds_demo.lanes import SCENE  # noqa: E402

SID = "svc:probe:withdrawing"
SID2 = "svc:probe:departing"
CATALOG = "svc:content:probe/catalog"

verdicts: list = []


def verdict(arm: str, subject: str, claim: str, held: bool, detail: str) -> None:
    verdicts.append((arm, subject, held))
    print(f"  [{'PASS' if held else 'FAIL'}] {subject:8s} {claim}: {detail}")


def bridge_for(domain: int):
    from bridges.web_bridge.server import SpatialDDSBridge
    bridge = SpatialDDSBridge()
    bridge._domain_id = domain
    bridge._start_transport(domain)
    return bridge


def planted_cache():
    """Finding 3, planted: the bridge's AnnounceCache, with each Dispose
    dropped before it is read, so a dispose that arrives on a valid sample is
    read as the sample alone."""
    from announce_cache import AnnounceCache

    class Planted(AnnounceCache):
        def apply_events(self, events) -> int:
            return super().apply_events(
                [ev for ev in events if not isinstance(ev, sd.Dispose)])
    return Planted()


def planted_depart_reader(domain: int):
    """Finding 4, planted: a Depart reader with the QoS the census found
    (RELIABLE, TRANSIENT_LOCAL), raw cyclonedds, since the SDK refuses to open
    one. Returns it with its participant, to keep both alive."""
    from cyclonedds.core import Policy, Qos
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.sub import DataReader
    from cyclonedds.topic import Topic
    from cyclonedds.util import duration

    wk = sd.dds.registry.well_known("Depart")
    dp = DomainParticipant(domain)
    topic = Topic(dp, wk.topic, sd.dds.class_for("Depart"))
    qos = Qos(Policy.Reliability.Reliable(duration(seconds=1)),
              Policy.Durability.TransientLocal,
              Policy.History.KeepLast(1))
    return DataReader(dp, topic, qos=qos), dp


class PlantedInventory:
    """Finding 2, planted: an inventory reader that purges every item when its
    writer is lost, as the old ContentInventory did. Otherwise the shipped
    content_items()."""

    def __init__(self, participant) -> None:
        self._reader = participant.reader("ContentAnnounce", settle_s=0.3)
        self._items: dict = {}

    def items(self) -> list:
        for ev in self._reader.take():
            if isinstance(ev, sd.Sample):
                self._items[ev.key] = ev.data
            elif isinstance(ev, sd.Dispose):
                self._items.pop(ev.key, None)
            elif isinstance(ev, sd.Liveliness) and not ev.alive:
                self._items.clear()                 # the defect
        return list(self._items.values())


def child(code: str, domain: int, **env) -> subprocess.Popen:
    """A participant of its own, in a process of its own."""
    return subprocess.Popen(
        [sys.executable, "-c", code], cwd=REPO,
        env={**os.environ, "SPATIALDDS_DDS_DOMAIN": str(domain),
             "PYTHONPATH": str(REPO), **env},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


def wait_for(predicate, timeout: float, step: float = 0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


# Arm 1's service writes the wire condition finding 3 is about: a final
# announce and then its dispose, back to back, so the reader's last sample
# arrives already carrying NOT_ALIVE_DISPOSED. An SDK participant writes
# exactly that: announce() re-publishes, and close() disposes. (Until
# spatialdds 0.7.3 its announce could not pass the bridge's discovery core,
# S14, and this was the demo's old AnnouncePublisher.) Its Depart goes to a
# lane arm 1 never reads, so arm 1 measures the Announce door alone.
WITHDRAWING = f"""
import sys, spatialdds as sd
p = sd.participant({SCENE!r}, service_id={SID!r}, authority="probe.example.com")
p.announce("VPS", coverage=sd.circle(30.27, -97.74, 500), ttl_sec=300)
print("announced", flush=True)
sys.stdin.readline()
p.announce("VPS", coverage=sd.circle(30.27, -97.74, 500), ttl_sec=300)  # the last sample ...
p.close(reason="the probe asked it to stop")   # ... and its dispose, untaken between
print("closed", flush=True)
"""

# Arm 2's service: the SDK's own announce, whose clean stop writes Depart.
DEPARTING = f"""
import sys, spatialdds as sd
p = sd.participant({SCENE!r}, service_id={SID2!r}, authority="probe.example.com")
p.announce("VPS", coverage=sd.circle(30.27, -97.74, 500), ttl_sec=300)
print("announced", flush=True)
sys.stdin.readline()
p.close(reason="the probe asked it to stop")
print("closed", flush=True)
"""


def stop(service: subprocess.Popen) -> None:
    service.stdin.write("close\n")
    service.stdin.flush()
    assert service.stdout.readline().strip() == "closed"
    service.wait(timeout=10)
    time.sleep(1.0)            # every sample of the stop has arrived


def arm_1(domain: int) -> None:
    print("arm 1: the departure that arrives on a valid sample")
    bridge = bridge_for(domain)
    control = planted_cache()
    service = child(WITHDRAWING, domain)
    try:
        assert service.stdout.readline().strip() == "announced"

        def cached(cache):
            return SID in {r.service_id for r in cache.records()}

        def both_see():
            events = bridge._announce_sub.take()
            bridge._announces.apply_events(events)
            control.apply_events(events)
            return cached(bridge._announces) and cached(control)

        wait_for(both_see, 8.0)
        seen_shipped, seen_control = cached(bridge._announces), cached(control)
        print(f"  before the stop: shipped caches it={seen_shipped}, "
              f"control caches it={seen_control}")
        if not (seen_shipped and seen_control):
            print("  setup failed: a subject never saw the service alive, so "
                  "this arm can say nothing about its departure")
            verdicts.append(("1", "setup", False))
            return

        stop(service)
        # One take after the whole stop has arrived: the terminal announce
        # and its dispose, neither taken before.
        events = bridge._announce_sub.take()
        bridge._announces.apply_events(events)
        control.apply_events(events)
        kinds = [type(ev).__name__ for ev in events]
        gone = not cached(bridge._announces)
        verdict("1", "shipped", "a clean stop evicts the service", gone,
                f"Announce reader events {kinds} -> "
                f"{'evicted' if gone else 'STILL CACHED'}")
        still = cached(control)
        verdict("1", "control", "reproduces finding 3 (reports it alive)",
                still, f"the same events with the Dispose dropped -> "
                       f"{'still cached' if still else 'evicted'}")
    finally:
        if service.poll() is None:
            service.kill()
            service.wait()


def arm_2(domain: int) -> None:
    print("\narm 2: the Depart lane")
    bridge = bridge_for(domain)
    control, _control_dp = planted_depart_reader(domain)
    service = child(DEPARTING, domain)
    try:
        assert service.stdout.readline().strip() == "announced"
        wait_for(lambda: bridge._depart_sub.matched() > 0, 8.0)
        time.sleep(0.5)        # the same settle for the control's reader
        matched = bridge._depart_sub.matched()
        matched_control = control.get_subscription_matched_status().current_count
        stop(service)
        departs = [ev for ev in bridge._depart_sub.take()
                   if isinstance(ev, sd.Sample)
                   and ev.data.service_id == SID2]
        verdict("2", "shipped", "the Depart reader hears the departure",
                matched > 0 and len(departs) == 1,
                f"matched writers before close: {matched}; "
                f"Depart samples for it: {len(departs)}")
        verdict("2", "control", "reproduces finding 4 (matches nothing)",
                matched_control == 0,
                f"matched writers before close: {matched_control}")
    finally:
        if service.poll() is None:
            service.kill()
            service.wait()


def catalogue(domain: int, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "ar_demo/spatialdds_catalog_server.py",
         "--seed", "ar_demo/catalog_seed.json", "--summary-only"],
        cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        env={**os.environ, "SPATIALDDS_DDS_DOMAIN": str(domain),
             "PYTHONPATH": str(REPO),
             "SPATIALDDS_CATALOG_SERVICE_ID": CATALOG,
             "SPATIALDDS_CONTENT_RESOLVER_PORT": str(port)})


def arm_3(domain: int, port: int) -> None:
    from spatialdds_demo import content_catalogue as cc

    # The whole inventory, counted from the seed the server publishes. The
    # first draft waited for "any items" and compared partial counts: 7 of a
    # 30-row seed, which says nothing about the 23 not yet arrived.
    seed = len(cc.load_seed(str(REPO / "ar_demo" / "catalog_seed.json")))

    print("\narm 3: the catalogue is kill -9ed")
    bridge = bridge_for(domain)
    control_participant = sd.participant(SCENE, domain=domain)
    control = PlantedInventory(control_participant)
    server = catalogue(domain, port)
    try:
        wait_for(lambda: len(bridge.content_items()) == seed, 20.0)
        wait_for(lambda: len(control.items()) == seed, 20.0)
        n, n_control = len(bridge.content_items()), len(control.items())
        print(f"  before: shipped holds {n} item(s), control {n_control}, "
              f"of {seed} in the seed; killing pid {server.pid}")
        if not (n == seed and n_control == seed):
            print("  setup failed: the whole inventory never arrived")
            verdicts.append(("3", "setup", False))
            return
        os.kill(server.pid, signal.SIGKILL)
        server.wait()
        t0 = time.time()
        # Lost when the lease lapses; wait for the shipped reader to say so.
        wait_for(lambda: (bridge.content_items(),
                          bridge.content_events["writers_lost"])[1], 30.0)
        lost_after = time.time() - t0
        time.sleep(1.0)
        after = len(bridge.content_items())
        ev = dict(bridge.content_events)
        verdict("3", "shipped", "items stay present, writer reported lost",
                after == n and ev["writers_lost"] > 0 and ev["withdrawn"] == 0,
                f"{after} of {n} item(s) after the kill; writer lost "
                f"{lost_after:.1f}s after it ({ev['writers_lost']} event(s)); "
                f"withdrawn {ev['withdrawn']}")
        after_control = len(control.items())
        verdict("3", "control", "reproduces finding 2 (purges on no-writers)",
                after_control < n_control,
                f"{after_control} of {n_control} item(s) after the kill")
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()

    print("\narm 3, the other door: the catalogue stops cleanly (SIGTERM)")
    bridge = bridge_for(domain + 1)
    server = catalogue(domain + 1, port)
    try:
        wait_for(lambda: len(bridge.content_items()) == seed, 20.0)
        n = len(bridge.content_items())
        if n != seed:
            print(f"  setup failed: {n} of {seed} item(s) arrived")
            verdicts.append(("3", "setup", False))
            return
        os.kill(server.pid, signal.SIGTERM)
        server.wait(timeout=15)
        wait_for(lambda: not bridge.content_items(), 10.0)
        ev = dict(bridge.content_events)
        after = len(bridge.content_items())
        verdict("3", "shipped", "a clean stop withdraws every item",
                n > 0 and after == 0 and ev["withdrawn"] == n,
                f"{n} item(s) -> {after}; withdrawn {ev['withdrawn']}, "
                f"exit status {server.returncode}")
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()


def main() -> int:
    domain = int(os.environ.get("SPATIALDDS_DDS_DOMAIN", "97"))
    os.environ["SPATIALDDS_DDS_DOMAIN"] = str(domain)
    print(f"probe: domains {domain} (arm 1), {domain + 1} (arm 2), "
          f"{domain + 2} and {domain + 3} (arm 3); "
          f"spatialdds {sd.__version__}\n")
    arm_1(domain)
    arm_2(domain + 1)
    arm_3(domain + 2, int(os.environ.get("PROBE_RESOLVER_PORT", "18097")))
    failed = [v for v in verdicts if not v[2]]
    print(f"\n{len(verdicts) - len(failed)} of {len(verdicts)} verdicts held")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
