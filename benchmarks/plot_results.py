#!/usr/bin/env python3
"""Generate publication-ready figures from benchmark CSV output."""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np


def _read_csv(path: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8") as handle:
        filtered = [line for line in handle if not line.startswith("#") and line.strip()]
    reader = csv.DictReader(filtered)
    for row in reader:
        rows.append(row)
    return rows


def _stats(values: List[float]) -> Tuple[float, float, float]:
    arr = np.array(values, dtype=float)
    return float(np.median(arr)), float(np.percentile(arr, 5)), float(np.percentile(arr, 95))


def figure_envelope_overhead(latency_rows: List[Dict[str, str]], out_path: str) -> None:
    grouped: Dict[Tuple[str, int], List[float]] = defaultdict(list)
    payload_sizes = sorted({int(r["payload_bytes"]) for r in latency_rows})
    for row in latency_rows:
        key = (row["path"], int(row["payload_bytes"]))
        grouped[key].append(float(row["latency_ns"]) / 1_000_000.0)

    paths = ["spatialdds_envelope", "raw_dds"]
    labels = ["SpatialDDS envelope", "Raw DDS"]
    x = np.arange(len(payload_sizes))
    width = 0.36

    fig, ax = plt.subplots(figsize=(3.5, 2.4), constrained_layout=True)
    for i, path in enumerate(paths):
        medians = []
        yerr_low = []
        yerr_high = []
        for size in payload_sizes:
            median, p5, p95 = _stats(grouped[(path, size)])
            medians.append(median)
            yerr_low.append(median - p5)
            yerr_high.append(p95 - median)
        ax.bar(
            x + (i - 0.5) * width,
            medians,
            width,
            label=labels[i],
            yerr=[yerr_low, yerr_high],
            capsize=2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels([f"{s//1024}KB" for s in payload_sizes])
    ax.set_xlabel("Payload size")
    ax.set_ylabel("Median latency (ms)")
    ax.set_title("Envelope overhead")
    ax.legend(fontsize=7)
    fig.savefig(out_path)


def figure_multioperator(multi_rows: List[Dict[str, str]], out_path: str) -> None:
    by_op: Dict[int, Dict[str, List[float]]] = defaultdict(lambda: {"latency": [], "throughput": []})
    for row in multi_rows:
        n = int(row["num_operators"])
        by_op[n]["latency"].append(float(row["msg_latency_ns"]) / 1_000_000.0)
        by_op[n]["throughput"].append(float(row["total_msgs_per_sec"]))

    ops = sorted(by_op.keys())
    med_latency = [np.median(by_op[n]["latency"]) for n in ops]
    med_tp = [np.median(by_op[n]["throughput"]) for n in ops]

    fig, ax1 = plt.subplots(figsize=(3.5, 2.4), constrained_layout=True)
    ax2 = ax1.twinx()

    l1 = ax1.plot(ops, med_latency, marker="o", color="tab:blue", label="Median latency")
    l2 = ax2.plot(ops, med_tp, marker="s", color="tab:orange", label="Aggregate throughput")

    ax1.set_xlabel("Number of operators")
    ax1.set_ylabel("Median latency (ms)", color="tab:blue")
    ax2.set_ylabel("Throughput (msgs/sec)", color="tab:orange")
    ax1.set_title("Multi-operator scaling")

    lines = l1 + l2
    ax1.legend(lines, [line.get_label() for line in lines], fontsize=7, loc="upper left")
    fig.savefig(out_path)


def figure_coverage_scaling(coverage_rows: List[Dict[str, str]], out_path: str) -> None:
    grouped: Dict[int, List[float]] = defaultdict(list)
    for row in coverage_rows:
        grouped[int(row["num_entries"])].append(float(row["query_time_ns"]) / 1_000_000.0)

    counts = sorted(grouped.keys())
    medians = []
    yerr_low = []
    yerr_high = []
    for n in counts:
        median, p5, p95 = _stats(grouped[n])
        medians.append(median)
        yerr_low.append(median - p5)
        yerr_high.append(p95 - median)

    fig, ax = plt.subplots(figsize=(3.5, 2.4), constrained_layout=True)
    ax.errorbar(counts, medians, yerr=[yerr_low, yerr_high], marker="o", capsize=2)
    ax.set_xlabel("Catalog entries")
    ax.set_ylabel("Median query time (ms)")
    ax.set_title("Coverage query scaling")
    fig.savefig(out_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot SpatialDDS benchmark results")
    parser.add_argument("--input", default="results/")
    parser.add_argument("--output", default="figures/")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.makedirs(args.output, exist_ok=True)
    plt.style.use("seaborn-v0_8-paper")
    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 8,
        "legend.fontsize": 7,
    })

    latency_rows = _read_csv(os.path.join(args.input, "latency.csv"))
    multi_rows = _read_csv(os.path.join(args.input, "multioperator.csv"))
    coverage_rows = _read_csv(os.path.join(args.input, "coverage_query.csv"))

    figure_envelope_overhead(latency_rows, os.path.join(args.output, "figure1_envelope_overhead.pdf"))
    figure_multioperator(multi_rows, os.path.join(args.output, "figure2_multioperator_scaling.pdf"))
    figure_coverage_scaling(coverage_rows, os.path.join(args.output, "figure3_coverage_scaling.pdf"))

    print(f"Wrote figures to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
