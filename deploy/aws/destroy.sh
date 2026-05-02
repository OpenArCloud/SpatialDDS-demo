#!/usr/bin/env bash
# Tear down the SpatialDDS Fargate stack.
#
# Usage:
#   deploy/aws/destroy.sh                # uses config.yaml; prompts before destroy
#   deploy/aws/destroy.sh --yes          # skip confirmation (CI-friendly)
#   deploy/aws/destroy.sh path.yaml --yes
set -euo pipefail
cd "$(dirname "$0")"

CONFIG_FILE=""
YES=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)   YES=1 ;;
    -*)         echo "[destroy] unknown flag: $arg" >&2; exit 2 ;;
    *)          CONFIG_FILE="$arg" ;;
  esac
done
CONFIG_FILE="${CONFIG_FILE:-config.yaml}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[destroy] config file not found: $CONFIG_FILE" >&2
  exit 1
fi

STACK_NAME="$(python3 -c "import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))['stack_name'])" "$CONFIG_FILE")"

echo "[destroy] About to remove stack: ${STACK_NAME}"
echo "          (ALB, ECS service, task definition, VPC, log group will go away)"
echo "          The S3 RecordingBucket — if you enabled recording — is RETAINed"
echo "          and must be emptied + deleted manually."

if [[ "$YES" -ne 1 ]]; then
  read -p "Continue? (y/N) " -r confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || { echo "[destroy] cancelled."; exit 0; }
fi

(
  cd cdk
  cdk destroy --context "config_file=../$CONFIG_FILE" --force
)
echo "[destroy] done."
