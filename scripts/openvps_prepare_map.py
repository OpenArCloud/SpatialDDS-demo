#!/usr/bin/env python3
"""
Reproduce, in one command, the map-side preparation an OpenVPS map needs
before it can be georeferenced and served.

A freshly built map is not ready to use:

  * The reconstruction is in COLMAP's arbitrary frame. Measured on the UT
    fountain capture, its +Y sat 79.7 degrees from true up, at 2.3047 m per
    map unit. `hloc_metric_alignment --mode rescale_model` rotates and scales
    it into the ARKit prior frame, making it level and metric. The pipeline
    cannot do this itself: `HlocConfigurationStage` hardcodes
    `coord_scale_only`, with a TODO, so `metric_alignment_mode` in the API
    request is accepted, echoed back, and ignored.
  * `sparse.ply` does not follow the reconstruction. Rotating the model leaves
    the PLY that MapAligner renders untouched, so the aligner keeps showing the
    old orientation with nothing to indicate it is stale.
  * `transform.json` comes out with latitude/longitude/height null, so the map
    is localizable but deliberately never announced over DDS. It needs a
    georeference, which is what --transform applies.

Everything runs over SSM against the instance; nothing here needs the map
locally. Each step backs up what it replaces, and --verify reports the state
so a run can be checked rather than assumed.
"""
import argparse
import base64
import json
import subprocess
import sys
import time


def ssm(instance, script, region="us-east-1", timeout=900):
    """Run a shell script on the instance and return its stdout."""
    b64 = base64.b64encode(script.encode()).decode()
    cmd = subprocess.run(
        ["aws", "ssm", "send-command", "--region", region,
         "--instance-ids", instance, "--document-name", "AWS-RunShellScript",
         "--timeout-seconds", str(timeout),
         "--parameters", json.dumps({"commands": [
             f"echo '{b64}' | base64 -d > /tmp/_step.sh", "bash /tmp/_step.sh"]}),
         "--query", "Command.CommandId", "--output", "text"],
        capture_output=True, text=True)
    if cmd.returncode:
        sys.exit(f"send-command failed: {cmd.stderr.strip()}")
    cid = cmd.stdout.strip()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(10)
        got = subprocess.run(
            ["aws", "ssm", "get-command-invocation", "--region", region,
             "--command-id", cid, "--instance-id", instance,
             "--query", "[Status,StandardOutputContent,StandardErrorContent]",
             "--output", "json"], capture_output=True, text=True)
        if got.returncode:
            continue
        status, out, err = json.loads(got.stdout)
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            if status != "Success":
                print(out or "", file=sys.stderr)
                sys.exit(f"step {status}: {(err or '').strip()[:400]}")
            return out or ""
    sys.exit("timed out waiting for the command")


ALIGN = r"""
set -e
D=/home/ubuntu/data/maps/@DS@/hlocMaps/@MAP@
C=/uploads/@DS@/hlocMaps/@MAP@
[ -d "$D" ] || { echo "no such map: $D"; exit 1; }
# Back up only on the first run, so re-running does not overwrite the originals
# with already-transformed copies.
for f in hloc_reconstruction transform.json sparse.ply; do
  [ -e "$D/$f.bak" ] || cp -r "$D/$f" "$D/$f.bak"
done
echo "-- metric alignment (rescale_model)"
docker exec openvps-backend-1 python3 /app/scripts/hloc_metric_alignment.py \
  --prior_model_path    $C/prior_model \
  --reconstruction_path $C/hloc_reconstruction \
  --transform_json_path $C/transform.json \
  --mode rescale_model
echo "-- regenerating sparse.ply from the rotated model"
docker exec openvps-backend-1 python3 /app/scripts/colmap_model_export_ply.py \
  --input_model_dir $C/hloc_reconstruction --output_ply_path $C/sparse.ply
echo "-- reloading the map"
curl -s http://127.0.0.1:8000/unload_map/@MAP@ >/dev/null || true
curl -s --max-time 600 http://127.0.0.1:8000/load_map/@MAP@
echo
"""

LOAD = r"""
# Load a map into maplocalizer. Nothing does this on boot: `openvps.service`
# brings the compose stack back after a restart, and maplocalizer comes up
# healthy holding no map. Until a map is loaded it cannot localize *and* it
# does not announce, so the demo's discovery finds no VPS at all -- which
# reads as a broken deployment rather than an unloaded one.
set -e
curl -s http://127.0.0.1:8000/unload_map/@MAP@ >/dev/null || true
echo "-- loading @MAP@ (cold load pulls SuperPoint/SuperGlue weights; ~30-60 s)"
curl -s --max-time 600 http://127.0.0.1:8000/load_map/@MAP@
echo
"""

