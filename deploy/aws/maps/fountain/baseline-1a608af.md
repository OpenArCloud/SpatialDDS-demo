# Baseline — the deployed ref, before the rebuild

Captured 2026-10-01 against the running deployment, so the rebuild at
`31b3df8` has something to be compared with rather than judged by eye.

```
openvps ref      1a608af  (openvps @ spatialdds, 2026-08-29)
map              b1afa008-6c71-4877-b831-70b113d601dc
announced as     svc:vps:oarc/aws-test;v=b1afa008-6c71-4877-b831-70b113d601dc
path measured    POST /v1/localize on the demo's ALB, over DDS to the GPU
```

The demo's own path, not the localizer's private HTTP API — the point is what
the demo does, and `/localize/geopose` needs a hand-built GeoPose v2.0 envelope
carrying camera parameters, which is exactly the thing `31b3df8` changes.

## Map geometry

```
images 269   rotation-from-prior 0.00 deg   scale 1.0000
per-image offset mean 0.083 m
aligned, georeferenced
```

## Poses, two passes per frame

```
frame              status         lat            lon           alt      conf   rmse    s
frame_0005.jpg     VPS_SUCCESS    30.28378129   -97.73979090   145.617  1.000  0.0000  4.9
frame_0383.jpg     VPS_SUCCESS    30.28376706   -97.73977255   145.452  1.000  0.0000  4.9
frame_0595.jpg     VPS_SUCCESS    30.28374246   -97.73971716   145.235  1.000  0.0000  5.3
frame_0794.jpg     VPS_SUCCESS    30.28373637   -97.73965370   145.232  1.000  0.0000  4.6
frame_1007.jpg     VPS_SUCCESS    30.28374923   -97.73959138   145.243  1.000  0.0000  4.9
frame_1233.jpg     VPS_SUCCESS    30.28376720   -97.73953124   145.249  1.000  0.0000  4.5
```

4.5-5.3 s per localize, inside the 3.6-5.7 s this repo already documents.

**Distinct frames give distinct poses** — about 5 m of latitude and 22 m of
longitude across the six, which is the walk around the fountain they were taken
on. That is the check that pixels are being used: a stand-in returning
prior-plus-jitter cannot produce a track.

**A repeated frame reproduces its pose to sub-millimetre, not bit-for-bit.**
Pass-to-pass differences were ~1e-9 degrees (about 0.1 mm) in latitude and
longitude, and up to 0.9 mm in altitude (`frame_0794`). The repo elsewhere says
a repeated frame "reproduces its pose exactly"; that is the right idea and
slightly too strong. RANSAC inside PnP is not deterministic across runs, so a
comparison after the rebuild should use a tolerance, and anything at millimetre
scale is agreement rather than drift.

Tolerance to judge the rebuild by: **1e-7 degrees and 10 mm**. That is two
orders of magnitude above the noise measured here and still far below any real
change in a pose.

`rmse_m` came back exactly 0.0000 on every frame while `confidence` was 1.000.
Both look like placeholder values from the binding rather than measurements, and
neither should be read as a quality signal. Not investigated.
