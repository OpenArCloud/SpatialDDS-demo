#!/usr/bin/env bash
# One-button deploy of the SpatialDDS demo to AWS Fargate.
#
# Prerequisites (the script checks each):
#   - aws CLI v2 with credentials configured (`aws configure`)
#   - AWS CDK v2 (`npm i -g aws-cdk`)
#   - Docker daemon running (CDK uses it to build the image asset)
#   - Python 3.9+ to run the CDK app
#
# Usage:
#   deploy/aws/deploy.sh                             # uses config.yaml
#   deploy/aws/deploy.sh path/to/other-config.yaml
set -euo pipefail
cd "$(dirname "$0")"

CONFIG_FILE="${1:-config.yaml}"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[deploy] config file not found: $CONFIG_FILE" >&2
  echo "         cp config.yaml.example config.yaml  # then edit aws_region / stack_name" >&2
  exit 1
fi

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "[deploy] missing prerequisite: $1" >&2; exit 1; }
}
require aws
require cdk
require docker
require python3

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "[deploy] AWS credentials not configured. Run: aws configure" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "[deploy] Docker daemon not reachable." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGION="$(python3 -c "import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))['aws_region'])" "$CONFIG_FILE")"
STACK_NAME="$(python3 -c "import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))['stack_name'])" "$CONFIG_FILE")"

echo "============================================"
echo "  SpatialDDS AWS Deployment"
echo "============================================"
echo "  Account:   $ACCOUNT_ID"
echo "  Region:    $REGION"
echo "  Stack:     $STACK_NAME"
echo "  Config:    $CONFIG_FILE"
echo ""

echo "[deploy] installing CDK Python deps..."
python3 -m pip install --quiet -r cdk/requirements.txt

echo "[deploy] bootstrapping CDK (idempotent)..."
( cd cdk && cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}" --quiet ) || true

# Pre-flight: ensure the cyclonedds-python-base image used by
# Dockerfile.deploy is available locally for linux/amd64 (the platform
# Fargate runs). The public GHCR mirror has been unreliable, and on an
# arm64 host the default ``docker build`` produces an arm64-only image
# which the CDK asset (which forces --platform linux/amd64) can't use.
# Always build the base for linux/amd64 here so deploy works on either
# host arch. (Idempotent — Docker reuses the cache when the Dockerfile
# and the apt/pip layers haven't changed.)
BASE_TAG="ghcr.io/openarcloud/cyclonedds-python-base:0.10.5-ubuntu22.04"
echo "[deploy] ensuring base image ${BASE_TAG} is available for linux/amd64..."
( cd ../.. && docker build --platform=linux/amd64 \
    -f Dockerfile.base -t "$BASE_TAG" . )

OUTPUTS_FILE="$(pwd)/outputs.json"
echo "[deploy] cdk deploy (this typically takes 5–10 minutes)..."
(
  cd cdk
  cdk deploy \
      --context "config_file=../$CONFIG_FILE" \
      --require-approval never \
      --outputs-file "$OUTPUTS_FILE"
)

echo ""
echo "============================================"
echo "  Deployment Complete"
echo "============================================"

python3 - <<PY
import json, sys
with open("${OUTPUTS_FILE}") as f:
    outputs = json.load(f) or {}
if not outputs:
    print("(no outputs reported)")
    sys.exit(0)
stack = next(iter(outputs.values()))
for label, key in [("Dashboard ", "DashboardURL"),
                    ("WebSocket ", "WebSocketURL"),
                    ("Topics API", "TopicsAPI"),
                    ("Health    ", "HealthURL"),
                    ("Base URL  ", "BaseURL"),
                    ("Recording ", "RecordingBucketName")]:
    val = stack.get(key)
    if val:
        print(f"  {label}: {val}")
base = stack.get("BaseURL", "<base>")
print()
print("  Smoke-test the deployment:")
print(f"    BASE='{base}' \\")
print( "      python3 deploy/aws/smoke_test.py")
print()
print("  Tear down: deploy/aws/destroy.sh")
PY
