#!/usr/bin/env python3
"""
The shared sensing types, re-exported for the DeepSense converters.

They live in ``nuscenes/sensor_types.py`` because that demo needed them
first; both demos build the same `spatial::sensing` types, so there is one
definition. The module was called `spatialdds_types` on both sides and in
`multi_operator_fusion/`, and whichever directory reached ``sys.path`` first
won — a real footgun that silently gave one demo another's idea of a type.
"""

from nuscenes.sensor_types import *  # noqa: F401,F403
