#!/usr/bin/env python3
"""
Generate a small mobile robot as a self-contained .glb.

Written rather than downloaded, for the same reason `make_duck_glb.py` was:
the asset's provenance is this file. No third-party mesh, no licence to
track, no question about whether a TurtleBot3 model someone posted is
redistributable. It is a recognisable silhouette rather than a likeness --
a stacked cylindrical body, two wheels, a caster and a spinning lidar can --
because at the size it appears in the demo nothing more survives.

The shape faces +X, which is the frame convention the seeder documents:
identity orientation points a model's authored forward along the venue
frame's east, and the robot bridge writes a yaw about Z on top of that.

glTF 2.0 binary, same structure as the duck: a JSON chunk describing the
scene, one binary chunk of interleaved-by-attribute vertex data, one
primitive per material.
"""

import argparse
import json
import struct
from pathlib import Path

import numpy as np



def cylinder(radius=1.0, height=1.0, sectors=24, axis="z"):
    """Closed cylinder as (positions, normals, indices)."""
    v, n, idx = [], [], []
    for j in range(sectors + 1):
        t = 2 * np.pi * j / sectors
        c, s = np.cos(t), np.sin(t)
        for h in (0.0, height):
            v.append([radius * c, radius * s, h])
            n.append([c, s, 0.0])
    for j in range(sectors):
        a = j * 2
        idx += [a, a + 2, a + 1, a + 1, a + 2, a + 3]
    # Caps, with their own flat normals.
    for h, nz, wind in ((0.0, -1.0, (0, 2, 1)), (height, 1.0, (0, 1, 2))):
        centre = len(v)
        v.append([0.0, 0.0, h]); n.append([0.0, 0.0, nz])
        first = len(v)
        for j in range(sectors):
            t = 2 * np.pi * j / sectors
            v.append([radius * np.cos(t), radius * np.sin(t), h])
            n.append([0.0, 0.0, nz])
        for j in range(sectors):
            ring = [centre, first + j, first + (j + 1) % sectors]
            idx += [ring[wind[0]], ring[wind[1]], ring[wind[2]]]
    p = np.array(v, dtype=np.float32)
    nn = np.array(n, dtype=np.float32)
    if axis == "y":                      # wheels lie on their side
        p = p[:, [0, 2, 1]] * np.array([1, 1, -1], dtype=np.float32)
        nn = nn[:, [0, 2, 1]] * np.array([1, 1, -1], dtype=np.float32)
    return p, nn, np.array(idx, dtype=np.uint32)


def uv_sphere(rings=10, sectors=14):
    v, idx = [], []
    for i in range(rings + 1):
        phi = np.pi * i / rings
        for j in range(sectors + 1):
            theta = 2 * np.pi * j / sectors
            v.append([np.sin(phi) * np.cos(theta), np.cos(phi),
                      np.sin(phi) * np.sin(theta)])
    for i in range(rings):
        for j in range(sectors):
            a = i * (sectors + 1) + j
            b = a + sectors + 1
            idx += [a, b, a + 1, a + 1, b, b + 1]
    p = np.array(v, dtype=np.float32)
    return p, p.copy(), np.array(idx, dtype=np.uint32)


def place(p, n, scale, offset):
    s = np.asarray(scale, dtype=np.float32)
    out = p * s + np.asarray(offset, dtype=np.float32)
    nn = n / s
    nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-6)
    return out.astype(np.float32), nn.astype(np.float32)


