"""Shared routing helpers for the multi-operator fusion demo.

Pure functions/constants with no DDS or Rerun dependency — lives
here so test harnesses can import without the heavy deps.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

OPERATOR_COLORS: Dict[str, Tuple[int, int, int]] = {
    "operator_a":     (80, 200, 120),   # green
    "operator_b":     (80, 140, 240),   # blue
    "operator_c":     (240, 160, 60),   # orange
    "infrastructure": (220, 90, 200),   # magenta
    "platform":       (240, 240, 240),  # white (fused)
}
UNKNOWN_COLOR: Tuple[int, int, int] = (160, 160, 160)


def operator_from_topic(logical_topic: str) -> Optional[str]:
    """Extract the operator namespace from ``spatialdds/{operator}/…``.

    Returns None for malformed or non-spatialdds topics.
    """
    if not logical_topic.startswith("spatialdds/"):
        return None
    parts = logical_topic.split("/")
    if len(parts) < 3:
        return None
    return parts[1]


def color_for_operator(operator: str) -> Tuple[int, int, int]:
    return OPERATOR_COLORS.get(operator, UNKNOWN_COLOR)
