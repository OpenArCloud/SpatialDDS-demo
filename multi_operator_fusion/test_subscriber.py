#!/usr/bin/env python3
"""Unit tests for multi_operator_fusion.routing helpers.

Covers the pure routing functions shared by the Rerun subscriber — topic
parsing and per-operator color mapping — without pulling in rerun or
cyclonedds.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SELF_DIR = Path(__file__).resolve().parent
if str(SELF_DIR) not in sys.path:
    sys.path.insert(0, str(SELF_DIR))

from routing import (  # noqa: E402
    OPERATOR_COLORS,
    UNKNOWN_COLOR,
    color_for_operator,
    operator_from_topic,
)


class OperatorFromTopic(unittest.TestCase):
    def test_detection3d_topic(self):
        self.assertEqual(
            operator_from_topic("spatialdds/operator_a/sensing/detection3d/v1"),
            "operator_a",
        )

    def test_vision_topic(self):
        self.assertEqual(
            operator_from_topic("spatialdds/operator_b/vision/CAM_FRONT/frame/v1"),
            "operator_b",
        )

    def test_platform_topic(self):
        self.assertEqual(
            operator_from_topic("spatialdds/platform/fusion/track/v1"),
            "platform",
        )

    def test_infrastructure_topic(self):
        self.assertEqual(
            operator_from_topic("spatialdds/infrastructure/sensing/detection3d/v1"),
            "infrastructure",
        )

    def test_non_spatialdds_topic_returns_none(self):
        self.assertIsNone(operator_from_topic("foo/bar/baz"))

    def test_short_topic_returns_none(self):
        self.assertIsNone(operator_from_topic("spatialdds"))

    def test_empty_topic_returns_none(self):
        self.assertIsNone(operator_from_topic(""))


class OperatorColors(unittest.TestCase):
    def test_all_spec_operators_have_distinct_colors(self):
        ops = ["operator_a", "operator_b", "operator_c", "infrastructure", "platform"]
        colors = [color_for_operator(op) for op in ops]
        self.assertEqual(len(set(colors)), len(ops),
                         f"Operator colors collide: {dict(zip(ops, colors))}")

    def test_unknown_operator_gets_fallback_color(self):
        self.assertEqual(color_for_operator("mystery"), UNKNOWN_COLOR)

    def test_color_is_rgb_triple(self):
        c = color_for_operator("operator_a")
        self.assertEqual(len(c), 3)
        for channel in c:
            self.assertTrue(0 <= channel <= 255)

    def test_spec_colors_table_has_all_five(self):
        for op in ("operator_a", "operator_b", "operator_c", "infrastructure", "platform"):
            self.assertIn(op, OPERATOR_COLORS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
