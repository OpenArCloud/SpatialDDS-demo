#!/usr/bin/env python3
"""Radar and beam visualization helpers for DeepSense Scenario 9."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np


def radar_cube_to_range_angle(cube: np.ndarray) -> np.ndarray:
    range_doppler = np.fft.fft(cube, axis=2)
    range_angle = np.fft.fft(range_doppler, n=64, axis=0)
    range_angle_map = np.max(np.abs(range_angle), axis=2).T
    return 20.0 * np.log10(range_angle_map + 1e-10)


def radar_cube_to_range_doppler(cube: np.ndarray) -> np.ndarray:
    summed = np.sum(cube, axis=0)
    rd = np.fft.fftshift(np.fft.fft(summed, axis=1), axes=1)
    return 20.0 * np.log10(np.abs(rd) + 1e-10)


def render_beam_polar(power_vector: np.ndarray, best_idx: int, fov_deg: float = 90.0) -> np.ndarray:
    fig, ax = plt.subplots(figsize=(4, 4), subplot_kw={"projection": "polar"})
    angles = np.linspace(-np.radians(fov_deg / 2.0), np.radians(fov_deg / 2.0), len(power_vector))
    bars = power_vector - np.min(power_vector)
    colors = ["#ff6b3d" if i == best_idx else "#2f6db0" for i in range(len(power_vector))]
    ax.bar(angles, bars, width=np.radians(fov_deg / max(1, len(power_vector))), color=colors, alpha=0.85)
    ax.set_theta_zero_location("N")
    ax.set_thetamin(-fov_deg / 2.0)
    ax.set_thetamax(fov_deg / 2.0)
    ax.set_title(f"Best beam {best_idx} | {power_vector[best_idx]:.2f}")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    image = plt.imread(buf)
    return (image[:, :, :3] * 255).astype(np.uint8)
