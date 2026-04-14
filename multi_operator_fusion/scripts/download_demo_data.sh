#!/usr/bin/env bash
# Download the pruned demo dataset into multi_operator_fusion/data/.
#
# The subset (~80 MB) is hosted externally so the repo stays lightweight.
# Set MOF_DATA_URL in your environment if the hosted location changes.
#
#   bash multi_operator_fusion/scripts/download_demo_data.sh
#
# To regenerate the subset from full-fidelity sources, see make_demo_subset.py.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${DEMO_DIR}/data"

# EDIT: Replace with the published URL once the subset is uploaded.
#   - HuggingFace datasets: https://huggingface.co/datasets/<user>/<name>/resolve/main/demo_subset.tar.gz
#   - Zenodo:              https://zenodo.org/record/<id>/files/demo_subset.tar.gz
#   - GitHub release:      https://github.com/<user>/<repo>/releases/download/v1/demo_subset.tar.gz
MOF_DATA_URL="${MOF_DATA_URL:-REPLACE_ME_WITH_PUBLISHED_URL}"
ARCHIVE="${ARCHIVE:-${DATA_DIR}/demo_subset.tar.gz}"
EXPECTED_SHA256="${EXPECTED_SHA256:-}"  # optional integrity check

if [[ "${MOF_DATA_URL}" == "REPLACE_ME_WITH_PUBLISHED_URL" ]]; then
  cat >&2 <<EOF
[download] ERROR: MOF_DATA_URL is unset and no default is baked in.

To fix, either:
  1) Edit multi_operator_fusion/scripts/download_demo_data.sh and set MOF_DATA_URL
     to the published archive URL, or
  2) Export MOF_DATA_URL before running:
       export MOF_DATA_URL="https://example.com/demo_subset.tar.gz"
       bash multi_operator_fusion/scripts/download_demo_data.sh

Or regenerate the subset locally from full datasets:
  python multi_operator_fusion/scripts/make_demo_subset.py \\
    --nuscenes-src /path/to/nuscenes/v1.0-mini \\
    --deepsense-src /path/to/scenario9_dev \\
    --out ${DATA_DIR}
EOF
  exit 1
fi

if [[ -d "${DATA_DIR}/nuscenes_scene" && -d "${DATA_DIR}/deepsense_seq" ]]; then
  echo "[download] ${DATA_DIR} already populated; delete to re-fetch" >&2
  exit 0
fi

mkdir -p "${DATA_DIR}"
echo "[download] fetching ${MOF_DATA_URL}" >&2
curl -fL --progress-bar -o "${ARCHIVE}" "${MOF_DATA_URL}"

if [[ -n "${EXPECTED_SHA256}" ]]; then
  actual="$(shasum -a 256 "${ARCHIVE}" | awk '{print $1}')"
  if [[ "${actual}" != "${EXPECTED_SHA256}" ]]; then
    echo "[download] checksum mismatch: want ${EXPECTED_SHA256}, got ${actual}" >&2
    exit 1
  fi
fi

echo "[download] extracting into ${DATA_DIR}" >&2
tar -xzf "${ARCHIVE}" -C "${DATA_DIR}"
rm -f "${ARCHIVE}"

echo "[download] done. Run the demo with:" >&2
echo "  NUSCENES_DATAROOT=${DATA_DIR}/nuscenes_scene \\" >&2
echo "  DEEPSENSE_DATAROOT=${DATA_DIR}/deepsense_seq \\" >&2
echo "  bash multi_operator_fusion/run_docker_demo.sh" >&2
