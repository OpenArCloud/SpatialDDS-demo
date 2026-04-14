#!/usr/bin/env python3
"""Carve a minimal reproducible subset for the multi-operator fusion demo.

Takes the full nuScenes v1.0-mini + DeepSense Scenario 9 directories and
writes a ~100 MB subset that keeps everything the demo actually reads:

  nuScenes: scene-0061 keyframe samples only (LiDAR + radar + cameras)
            plus the v1.0-mini JSON tables and maps.
            Drops: sweeps/ (unused — publishers only read samples/), and
            every other scene's sample files.

  DeepSense Scenario 9: one sequence (default 1) — mmWave beam power,
            2D LiDAR, GPS. Optionally radar cubes (opt-in via flag;
            they add ~100 MB per sequence).

The output layout matches what run_docker_demo.sh expects via the
NUSCENES_DATAROOT / DEEPSENSE_DATAROOT env vars.

Typical use by the demo maintainer:

    python multi_operator_fusion/scripts/make_demo_subset.py \\
        --nuscenes-src     /path/to/full/nuscenes/v1.0-mini \\
        --deepsense-src    /path/to/full/scenario9_dev \\
        --out              multi_operator_fusion/data \\
        --scene            scene-0061 \\
        --deepsense-sequence 1

Then tar it up and host externally (HuggingFace Datasets, Zenodo, or a
GitHub Release); attendees run scripts/download_demo_data.sh to fetch.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Set


def _sizeof(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def _human(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _scene_filenames(nusc_src: Path, scene_name: str) -> Set[str]:
    """Return every sample_data filename referenced by the target scene.

    Reads the v1.0-mini JSON tables directly — no NuScenes devkit dep,
    so this runs in a bare Python env.
    """
    meta_dir = nusc_src / "v1.0-mini"
    scenes = json.loads((meta_dir / "scene.json").read_text())
    samples = json.loads((meta_dir / "sample.json").read_text())
    sample_data = json.loads((meta_dir / "sample_data.json").read_text())

    scene = next((s for s in scenes if s["name"] == scene_name), None)
    if scene is None:
        raise SystemExit(f"scene '{scene_name}' not found in {meta_dir}/scene.json")

    sample_tokens: Set[str] = set()
    token = scene["first_sample_token"]
    sample_by_token = {s["token"]: s for s in samples}
    while token:
        sample_tokens.add(token)
        token = sample_by_token[token]["next"]

    # sample_data entries include keyframes (is_key_frame) and sweeps.
    # Keep only keyframes belonging to the target scene's samples.
    filenames: Set[str] = set()
    for sd in sample_data:
        if sd["sample_token"] in sample_tokens and sd.get("is_key_frame"):
            filenames.add(sd["filename"])
    return filenames


def prune_nuscenes(src: Path, dst: Path, scene_name: str) -> None:
    dst.mkdir(parents=True, exist_ok=True)

    # 1. metadata tables (small, full copy)
    meta_src = src / "v1.0-mini"
    meta_dst = dst / "v1.0-mini"
    meta_dst.mkdir(exist_ok=True)
    for jf in meta_src.glob("*.json"):
        _copy_file(jf, meta_dst / jf.name)

    # 2. maps (small)
    if (src / "maps").exists():
        shutil.copytree(src / "maps", dst / "maps", dirs_exist_ok=True)

    # 3. LICENSE (if present)
    for aux in ("LICENSE", "README.md"):
        p = src / aux
        if p.is_file():
            _copy_file(p, dst / aux)

    # 4. scene-0061 keyframe sample_data only
    filenames = _scene_filenames(src, scene_name)
    kept, missing = 0, 0
    for rel in filenames:
        src_file = src / rel
        if not src_file.exists():
            missing += 1
            continue
        _copy_file(src_file, dst / rel)
        kept += 1
    print(f"  nuScenes: copied {kept}/{len(filenames)} files for {scene_name} "
          f"({missing} missing in source)", file=sys.stderr)


def _deepsense_row_paths(row: dict, include_radar: bool) -> Iterable[str]:
    """Yield every file path referenced by a scenario9.csv row, relative to
    dataroot. Skips columns that point to heavy unused blobs."""
    candidates = [
        "unit1_pwr_60ghz",      # beam power vector
        "unit1_lidar",          # BS lidar .mat
        "unit1_loc",            # BS GPS
        "unit2_loc_cal",        # Tx vehicle GPS
        "unit2_loc",            # Tx vehicle GPS (raw)
        "unit1_rgb",            # BS camera (keep — small)
    ]
    if include_radar:
        candidates.append("unit1_radar")
    for col in candidates:
        if col in row and row[col]:
            yield row[col].lstrip("./")


def prune_deepsense(
    src: Path, dst: Path, sequence: int, include_radar: bool, include_camera: bool,
) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    idx_csv = src / "scenario9.csv"
    if not idx_csv.exists():
        raise SystemExit(f"scenario9.csv not found in {src}")

    with idx_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        all_rows = list(reader)

    seq_rows = [r for r in all_rows if int(r.get("seq_index", -1)) == sequence]
    if not seq_rows:
        raise SystemExit(f"sequence {sequence} not found in {idx_csv}")

    # Write a pruned scenario9.csv with just the selected rows.
    dst_csv = dst / "scenario9.csv"
    with dst_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in seq_rows:
            writer.writerow(r)
    print(f"  DeepSense: kept {len(seq_rows)}/{len(all_rows)} rows from "
          f"scenario9.csv (seq {sequence})", file=sys.stderr)

    # Copy referenced files.
    kept, missing, skipped_cam = 0, 0, 0
    for row in seq_rows:
        for rel in _deepsense_row_paths(row, include_radar):
            if not include_camera and rel.endswith(".jpg"):
                skipped_cam += 1
                continue
            src_file = src / rel
            if not src_file.exists():
                missing += 1
                continue
            _copy_file(src_file, dst / rel)
            kept += 1

    # resources/ is small and useful for the publisher's bbox lookups
    res_src = src / "resources"
    if res_src.exists():
        shutil.copytree(res_src, dst / "resources", dirs_exist_ok=True)

    print(f"  DeepSense: copied {kept} blobs ({missing} missing in source, "
          f"{skipped_cam} cameras skipped)", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nuscenes-src", required=True, type=Path,
                   help="Full nuScenes v1.0-mini directory")
    p.add_argument("--deepsense-src", required=True, type=Path,
                   help="Full DeepSense Scenario 9 directory")
    p.add_argument("--out", required=True, type=Path,
                   help="Output directory for the pruned subset")
    p.add_argument("--scene", default="scene-0061",
                   help="nuScenes scene to keep (default: scene-0061)")
    p.add_argument("--deepsense-sequence", type=int, default=1,
                   help="DeepSense sequence index to keep (default: 1)")
    p.add_argument("--include-radar-cubes", action="store_true",
                   help="Include DeepSense FMCW radar .mat cubes (~100 MB/seq)")
    p.add_argument("--skip-nuscenes-cameras", action="store_true",
                   help="Skip CAM_* sample files (smaller, but Rerun cam feeds go blank)")
    args = p.parse_args()

    for src in (args.nuscenes_src, args.deepsense_src):
        if not src.is_dir():
            raise SystemExit(f"source not found: {src}")

    nusc_dst = args.out / "nuscenes_scene"
    deep_dst = args.out / "deepsense_seq"
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"[subset] writing subset to {args.out}", file=sys.stderr)
    prune_nuscenes(args.nuscenes_src, nusc_dst, args.scene)
    if args.skip_nuscenes_cameras:
        for cam_dir in (nusc_dst / "samples").glob("CAM_*"):
            shutil.rmtree(cam_dir)
            print(f"  dropped: samples/{cam_dir.name}", file=sys.stderr)

    prune_deepsense(
        args.deepsense_src, deep_dst,
        sequence=args.deepsense_sequence,
        include_radar=args.include_radar_cubes,
        include_camera=True,
    )

    print(f"[subset] nuScenes subset: {_human(_sizeof(nusc_dst))}", file=sys.stderr)
    print(f"[subset] DeepSense subset: {_human(_sizeof(deep_dst))}", file=sys.stderr)
    print(f"[subset] total: {_human(_sizeof(args.out))}", file=sys.stderr)
    print(f"\nPoint run_docker_demo.sh at these dirs:\n"
          f"  export NUSCENES_DATAROOT={nusc_dst.resolve()}\n"
          f"  export DEEPSENSE_DATAROOT={deep_dst.resolve()}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