TRANSFORM = r"""
set -e
MB=$(grep '^MAPBUILDER_URL=' /opt/openvps/docker.env | cut -d= -f2-)
C=/tmp/py-cookies.txt
[ -f $C ] || { echo "no session; run oauth-login.py on the instance first"; exit 1; }
cat > /tmp/_transform.json <<'JSON'
@PAYLOAD@
JSON
code=$(curl -s -o /tmp/_tr.out -w '%{http_code}' -b $C -X POST \
  -H 'Content-Type: application/json' -d @/tmp/_transform.json \
  "$MB/maps/@DS@/hloc/@MAP@/transform")
echo "POST transform -> HTTP $code"
head -c 200 /tmp/_tr.out; echo
curl -s http://127.0.0.1:8000/unload_map/@MAP@ >/dev/null || true
curl -s --max-time 600 http://127.0.0.1:8000/load_map/@MAP@
echo
"""

VERIFY = r"""
D=/home/ubuntu/data/maps/@DS@/hlocMaps/@MAP@
C=/uploads/@DS@/hlocMaps/@MAP@
echo "-- transform.json"
cat $D/transform.json | tr -d '\n '
echo
echo "-- geometry"
docker exec openvps-maplocalizer-1 python3 -c "
import numpy as np, pycolmap, json
rec = pycolmap.Reconstruction('$C/hloc_reconstruction')
pri = pycolmap.Reconstruction('$C/prior_model')
def c(r):
    o={}
    for im in r.images.values():
        try: o[im.name]=np.array(im.projection_center(),dtype=float)
        except Exception: o[im.name]=np.array(im.cam_from_world().inverse().translation,dtype=float)
    return o
a,b=c(rec),c(pri); n=sorted(set(a)&set(b))
X=np.stack([a[k] for k in n]); Y=np.stack([b[k] for k in n])
Xc,Yc=X-X.mean(0),Y-Y.mean(0)
U,S,Vt=np.linalg.svd(Xc.T@Yc); d=np.sign(np.linalg.det(U@Vt))
R=U@np.diag([1,1,d])@Vt
ang=np.degrees(np.arccos(np.clip((np.trace(R)-1)/2,-1,1)))
scale=(S*np.array([1,1,d])).sum()/(Xc**2).sum()
print(f'  images {len(n)}  rotation-from-prior {ang:.2f} deg  scale {scale:.4f}')
print(f'  per-image offset mean {np.linalg.norm(X-Y,axis=1).mean():.3f} m')
print('  aligned' if ang<1 and abs(scale-1)<0.02 else '  NOT aligned - run --align')
"
echo "-- files"
ls -l --time-style=+%Y-%m-%dT%H:%M $D/sparse.ply $D/transform.json 2>/dev/null | awk '{print "  " $6, $7}'
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", required=True, help="EC2 instance id")
    ap.add_argument("--dataset", required=True, help="OpenVPS dataset id")
    ap.add_argument("--map", required=True, dest="map_id", help="OpenVPS map id")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--align", action="store_true",
                    help="metric-align the reconstruction, regenerate sparse.ply, reload")
    ap.add_argument("--transform", metavar="FILE",
                    help="POST a transform.json — the aligner's output — and reload")
    ap.add_argument("--load", action="store_true",
                    help="load the map into maplocalizer (what a restart needs)")
    ap.add_argument("--verify", action="store_true",
                    help="report whether the map is aligned and georeferenced")
    a = ap.parse_args()
    if not (a.align or a.transform or a.load or a.verify):
        ap.error("nothing to do: pass --align, --transform, --load and/or --verify")

    def fill(template, **extra):
        """Token substitution, not str.format: these templates are shell and
        Python, and every brace in them is real syntax rather than a field."""
        out = template.replace("@DS@", a.dataset).replace("@MAP@", a.map_id)
        for k, v in extra.items():
            out = out.replace(f"@{k.upper()}@", v)
        return out

    if a.align:
        print(ssm(a.instance, fill(ALIGN), a.region))
    if a.transform:
        payload = json.dumps(json.load(open(a.transform)), indent=1)
        if "latitude" not in payload:
            sys.exit(f"{a.transform} has no latitude; is this the aligner's output?")
        print(ssm(a.instance, fill(TRANSFORM, payload=payload), a.region))
    if a.load:
        print(ssm(a.instance, fill(LOAD), a.region))
    if a.verify:
        print(ssm(a.instance, fill(VERIFY), a.region))


if __name__ == "__main__":
    main()
