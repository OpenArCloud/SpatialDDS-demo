#!/usr/bin/env python3
"""Shared benchmark utilities for SpatialDDS performance tests."""

from __future__ import annotations

import csv
import datetime as dt
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Sequence

WARMUP_ITERATIONS = 50
DEFAULT_ITERATIONS = 1000
DDS_DOMAIN_BOOTSTRAP = 0
DDS_DOMAIN_MAIN = 1


class Timer:
    def __init__(self) -> None:
        self.start_ns = 0
        self.end_ns = 0
        self.elapsed_ns = 0

    def __enter__(self) -> "Timer":
        self.start_ns = time.perf_counter_ns()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.end_ns = time.perf_counter_ns()
        self.elapsed_ns = self.end_ns - self.start_ns


@dataclass
class Stats:
    count: int
    mean: float
    median: float
    p95: float
    p99: float
    stddev: float
    p5: float

    @classmethod
    def from_values(cls, values: Sequence[float]) -> "Stats":
        if not values:
            return cls(0, math.nan, math.nan, math.nan, math.nan, math.nan, math.nan)

        ordered = sorted(float(v) for v in values)
        count = len(ordered)
        mean = statistics.fmean(ordered)
        median = statistics.median(ordered)
        stddev = statistics.stdev(ordered) if count > 1 else 0.0
        return cls(
            count=count,
            mean=mean,
            median=median,
            p95=_percentile(ordered, 95.0),
            p99=_percentile(ordered, 99.0),
            stddev=stddev,
            p5=_percentile(ordered, 5.0),
        )


def _percentile(sorted_values: Sequence[float], p: float) -> float:
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * (p / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(sorted_values[low])
    frac = rank - low
    return float(sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac)


def ensure_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def csv_metadata() -> List[str]:
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    mem_bytes = _memory_bytes()
    memory_gb = f"{mem_bytes / (1024 ** 3):.2f}" if mem_bytes else "unknown"
    return [
        f"generated_utc: {stamp}",
        f"host: {platform.node() or 'unknown'}",
        f"os: {platform.platform()}",
        f"python: {platform.python_version()}",
        f"cpu_model: {_cpu_model()}",
        f"cpu_logical_cores: {os.cpu_count() or 'unknown'}",
        f"ram_gb: {memory_gb}",
    ]


def write_csv(filename: str, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    ensure_dir(filename)
    with open(filename, "w", newline="", encoding="utf-8") as handle:
        for item in csv_metadata():
            handle.write(f"# {item}\n")
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def warmup(func: Callable[[], object], n: int = 10) -> None:
    for _ in range(max(0, int(n))):
        func()


def parse_csv_ints(value: str) -> List[int]:
    items: List[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        items.append(int(part))
    return items


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def format_ns_ms(value_ns: float) -> str:
    return f"{value_ns / 1_000_000.0:.3f}"


def _cpu_model() -> str:
    proc = platform.processor().strip()
    if proc:
        return proc
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True)
            return out.strip() or "unknown"
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def _memory_bytes() -> int:
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(out)
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return int(page_size) * int(pages)
    except Exception:
        return 0
