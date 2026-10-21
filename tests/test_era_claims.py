#!/usr/bin/env python3
"""
What this repository claims its era is, checked against what its types are.

Era is carried twice. Once in the generated code -- every module's
``MODULE_ID`` -- and once as data written by hand: the ``schema_version`` on a
sample, the ``preferred_profiles`` in an announce, the ``profile`` of a
manifest. The second copy is what peers read, and nothing tied it to the
first: the 1.8 migration found 65 such literals across 16 files still saying
1.7, in modules whose types had already moved. A sample whose types are 1.8
and whose ``schema_version`` says 1.7 is a false statement on the wire, and
the only thing that would ever have noticed is a strict peer.

So the hand-written constants are asserted equal to the generated ones.
They stay literals in ``payloads`` rather than imports, because that module
is deliberately stdlib-only; this gate is the price of that choice.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import spatialdds_idl18                                   # noqa: E402
from spatialdds_demo import discovery_http, payloads      # noqa: E402
from spatialdds_validation import SpatialDDSValidator     # noqa: E402


def generated_module_ids() -> dict:
    """{'spatial.core': 'spatial.core/1.8', ...} from the 1.8 tree itself."""
    out = {}
    for m in pkgutil.walk_packages(spatialdds_idl18.__path__, "spatialdds_idl18."):
        mod = importlib.import_module(m.name)
        mid = getattr(mod, "MODULE_ID", None)
        if isinstance(mid, str):
            out[mid.split("/")[0]] = mid
    return out


class TheHandWrittenEraIsTheGeneratedOne(unittest.TestCase):
    def test_payloads_schema_constants(self):
        ids = generated_module_ids()
        for name in ("SCHEMA_CORE", "SCHEMA_SEMANTICS", "SCHEMA_EVENTS",
                     "SCHEMA_DISCOVERY"):
            with self.subTest(constant=name):
                claim = getattr(payloads, name)
                self.assertEqual(claim, ids[claim.split("/")[0]],
                                 f"payloads.{name} says {claim}")

    def test_the_manifest_profile_is_the_specs_era(self):
        core = generated_module_ids()["spatial.core"]
        minor = core.split("/")[1]
        self.assertEqual(discovery_http.MANIFEST_PROFILE,
                         f"spatial.manifest/{minor}",
                         "manifests served here must name the era the types "
                         "are, per spec §8: spatial.manifest/<era>")

    def test_the_bootstrap_schema_is_the_specs_era(self):
        core = generated_module_ids()["spatial.core"]
        self.assertEqual(discovery_http.BOOTSTRAP_SCHEMA, core.split("/")[1])

    def test_the_validator_accepts_every_normative_module_id(self):
        """
        Every non-provisional MODULE_ID in the tree passes this repo's own
        validator. Provisional modules (spatial.owm/0.1) version on their own
        and are excluded by their major, not by name.
        """
        for family, mid in sorted(generated_module_ids().items()):
            if not re.search(r"/1\.\d+$", mid):
                continue
            with self.subTest(module=mid):
                SpatialDDSValidator.validate_module_version(mid)

    def test_and_rejects_the_previous_era(self):
        prev = re.sub(r"/1\.(\d+)$",
                      lambda m: f"/1.{int(m.group(1)) - 1}",
                      payloads.SCHEMA_CORE)
        with self.assertRaises(Exception):
            SpatialDDSValidator.validate_module_version(prev)


if __name__ == "__main__":
    unittest.main()
