#!/usr/bin/env bash
# Print download instructions for the datasets the demo uses.
#
# We DO NOT redistribute nuScenes or DeepSense 6G data. Both require the
# user to accept their own license/terms on download:
#
#   nuScenes v1.0-mini (CC BY-NC-SA 4.0):
#       https://www.nuscenes.org/nuscenes#download
#   DeepSense 6G Scenario 9 (research use, per dataset agreement):
#       https://www.deepsense6g.net/scenario-9/
#
# After you've downloaded and extracted each dataset, put (or symlink) it
# at the paths below and the demo launcher will pick it up automatically.
#
# Optional: run scripts/make_demo_subset.py afterwards to carve a ~100 MB
# reproducible subset into multi_operator_fusion/data/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEMO_DIR}/.." && pwd)"

NUSCENES_PATH_FULL="${REPO_ROOT}/nuscenes/data/v1.0-mini"
DEEPSENSE_PATH_FULL="${REPO_ROOT}/nuscenes/scenario9_dev"
SUBSET_NUSC="${DEMO_DIR}/data/nuscenes_scene"
SUBSET_DEEP="${DEMO_DIR}/data/deepsense_seq"

status() { printf "  [%s] %s\n" "$1" "$2"; }

echo ""
echo "=== Demo data status ==="
echo ""

need_instructions=0

if [[ -d "${SUBSET_NUSC}" ]]; then
  status OK "nuScenes subset present: ${SUBSET_NUSC}"
elif [[ -d "${NUSCENES_PATH_FULL}" ]]; then
  status OK "nuScenes full dataset present: ${NUSCENES_PATH_FULL}"
else
  status "!!" "nuScenes missing"
  need_instructions=1
fi

if [[ -d "${SUBSET_DEEP}" ]]; then
  status OK "DeepSense subset present: ${SUBSET_DEEP}"
elif [[ -d "${DEEPSENSE_PATH_FULL}" ]]; then
  status OK "DeepSense full dataset present: ${DEEPSENSE_PATH_FULL}"
else
  status "!!" "DeepSense missing"
  need_instructions=1
fi

if [[ "${need_instructions}" == 0 ]]; then
  echo ""
  echo "You're ready. Run the demo with:"
  echo "  bash multi_operator_fusion/run_docker_demo.sh"
  exit 0
fi

cat <<EOF

=== Download instructions ===

We don't redistribute the raw datasets — both require you to accept their
own terms of use. Grab each from the upstream source:

1. nuScenes v1.0-mini (~5 GB; the demo only reads ~700 MB of it)
   - Register (free) and accept the license at:
       https://www.nuscenes.org/nuscenes#download
   - Download the "Mini" archive (v1.0-mini.tgz).
   - Extract to: ${NUSCENES_PATH_FULL}
     (so that v1.0-mini/*.json, samples/, maps/ live directly under it)

2. DeepSense 6G Scenario 9 (~7 GB; the demo only reads ~150 MB of it)
   - Register (free) and accept the agreement at:
       https://www.deepsense6g.net/scenario-9/
   - Download the "Development dataset" archive.
   - Extract to: ${DEEPSENSE_PATH_FULL}
     (so that scenario9.csv, unit1/, unit2/ live directly under it)

Once both are in place, re-run this script to confirm.

=== Optional: build a lighter subset (~100 MB) ===

After the downloads complete, you can carve the files actually used by the
demo into multi_operator_fusion/data/ so future runs skip the 12 GB of
unused blobs:

  python multi_operator_fusion/scripts/make_demo_subset.py \\
    --nuscenes-src ${NUSCENES_PATH_FULL} \\
    --deepsense-src ${DEEPSENSE_PATH_FULL} \\
    --out ${DEMO_DIR}/data \\
    --skip-nuscenes-cameras       # optional, saves another ~50 MB

The launcher auto-detects the subset when present and otherwise falls
back to the full datasets — either works.
EOF

exit 1