def build():
    """
    Parts grouped by material. Roughly TurtleBot3-Burger proportions: about
    14 cm across the plates, 19 cm to the top of the lidar, which at venue
    scale makes it a little smaller than a duck is long.
    """
    groups = {"plate": ([], [], []), "wheel": ([], [], []),
              "lidar": ([], [], []), "trim": ([], [], [])}

    def add(group, p, n, i):
        pos, nor, ind = groups[group]
        base = sum(len(x) for x in pos)
        pos.append(p); nor.append(n); ind.append(i + base)

    cy_p, cy_n, cy_i = cylinder()
    wh_p, wh_n, wh_i = cylinder(sectors=16, axis="y")
    sp_p, sp_n, sp_i = uv_sphere()

    # Three stacked plates on standoffs -- the Burger's silhouette.
    for z in (0.04, 0.10, 0.155):
        p, n = place(cy_p, cy_n, (0.070, 0.070, 0.012), (0, 0, z))
        add("plate", p, n, cy_i)
    for sx, sy in ((0.045, 0.045), (0.045, -0.045), (-0.045, 0.045), (-0.045, -0.045)):
        p, n = place(cy_p, cy_n, (0.005, 0.005, 0.115), (sx, sy, 0.040))
        add("trim", p, n, cy_i)

    # Wheels either side, and a caster at the back.
    for sy in (-1, 1):
        p, n = place(wh_p, wh_n, (0.033, 0.012, 0.033), (0.0, 0.080 * sy, 0.033))
        add("wheel", p, n, wh_i)
    p, n = place(sp_p, sp_n, (0.015, 0.015, 0.015), (-0.055, 0.0, 0.017))
    add("wheel", p, n, sp_i)

    # The lidar can on top: the thing that makes it read as a robot.
    p, n = place(cy_p, cy_n, (0.036, 0.036, 0.030), (0, 0, 0.167))
    add("lidar", p, n, cy_i)
    # A notch on the front face, so its heading is legible when it turns.
    p, n = place(cy_p, cy_n, (0.008, 0.008, 0.032), (0.030, 0, 0.166))
    add("trim", p, n, cy_i)

    return {name: (np.concatenate(pos), np.concatenate(nor), np.concatenate(ind))
            for name, (pos, nor, ind) in groups.items()}


MATERIALS = {
    "plate": ([0.14, 0.16, 0.20, 1.0], 0.45),
    "wheel": ([0.06, 0.06, 0.07, 1.0], 0.70),
    "lidar": ([0.85, 0.87, 0.90, 1.0], 0.30),
    "trim":  ([0.55, 0.58, 0.62, 1.0], 0.35),
}


def write_glb(parts, path: Path):
    buf = bytearray()
    accessors, views, prims = [], [], []

    def add_view(data: bytes, target: int) -> int:
        while len(buf) % 4:
            buf.append(0)
        views.append({"buffer": 0, "byteOffset": len(buf),
                      "byteLength": len(data), "target": target})
        buf.extend(data)
        return len(views) - 1

    for name, (pos, nor, ind) in parts.items():
        pv = add_view(pos.astype(np.float32).tobytes(), 34962)
        nv = add_view(nor.astype(np.float32).tobytes(), 34962)
        iv = add_view(ind.astype(np.uint32).tobytes(), 34963)
        accessors.append({"bufferView": pv, "componentType": 5126,
                          "count": len(pos), "type": "VEC3",
                          "min": pos.min(axis=0).tolist(),
                          "max": pos.max(axis=0).tolist()})
        accessors.append({"bufferView": nv, "componentType": 5126,
                          "count": len(nor), "type": "VEC3"})
        accessors.append({"bufferView": iv, "componentType": 5125,
                          "count": len(ind), "type": "SCALAR"})
        base = len(accessors) - 3
        prims.append({"attributes": {"POSITION": base, "NORMAL": base + 1},
                      "indices": base + 2, "material": len(prims)})

    gltf = {
        "asset": {"version": "2.0",
                  "generator": "SpatialDDS-demo scripts/make_tb3_glb.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": prims}],
        "materials": [
            {"name": n, "doubleSided": True,
             "pbrMetallicRoughness": {"baseColorFactor": c,
                                      "metallicFactor": 0.0,
                                      "roughnessFactor": r}}
            for n, (c, r) in ((n, MATERIALS[n]) for n in parts)
        ],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(buf)}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    bin_ = bytes(buf) + b"\0" * ((4 - len(buf) % 4) % 4)
    glb = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
           + struct.pack("<II", len(js), 0x4E4F534A) + js
           + struct.pack("<II", len(bin_), 0x004E4942) + bin_)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(glb)
    return len(glb)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", type=Path,
                    default=Path("web/public/models/tb3.glb"))
    a = ap.parse_args()
    parts = build()
    n = write_glb(parts, a.out)
    tris = sum(len(i) // 3 for _, _, i in parts.values())
    print(f"  wrote {a.out} — {n/1024:.1f} KB, {tris} triangles, "
          f"{len(parts)} materials")
