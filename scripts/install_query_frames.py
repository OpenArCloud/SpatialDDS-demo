#!/usr/bin/env python3
"""
Install a query-frame bundle from a local OpenVPS map.

The AR demo's "Localize with Image" button sends real photographs from the scan
a VPS map was built from. Those are pictures of a real place, and the manifest
carries that place's coordinates, so nothing here is committed: the bundle is
built locally into web/public/query-frames/, which is git-ignored.

Point this at an OpenVPS dataset directory — the one holding `status.json` and
`hlocMaps/<map-id>/` — and it copies a few registered frames and writes the
manifest the app reads:

    scripts/install_query_frames.py ~/maps/<dataset-id> --count 3

Frames are taken from the map's own registered images on purpose. A 1.7
VpsRequest has nowhere to carry camera intrinsics, so OpenVPS falls back to the
map's camera model: right for query images drawn from the map, silently wrong
for a foreign camera.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

DEST = Path(__file__).resolve().parent.parent / "web" / "public" / "query-frames"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", type=Path,
                    help="OpenVPS dataset dir (contains status.json and hlocMaps/)")
    ap.add_argument("--map-id", help="which map under hlocMaps/ (default: the only one)")
    ap.add_argument("--count", type=int, default=3, help="frames to install (default 3)")
    ap.add_argument("--dest", type=Path, default=DEST)
    args = ap.parse_args()

    maps_dir = args.dataset / "hlocMaps"
    if not maps_dir.is_dir():
        ap.error(f"{args.dataset} has no hlocMaps/ — is this an OpenVPS dataset directory?")

    candidates = sorted(p for p in maps_dir.iterdir() if p.is_dir())
    if args.map_id:
        chosen = maps_dir / args.map_id
        if not chosen.is_dir():
            ap.error(f"no map {args.map_id} under {maps_dir}")
    elif len(candidates) == 1:
        chosen = candidates[0]
    else:
        ap.error(f"{len(candidates)} maps under {maps_dir}; pass --map-id "
                 f"({', '.join(p.name for p in candidates)})")

    transform = chosen / "transform.json"
    if not transform.is_file():
        ap.error(f"{chosen} has no transform.json, so the map has no georeference "
                 "and the demo would have no anchor to search around")
    xf = json.loads(transform.read_text())
    for key in ("latitude", "longitude"):
        if key not in xf:
            ap.error(f"{transform} has no '{key}'")

    images = chosen / "prior_model" / "images"
    if not images.is_dir():
        ap.error(f"{chosen} has no prior_model/images/")
    frames = sorted(p for p in images.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
    if not frames:
        ap.error(f"no JPEGs in {images}")

    # Spread across the recording rather than taking the first N, so repeated
    # clicks localize from genuinely different viewpoints.
    n = min(args.count, len(frames))
    picked = [frames[round(i * (len(frames) - 1) / max(n - 1, 1))] for i in range(n)]

    dest = args.dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for f in picked:
        shutil.copy2(f, dest / f.name)

    label = "scan"
    status = args.dataset / "status.json"
    if status.is_file():
        try:
            label = json.loads(status.read_text())["metadata"].get("name", label)
        except Exception:
            pass

    manifest = {
        "label": label,
        "mapId": chosen.name,
        "datasetId": args.dataset.name,
        "anchor": {
            "lat_deg": float(xf["latitude"]),
            "lon_deg": float(xf["longitude"]),
            "alt_m": float(xf.get("height", 0) or 0),
        },
        "frames": [f.name for f in picked],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    total = sum((dest / f.name).stat().st_size for f in picked)
    print(f"installed {n} frames ({total // 1024} KB) from {label} into {dest}")
    print(f"  map {chosen.name}")
    print("  the stand-in VPS needs coverage over this map; ar_demo/README.md "
          "shows the environment for that")
    return 0


if __name__ == "__main__":
    sys.exit(main())
