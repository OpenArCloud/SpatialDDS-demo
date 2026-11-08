#!/usr/bin/env bash
# The claims tooth: a hazard claim bends the robot's route, a killed hazard
# stops bending it, and an untrusted one never did. docs/ros-claims.md.
#
#   tools/claims_tooth.sh OUT_DIR            # run, then check
#   tools/claims_tooth.sh --check OUT_DIR    # check a recorded run
#
# Runs the demo's own nav2 tier image in a container of its own, on its own
# DDS domain (71) and its own network, so a running stack and tier are
# neither read nor disturbed. The library is mounted from a checkout rather
# than taken from the image while the consuming half is unreleased:
# SPATIALDDS_ROS_SRC, default the sibling ../spatialdds-ros.
#
# Plants, for failability (docs/ros-claims.md lists what each must break):
#   NAV2_PARAMS=/out/<file>      a parameter file of your own, under OUT_DIR
#   KILL_SIGNAL=TERM             a clean stop where the arm wants a crash
#   SPATIALDDS_CLAIMS_TRUST=...  the robot's trust rule, replaced
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${ROBOT_TIER_IMAGE:-spatialdds-robot-tier:latest}"
LIB="${SPATIALDDS_ROS_SRC:-${REPO}/../spatialdds-ros}"

check_only=0
if [[ "${1:-}" == "--check" ]]; then check_only=1; shift; fi
OUT="${1:?usage: tools/claims_tooth.sh [--check] OUT_DIR}"
mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"

if (( ! check_only )); then
  LIB="$(cd "${LIB}" && pwd)"
  if [[ -n "$(ls -A "${OUT}")" && ! -f "${OUT}/.plant" ]]; then
    echo "${OUT} is not empty; a run records into a fresh directory" >&2
    exit 1
  fi
  name="claims_tooth_$$"
  echo "claims_tooth: ${name}, library from ${LIB}, recording to ${OUT}"
  docker run --rm --name "${name}" \
    -v "${REPO}:/ws:ro" -v "${LIB}:/opt/spatialdds-ros:ro" -v "${OUT}:/out" -w /ws \
    -e PYTHONPATH=/opt/spatialdds-ros \
    -e SPATIALDDS_DDS_DOMAIN=71 \
    -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp -e CYCLONEDDS_HOME=/usr/local \
    -e ROS_DOMAIN_ID=71 \
    ${NAV2_PARAMS:+-e NAV2_PARAMS="${NAV2_PARAMS}"} \
    ${KILL_SIGNAL:+-e KILL_SIGNAL="${KILL_SIGNAL}"} \
    ${SPATIALDDS_CLAIMS_TRUST:+-e SPATIALDDS_CLAIMS_TRUST="${SPATIALDDS_CLAIMS_TRUST}"} \
    "${IMAGE}" bash /ws/robot_tier/_claims_tooth_run.sh
fi

exec python3 "${REPO}/tools/claims_teeth.py" "${OUT}"
