"""
The client autostarts only against the demo's own VPS.

Since P3.0 a page load localizes itself without a click -- but only when the
announced VPS is the demo's mock. A real one (OpenVPS on a GPU box) would
otherwise get a localization request per page refresh, which is rude to the
service and hides what localization costs behind an F5. The demo exists partly
to make that exchange visible.

The discriminator is a service-id prefix, and it spans two languages: the
client carries `DEMO_VPS_PREFIX` and every mock deployment announces an id
under it. Nothing links them but this test. Rename a mock to
`svc:vps:mock/...` and autostart silently stops working -- the page would
still load, still be usable, and simply never localize itself, which is
exactly the kind of failure nobody notices for a month.

Same trick as the TYPE_LABELS guard in the bridge's cache tests, for the same
reason: two lists that must agree, in languages that cannot import each other.
"""

import os
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# The real deployment, which must NOT match, or the courtesy is undone.
OPENVPS_ID = "svc:vps:oarc/openvps-scan"

# Only ids a *deployment* announces are interesting. Test fixtures name
# `svc:vps:acme/...` and `svc:vps:whoever/it-is` all over the suite and should:
# they are exercising parsing, not standing up a service. What matters is the
# value something actually runs with, which in this repo is always assigned
# to SPATIALDDS_VPS_SERVICE_ID or vps_service_id.
CONFIG_KEYS = ("SPATIALDDS_VPS_SERVICE_ID", "vps_service_id")


def _announced_ids():
    """Every service id this repo configures something to announce."""
    found = {}
    for path in sorted(REPO.rglob("*")):
        if path.is_dir() or "node_modules" in path.parts or ".git" in path.parts:
            continue
        if path.suffix not in (".py", ".sh", ".ts", ".json", ".md", ".yml", ".yaml"):
            continue
        try:
            lines = path.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, 1):
            if not any(key in line for key in CONFIG_KEYS):
                continue
            for service_id in re.findall(r"svc:vps:[a-z0-9_.-]+/[a-z0-9_.-]+", line):
                found.setdefault(service_id, []).append(
                    f"{path.relative_to(REPO)}:{number}")
    return found


def _client_prefix() -> str:
    source = (REPO / "web" / "src" / "app.ts").read_text()
    match = re.search(r"DEMO_VPS_PREFIX\s*=\s*'([^']+)'", source)
    if match is None:
        raise AssertionError("DEMO_VPS_PREFIX not found in web/src/app.ts")
    return match.group(1)


class AutostartGating(unittest.TestCase):
    def test_every_configured_mock_is_under_the_prefix(self):
        """
        Discovered, not listed, so the guard cannot go stale.

        A mock renamed to `svc:vps:mock/...` fails here rather than silently
        disabling autostart -- which would leave a page that still loads,
        still works, and simply never localizes itself. That is the kind of
        regression nobody notices for a month.
        """
        prefix = _client_prefix()
        announced = _announced_ids()
        self.assertTrue(announced, "found no configured VPS ids at all — has the "
                                   "config key been renamed?")
        for service_id, places in sorted(announced.items()):
            if service_id == OPENVPS_ID:
                continue
            with self.subTest(service_id=service_id):
                self.assertTrue(
                    service_id.startswith(prefix),
                    f"{service_id!r} (configured at {', '.join(places)}) is not "
                    f"under the client's {prefix!r} gate — autostart would "
                    f"silently stop firing against it")

    def test_the_real_vps_is_not_under_the_prefix(self):
        """The half that protects somebody else's GPU."""
        self.assertFalse(OPENVPS_ID.startswith(_client_prefix()),
                         "autostart must never fire against the real VPS")


if __name__ == "__main__":
    unittest.main()
