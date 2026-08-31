#!/usr/bin/env python3
"""
Generate a small rubber duck as a self-contained .glb.

Written rather than downloaded so the asset's provenance is this file: no
third-party model, no licence to track, and the shape is adjustable. It is a
deliberately simple duck — an ellipsoid body, a sphere head, a cone beak and
two eyes — because at the size it appears in the demo (a few tens of pixels,
floating in a fountain) nothing more survives the projection.

glTF 2.0 binary: a JSON chunk describing the scene, then one binary chunk
holding interleaved-by-attribute vertex data. Positions, normals and indices
per primitive, one primitive per material.
"""
import argparse
import json
import struct
from pathlib import Path

import numpy as np


def uv_sphere(rings=16, sectors=24):
    """Unit sphere as (positions, normals, indices)."""
    v, idx = [], []
    for i in range(rings + 1):
        phi = np.pi * i / rings
        for j in range(sectors + 1):
            theta = 2 * np.pi * j / sectors
            v.append([np.sin(phi) * np.cos(theta), np.cos(phi), np.sin(phi) * np.sin(theta)])
    for i in range(rings):
        for j in range(sectors):
            a = i * (sectors + 1) + j
            b = a + sectors + 1
            idx += [a, b, a + 1, a + 1, b, b + 1]
    p = np.array(v, dtype=np.float32)
    return p, p.copy(), np.array(idx, dtype=np.uint32)


def cone(radius=1.0, height=1.0, sectors=20):
    """Cone along +Z, apex at +height, as (positions, normals, indices)."""
    v = [[0, 0, height]]
    for j in range(sectors):
        t = 2 * np.pi * j / sectors
        v.append([radius * np.cos(t), radius * np.sin(t), 0.0])
    idx = []
    for j in range(sectors):
        idx += [0, 1 + j, 1 + (j + 1) % sectors]
    p = np.array(v, dtype=np.float32)
    n = p / np.maximum(np.linalg.norm(p, axis=1, keepdims=True), 1e-6)
    return p, n.astype(np.float32), np.array(idx, dtype=np.uint32)


def place(p, n, scale, offset):
    s = np.asarray(scale, dtype=np.float32)
    out = p * s + np.asarray(offset, dtype=np.float32)
    # Non-uniform scale needs the inverse-transpose for normals, or the
    # shading goes subtly wrong on the squashed body.
    nn = n / s
    nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-6)
    return out.astype(np.float32), nn.astype(np.float32)


def build():
    """Parts grouped by material, so each material is one primitive."""
    sp, sn, si = uv_sphere()
    cp, cn, ci = cone()
    groups = {
        "body": ([], [], []),   # yellow
        "beak": ([], [], []),   # orange
        "eyes": ([], [], []),   # near-black
    }

    def add(group, p, n, i):
        pos, nor, ind = groups[group]
        base = sum(len(x) for x in pos)
        pos.append(p); nor.append(n); ind.append(i + base)

    body_p, body_n = place(sp, sn, (0.62, 0.50, 0.85), (0, 0.50, 0))
    add("body", body_p, body_n, si)
    tail_p, tail_n = place(sp, sn, (0.20, 0.22, 0.34), (0, 0.62, -0.86))
    add("body", tail_p, tail_n, si)
    head_p, head_n = place(sp, sn, (0.36, 0.36, 0.36), (0, 1.06, 0.46))
    add("body", head_p, head_n, si)

    beak_p, beak_n = place(cp, cn, (0.16, 0.10, 0.30), (0, 0.99, 0.72))
    # the cone points +Z already; nudge it forward out of the head
    add("beak", beak_p, beak_n, ci)

    for sx in (-1, 1):
        e_p, e_n = place(sp, sn, (0.055, 0.055, 0.055), (0.17 * sx, 1.17, 0.70))
        add("eyes", e_p, e_n, si)

    out = {}
    for name, (pos, nor, ind) in groups.items():
        out[name] = (np.concatenate(pos), np.concatenate(nor), np.concatenate(ind))
    return out


MATERIALS = {
    "body": ([1.00, 0.84, 0.10, 1.0], 0.35),
    "beak": ([0.95, 0.45, 0.05, 1.0], 0.45),
    "eyes": ([0.04, 0.04, 0.05, 1.0], 0.25),
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

    for mi, (name, (pos, nor, ind)) in enumerate(parts.items()):
        vi = add_view(pos.astype("<f4").tobytes(), 34962)
        accessors.append({"bufferView": vi, "componentType": 5126, "count": len(pos),
                          "type": "VEC3",
                          "min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()})
        a_pos = len(accessors) - 1
        ni = add_view(nor.astype("<f4").tobytes(), 34962)
        accessors.append({"bufferView": ni, "componentType": 5126, "count": len(nor),
                          "type": "VEC3"})
        a_nor = len(accessors) - 1
        ii = add_view(ind.astype("<u4").tobytes(), 34963)
        accessors.append({"bufferView": ii, "componentType": 5125, "count": len(ind),
                          "type": "SCALAR"})
        prims.append({"attributes": {"POSITION": a_pos, "NORMAL": a_nor},
                      "indices": len(accessors) - 1, "material": mi})

    gltf = {
        "asset": {"version": "2.0", "generator": "SpatialDDS-demo scripts/make_duck_glb.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "duck"}],
        "meshes": [{"name": "duck", "primitives": prims}],
        "materials": [
            {"name": n,
             "pbrMetallicRoughness": {"baseColorFactor": c,
                                      "metallicFactor": 0.0, "roughnessFactor": r}}
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
                    default=Path("web/public/models/duck.glb"))
    a = ap.parse_args()
    parts = build()
    n = write_glb(parts, a.out)
    tris = sum(len(i) // 3 for _, _, i in parts.values())
    print(f"  wrote {a.out} — {n/1024:.1f} KB, {tris} triangles, "
          f"{len(parts)} materials")
