"""Thin envelope publish/subscribe helpers for the ROS 2 bridge.

The bridge node sits on top of CycloneDDS but doesn't itself touch
``DataReader``/``DataWriter`` — it goes through these helpers so that
``ros2_to_spatialdds`` and ``spatialdds_to_ros2`` stay free of DDS imports
and remain unit-testable without cyclonedds.

The publisher/subscriber underneath are the same RELIABLE+KEEP_ALL
endpoints the MCAP recorder uses (``bridges/mcap_bridge/recorder.py``).
A bridge needs lossless replay just like a recorder does.

DDS imports are deferred until first use so importing this module on a
host without cyclonedds (Tier-1 test env) doesn't fail.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

# Reuse the lossless reader/writer factories from the MCAP bridge so the two
# bridges don't drift on QoS choices.
_HERE = Path(__file__).resolve().parent
_MCAP = _HERE.parent / "mcap_bridge"
if str(_MCAP) not in sys.path:
    sys.path.insert(0, str(_MCAP))


def _make_reader(domain_id: int):
    from recorder import _make_lossless_reader  # type: ignore
    return _make_lossless_reader(domain_id)


def _make_writer(domain_id: int):
    from replayer import _make_lossless_writer  # type: ignore
    return _make_lossless_writer(domain_id)


# ---------- Publisher --------------------------------------------------------

class EnvelopePublisher:
    """Lossless DDS writer that ships SpatialDDS envelope dicts.

    Holds one CycloneDDS participant + writer over the lifetime of the
    bridge node. Thread-safe writes (the underlying CycloneDDS writer is).
    """

    def __init__(self, domain_id: int):
        self._writer, self._EnvelopeCls = _make_writer(domain_id)

    def publish(self, logical_topic: str, msg_type: str,
                payload: Dict[str, Any], request_id: str = "",
                stamp_ns: Optional[int] = None) -> None:
        envelope = self._EnvelopeCls(
            msg_type=msg_type,
            logical_topic=logical_topic,
            payload_json=json.dumps(payload),
            stamp_ns=int(stamp_ns) if stamp_ns is not None else int(time.time_ns()),
            request_id=request_id or "",
        )
        self._writer.write(envelope)

    def close(self) -> None:
        # Allow RELIABLE peers a moment to ack outstanding samples.
        time.sleep(0.2)
        try:
            del self._writer
        except Exception:
            pass


# ---------- Subscriber -------------------------------------------------------

EnvelopeCallback = Callable[[str, str, Dict[str, Any], int], None]
"""Callback signature: ``(msg_type, logical_topic, payload_dict, stamp_ns) -> None``."""


class EnvelopeSubscriber:
    """Lossless DDS reader that hands decoded envelope payloads to a callback.

    Runs a daemon polling thread (matching ``EnvelopeTransport``'s pattern
    but with RELIABLE+KEEP_ALL QoS so bursts don't get collapsed).
    """

    def __init__(self, domain_id: int, callback: EnvelopeCallback,
                 poll_interval_s: float = 0.05):
        self._reader = _make_reader(domain_id)
        self._callback = callback
        self._poll_interval = float(poll_interval_s)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _poll(self) -> None:
        while not self._stop.is_set():
            samples = self._reader.take(N=512)
            if samples:
                for sample in samples:
                    if sample is None or not hasattr(sample, "payload_json"):
                        continue
                    try:
                        payload = json.loads(getattr(sample, "payload_json", "") or "{}")
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    msg_type = getattr(sample, "msg_type", "") or ""
                    topic = getattr(sample, "logical_topic", "") or ""
                    stamp_ns = int(getattr(sample, "stamp_ns", 0) or 0)
                    try:
                        self._callback(msg_type, topic, payload, stamp_ns)
                    except Exception as exc:
                        print(f"[envelope] callback error on {topic}: {exc}",
                              file=sys.stderr)
            else:
                self._stop.wait(timeout=self._poll_interval)
