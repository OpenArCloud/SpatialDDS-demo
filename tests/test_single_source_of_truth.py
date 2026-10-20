#!/usr/bin/env python3
"""
No module-level name in this package is assigned twice, and the tempo
re-exports are the tempo values.

**Why this is a gate.** `model_service` carried a ~10-line block of constants
twice, plus four literals that shadowed what it had imported from `tempo.py` —
`LATCH_EVERY_N_MOVES`, `IDLE_FLUSH_S`, `IDLE_FLUSH_FACTOR` and
`GAP_SMOOTHING`. Every pair agreed, so nothing was broken and nothing failed;
the later assignment simply won, and changing `tempo.py` would have had no
effect on the service that re-exports it. A single source of truth that is
silently not the source is worse than two acknowledged copies.

It survived for a long time because it is invisible from every direction: the
module imports correctly, the values are right, the tests pass, and the
duplication is only visible if you read the file top to bottom looking for it.

The `GAP_SMOOTHING` case is why this checks two different things. It was an
*import* shadowed by a literal rather than an assignment repeated — so a scan
for repeated assignments does not see it, and the first version of this
finding counted three where there were four.
"""
from __future__ import annotations

import ast
import collections
import importlib
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PACKAGE = REPO / "spatialdds_demo"


def _modules() -> list[Path]:
    return sorted(p for p in PACKAGE.glob("*.py") if p.name != "__init__.py")


class NothingIsAssignedTwice(unittest.TestCase):
    def test_there_are_modules_to_check(self):
        self.assertGreater(len(_modules()), 5, "the walk found almost nothing")

    def test_no_module_level_name_is_assigned_twice(self):
        offenders = []
        for path in _modules():
            counts: collections.Counter = collections.Counter()
            lines: dict[str, list[int]] = {}
            for node in ast.parse(path.read_text()).body:
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        counts[target.id] += 1
                        lines.setdefault(target.id, []).append(node.lineno)
            for name, n in counts.items():
                if n > 1:
                    offenders.append(
                        f"{path.name}: {name} assigned {n} times at "
                        f"{lines[name]} — the last one wins")
        self.assertEqual(
            offenders, [],
            "a repeated module-level assignment is silent: both agree today, "
            "and the earlier one stops mattering.\n" + "\n".join(offenders))

    def test_no_import_is_shadowed_by_a_later_literal(self):
        """
        The `GAP_SMOOTHING` shape: imported, then re-assigned. Invisible to the
        check above, because an import is not an `Assign`.
        """
        offenders = []
        for path in _modules():
            tree = ast.parse(path.read_text())
            imported: dict[str, int] = {}
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        imported[alias.asname or alias.name] = node.lineno
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Name)
                                and target.id in imported):
                            offenders.append(
                                f"{path.name}: {target.id} imported at line "
                                f"{imported[target.id]} and re-assigned at "
                                f"{node.lineno}, which shadows the import")
        self.assertEqual(offenders, [], "\n".join(offenders))


class TheTempoNumbersComeFromTempo(unittest.TestCase):
    """
    The re-exports are checked by value, not only by absence of shadowing.

    `model_service` deliberately re-exports tempo's numbers under its own
    historical names — its tests name them and its documented behaviour uses
    them. That is legitimate; what is not is those names drifting from what
    `tempo` says.
    """

    PAIRS = (("LATCH_EVERY_N_MOVES", "LATCH_EVERY_N"),
             ("IDLE_FLUSH_S", "IDLE_FLOOR_S"),
             ("IDLE_FLUSH_FACTOR", "IDLE_FACTOR"),
             ("GAP_SMOOTHING", "GAP_SMOOTHING"))

    def test_each_re_export_equals_its_tempo_value(self):
        service = importlib.import_module("spatialdds_demo.model_service")
        tempo = importlib.import_module("spatialdds_demo.tempo")
        for ours, theirs in self.PAIRS:
            with self.subTest(name=ours):
                self.assertEqual(
                    getattr(service, ours), getattr(tempo, theirs),
                    f"model_service.{ours} has drifted from tempo.{theirs}; "
                    f"tempo is the single source for the fast tier's cadence")

    def test_the_pairs_are_not_stale(self):
        """Every name this test claims to check must still exist."""
        service = importlib.import_module("spatialdds_demo.model_service")
        tempo = importlib.import_module("spatialdds_demo.tempo")
        for ours, theirs in self.PAIRS:
            with self.subTest(name=ours):
                self.assertTrue(hasattr(service, ours),
                                f"model_service no longer defines {ours}")
                self.assertTrue(hasattr(tempo, theirs),
                                f"tempo no longer defines {theirs}")


if __name__ == "__main__":
    unittest.main()
