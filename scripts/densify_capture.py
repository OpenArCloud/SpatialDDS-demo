#!/usr/bin/env python3
"""
Dense point cloud from a StrayScanner capture, in the map's own frame.

The sparse COLMAP cloud an OpenVPS map ships is ~11k points — too thin to
recognise a place by eye. The capture carries per-frame LiDAR/ARKit depth that
the mapping pipeline never uses for output, and once a map has been metrically
aligned to the ARKit prior (hloc_metric_alignment --mode rescale_model) the
reconstruction frame *is* the ARKit frame, so those depth frames back-project
straight into map coordinates with no extra transform.

Verify that assumption before trusting the result: --validate compares the
dense cloud against the map's sparse.ply.
"""
import argparse, csv, io, subprocess, sys, tempfile, zipfile
from pathlib import Path
import numpy as np
from PIL import Image


def quat_to_R(qx, qy, qz, qw):
    n = np.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
    qx, qy, qz, qw = qx/n, qy/n, qz/n, qw/n
    return np.array([
        [1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw),   2*(qx*qz+qy*qw)],
        [2*(qx*qy+qz*qw),   1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],
        [2*(qx*qz-qy*qw),   2*(qy*qz+qx*qw),   1-2*(qx*qx+qy*qy)]])


def read_odometry(zf, stem):
    rows = []
    with zf.open(f"{stem}/odometry.csv") as fh:
        for r in csv.DictReader(io.TextIOWrapper(fh, "utf-8")):
            r = { (k or "").strip(): (v or "").strip() for k, v in r.items() }
            rows.append(r)
    return rows


