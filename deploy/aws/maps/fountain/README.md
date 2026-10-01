# The UT Littlefield Fountain map

The map the OpenVPS demo localizes against, and — more to the point — **the
georeference somebody produced by hand in MapAligner.** The reconstruction can
be rebuilt from the scan. That placement cannot, and it is 352 bytes, so it
lives here in git rather than only on an EBS volume.

## Identifiers

```
dataset   e6c9dced-7c03-494a-8ac0-581da857c13c
map       b1afa008-6c71-4877-b831-70b113d601dc
anchor    lat 30.28396857092828   lon -97.73974829101985   height 145.083 m
geohash   9v6kr                   (precision 5, the cell discovery searches)
```

These were not written down anywhere before this file. They lived in one
person's shell history, and every doc in the repo said `<dataset-id>` and
`<map-id>` — which makes "anyone can reproduce this" false no matter how good
the rest of the instructions are.

## The two alignments, and why only one is kept

"Alignment" means two different things here, and conflating them is what makes
this look harder than it is.

**Metric alignment — reproducible, not kept.** A fresh reconstruction sits in
COLMAP's arbitrary frame. `hloc_metric_alignment --mode rescale_model` rotates
and scales it onto the **ARKit prior** recorded during the capture. On this map
that was +Y sitting 79.7° from true up at 2.3047 m per map unit.
`openvps_prepare_map.py --align` does it in one command, and `--verify` checks
it. The prior comes from the capture, which does not change — so this step
lands in the same place every time and there is nothing to preserve.

`transform.pre-align.json` is what that step leaves behind: a uniform 2.3047
scale, `height: 0`, and the capture's rough start position. Machine output,
kept only so the before/after is legible.

**The georeference — not reproducible, kept.** `transform.json` is MapAligner's
output: a real height and a rotation-plus-translation placing the aligned map on
the Earth. That is a human dragging a point cloud onto a basemap, and it is the
expensive artifact in this whole deployment.

A map with `latitude: null` stays localizable over HTTP and is **deliberately
never announced over DDS** — 1.7 coverage is entirely geographic, so a map with
no geodetic anchor has nothing truthful to advertise. Correct, and confusing if
unexpected: discovery simply reports no VPS.

## Why the georeference survives a rebuild

Because the metric alignment targets the prior rather than COLMAP's frame, a
rebuilt-and-realigned map lands in the same frame, and this `transform.json`
still applies. Measured on the deployed map, 2026-10-01:

```
images 269   rotation-from-prior 0.00 deg   scale 1.0000
per-image offset mean 0.083 m
aligned
```

0.00° and 1.0000 is the reconstruction sitting *exactly* in the prior frame.
That is the measurement the reuse argument rests on, which is why it is quoted
here rather than asserted.

## The full map archive

The reconstruction itself is too big for git:

```
s3://openvps-scans-f0c7ce49c6a5/fountain-aligned-20260831.tar.gz
  290801568 bytes
  sha256 1953bc5da5d45e83d6cd34ab70f6a26e1e9bed4c6cd68526dc45c5179474184d
```

Aligned **and** georeferenced, which the older `fountain2_map.tar.gz` in the
same bucket is not — that one predates both steps (2026-08-30 19:05, against
alignment at 23:48 and the georeference the following afternoon). Do not
restore the older archive expecting a working VPS.

Paths inside are relative to the maps root, so a restore is one `tar`:

```sh
# on the instance
aws s3 presign …          # from a laptop that can read the bucket
curl -o /tmp/m.tar.gz '<presigned GET>'
tar -C /home/ubuntu/data/maps -xzf /tmp/m.tar.gz
python3 scripts/openvps_prepare_map.py --instance <id> \
    --dataset e6c9dced-7c03-494a-8ac0-581da857c13c \
    --map b1afa008-6c71-4877-b831-70b113d601dc --load --verify
```

**The instance role cannot reach S3.** It carries only
`AmazonSSMManagedInstanceCore` plus three narrow inline policies — no S3 at
all. So transfers in either direction go through a presigned URL generated off
the instance; `aws s3 cp` on the box fails with no credentials. Worth knowing
before debugging it as a network problem.

Contents, 331 MB unpacked:

```
<dataset>/status.json
<dataset>/hlocMaps/<map>/config.yaml
<dataset>/hlocMaps/<map>/transform.json
<dataset>/hlocMaps/<map>/sparse.ply
<dataset>/hlocMaps/<map>/hloc_reconstruction      246 MB
<dataset>/hlocMaps/<map>/prior_model              85 MB, 269 images + ARKit poses
```

`prior_model` is **not** optional padding: it is the frame `--align` aligns to
and `--verify` measures against. Dropping it to save 85 MB costs the ability to
realign or check the map ever again.

Excluded as duplicates: `hloc_reconstruction.bak` (246 MB),
`hloc_reconstruction.zip` (194 MB), `sparse.ply.bak`, `transform.json.bak` —
440 MB of them, still sitting on the volume.

## Query frames

`web/public/query-frames/` (gitignored — photographs of a real place) holds six
frames from this scan with a manifest naming this dataset, map and anchor. They
are what **Localize with Image** sends, and the only way to get a pose from
pixels rather than from the stand-in's prior-plus-jitter.