def extract_rgb(zip_path, stem, w, h, outdir):
    """Video frames at depth resolution — colour per depth pixel, cheaply."""
    with zipfile.ZipFile(zip_path) as zf, open(outdir/"rgb.mp4", "wb") as out:
        out.write(zf.read(f"{stem}/rgb.mp4"))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(outdir/"rgb.mp4"),
         "-vf", f"scale={w}:{h}", "-f", "image2", str(outdir/"f%06d.png")],
        check=True)
    (outdir/"rgb.mp4").unlink()
    return sorted(outdir.glob("f*.png"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", type=Path, help="StrayScanner capture .zip")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output .ply")
    ap.add_argument("--stride", type=int, default=4, help="use every Nth frame")
    ap.add_argument("--voxel", type=float, default=0.03, help="downsample size, m")
    ap.add_argument("--max-depth", type=float, default=30.0,
                    help="drop beyond, m. ARKit reports out to ~25 m here; a "
                         "tighter cap silently truncates the scene — at 20 m "
                         "this capture lost half its building height (7.2 m "
                         "instead of 15.6) and looked mis-scaled rather than "
                         "cropped.")
    ap.add_argument("--min-confidence", type=int, default=2, choices=(0, 1, 2))
    ap.add_argument("--keep-sky", action="store_true",
                    help="keep sky-coloured points. ARKit gives sky pixels a "
                         "finite depth, so they land as a blue haze at roughly "
                         "building height; they are dropped by default because "
                         "they obscure exactly what alignment needs to see.")
    ap.add_argument("--validate", type=Path, help="map sparse.ply to compare against")
    a = ap.parse_args()

    zf = zipfile.ZipFile(a.zip)
    stem = zf.namelist()[0].split("/")[0]
    odo = read_odometry(zf, stem)
    depths = sorted(n for n in zf.namelist() if "/depth/" in n and n.endswith(".png"))
    print(f"  capture {stem}: {len(odo)} poses, {len(depths)} depth frames")

    d0 = np.array(Image.open(io.BytesIO(zf.read(depths[0]))))
    dh, dw = d0.shape
    print(f"  depth {dw}x{dh}, using every {a.stride} frame(s)")

    tmp = Path(tempfile.mkdtemp())
    rgb_frames = extract_rgb(a.zip, stem, dw, dh, tmp)
    print(f"  rgb frames extracted: {len(rgb_frames)}")

    pts, cols = [], []
    used = 0
    for i in range(0, len(depths), a.stride):
        name = depths[i]
        idx = int(Path(name).stem)
        if idx >= len(odo):
            continue
        r = odo[idx]
        try:
            fx, fy = float(r["fx"]), float(r["fy"])
            cx, cy = float(r["cx"]), float(r["cy"])
            t = np.array([float(r["x"]), float(r["y"]), float(r["z"])])
            R = quat_to_R(float(r["qx"]), float(r["qy"]), float(r["qz"]), float(r["qw"]))
        except (KeyError, ValueError):
            continue
        # Intrinsics are for the full-res RGB frame; depth is a scaled copy.
        s = dw / (cx * 2.0)
        fx, fy, cx, cy = fx*s, fy*s, cx*s, cy*s

        d = np.array(Image.open(io.BytesIO(zf.read(name)))).astype(np.float32) / 1000.0
        cname = name.replace("/depth/", "/confidence/")
        try:
            conf = np.array(Image.open(io.BytesIO(zf.read(cname))))
        except KeyError:
            conf = np.full_like(d, 2, dtype=np.uint8)

        m = (d > 0.1) & (d < a.max_depth) & (conf >= a.min_confidence)
        if not m.any():
            continue
        v, u = np.nonzero(m)
        z = d[v, u]
        # Camera convention determined empirically, not assumed: of the four
        # sign choices, only this one produces a ground plane (39.9% of points
        # within 20 cm of the modal height; the others give 19-30%). Paired
        # with the world flip below it reproduces the map frame.
        cam = np.stack([(u - cx) * z / fx, (v - cy) * z / fy, z], axis=1)
        pts.append(cam @ R.T + t)

        if idx < len(rgb_frames):
            c = np.array(Image.open(rgb_frames[idx]).convert("RGB"))
            cols.append(c[v, u])
        else:
            cols.append(np.full((len(v), 3), 200, np.uint8))
        used += 1

    if not pts:
        sys.exit("no points survived filtering")
    P = np.concatenate(pts); C = np.concatenate(cols)
    if not a.keep_sky:
        r, g, b = C[:, 0].astype(int), C[:, 1].astype(int), C[:, 2].astype(int)
        sky = (b > r + 25) & (b > 110) & (g > r)
        print(f"  dropped {int(sky.sum()):,} sky-coloured points")
        P, C = P[~sky], C[~sky]
    # stray_to_colmap left-multiplies diag(1,-1,-1) onto the camera-to-world
    # pose, which flips the *world*, not the camera. So the map frame is the
    # ARKit frame with Y and Z negated. Verified exactly: flipped ARKit camera
    # centres match the map's own to 0.00 m.
    P = P @ np.diag([1.0, -1.0, -1.0])
    print(f"  frames used: {used}   raw points: {len(P):,}")

    # Voxel downsample: one point per occupied cell, first hit wins.
    key = np.floor(P / a.voxel).astype(np.int64)
    _, keep = np.unique(key, axis=0, return_index=True)
    P, C = P[keep], C[keep]
    print(f"  after {a.voxel*100:.0f} cm voxel: {len(P):,} points")

    with open(a.out, "wb") as f:
        f.write(b"ply\nformat binary_little_endian 1.0\n")
        f.write(f"element vertex {len(P)}\n".encode())
        f.write(b"property float x\nproperty float y\nproperty float z\n"
                b"property uchar red\nproperty uchar green\nproperty uchar blue\n"
                b"end_header\n")
        rec = np.empty(len(P), dtype=[('x','<f4'),('y','<f4'),('z','<f4'),
                                      ('r','u1'),('g','u1'),('b','u1')])
        rec['x'], rec['y'], rec['z'] = P[:,0], P[:,1], P[:,2]
        rec['r'], rec['g'], rec['b'] = C[:,0], C[:,1], C[:,2]
        f.write(rec.tobytes())
    print(f"  wrote {a.out} ({a.out.stat().st_size/1e6:.1f} MB)")

    if a.validate:
        import re
        fh = open(a.validate, "rb"); h = b""
        while not h.endswith(b"end_header\n"): h += fh.read(1)
        n = int(re.search(rb"element vertex (\d+)", h).group(1))
        dt = np.dtype([('x','<f4'),('y','<f4'),('z','<f4'),('r','u1'),('g','u1'),('b','u1')])
        s = np.frombuffer(fh.read(n*dt.itemsize), dtype=dt, count=n)
        S = np.stack([s['x'], s['y'], s['z']], 1)
        for label, X in (("sparse", S), ("dense", P)):
            lo, hi = np.percentile(X, [5, 95], axis=0)
            print(f"  {label:7} centroid {np.round(X.mean(0),2).tolist()}  "
                  f"5-95 extent {np.round(hi-lo,1).tolist()}")
        off = np.linalg.norm(P.mean(0) - S.mean(0))
        print(f"  centroid offset: {off:.2f} m  "
              f"({'frames agree' if off < 5 else 'MISMATCH — check conventions'})")


if __name__ == "__main__":
    main()
