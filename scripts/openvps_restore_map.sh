#!/usr/bin/env bash
# Put the fountain map onto a fresh OpenVPS instance and serve it.
#
#     scripts/openvps_restore_map.sh i-0123456789abcdef0
#
# Restores the aligned, georeferenced map from S3, then loads and verifies it.
# After this the instance announces a VPS over DDS and localizes from pixels.
#
# Why this is a script and not three commands in a README: the instance cannot
# reach S3. Its role carries AmazonSSMManagedInstanceCore plus three narrow
# inline policies and nothing for S3, so `aws s3 cp` on the box fails with no
# credentials -- which reads like a network fault and is not one. The transfer
# goes through a presigned URL generated here, from whatever credentials the
# caller has, and the instance only ever runs curl.
set -euo pipefail

INSTANCE="${1:-}"
if [[ -z "$INSTANCE" ]]; then
  echo "usage: $0 <instance-id>" >&2
  exit 2
fi

REGION="${AWS_REGION:-us-east-1}"
BUCKET="${MAP_BUCKET:-openvps-scans-f0c7ce49c6a5}"
KEY="${MAP_KEY:-fountain-aligned-20260831.tar.gz}"
SHA256="${MAP_SHA256:-1953bc5da5d45e83d6cd34ab70f6a26e1e9bed4c6cd68526dc45c5179474184d}"
DATASET="${MAP_DATASET:-e6c9dced-7c03-494a-8ac0-581da857c13c}"
MAP="${MAP_ID:-b1afa008-6c71-4877-b831-70b113d601dc}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Not fountain2_map.tar.gz, which sits in the same bucket and predates both the
# metric alignment and the georeference. It restores to a map that localizes
# over HTTP and is never announced, because 1.7 coverage is geographic and a map
# with latitude null has nothing truthful to advertise.
echo "== map    s3://${BUCKET}/${KEY}"
echo "   into   ${INSTANCE}  (dataset ${DATASET:0:8}…, map ${MAP:0:8}…)"

echo "== presigning a GET (15 min)"
URL="$(aws s3 presign "s3://${BUCKET}/${KEY}" --expires-in 900 --region "$REGION")"

# Checksummed on the instance against the value recorded when the archive was
# built. A truncated download unpacks far enough to look fine and then fails
# inside the localizer, hours later, as a map that will not load.
REMOTE=$(cat <<EOF
set -e
curl -fsS --max-time 1800 -o /tmp/map.tar.gz '${URL}'
got=\$(sha256sum /tmp/map.tar.gz | cut -d' ' -f1)
if [ "\$got" != "${SHA256}" ]; then
  echo "checksum mismatch"; echo "  got      \$got"; echo "  expected ${SHA256}"; exit 1
fi
echo "  sha256 ok (\$(stat -c%s /tmp/map.tar.gz) bytes)"
mkdir -p /home/ubuntu/data/maps
tar -C /home/ubuntu/data/maps -xzf /tmp/map.tar.gz
rm -f /tmp/map.tar.gz
chown -R ubuntu:ubuntu /home/ubuntu/data/maps/${DATASET}
echo "  unpacked:"
du -sh /home/ubuntu/data/maps/${DATASET}/hlocMaps/${MAP} | sed 's/^/    /'
EOF
)

echo "== restoring over SSM"
CID=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript --timeout-seconds 3600 \
  --parameters "commands=[\"echo $(printf '%s' "$REMOTE" | base64 | tr -d '\n') | base64 -d > /tmp/_r.sh; bash /tmp/_r.sh 2>&1\"]" \
  --query 'Command.CommandId' --output text)

for _ in $(seq 1 240); do
  ST=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CID" \
       --instance-id "$INSTANCE" --query Status --output text 2>/dev/null || echo Pending)
  case "$ST" in Success|Failed|TimedOut|Cancelled) break;; esac
  sleep 5
done
aws ssm get-command-invocation --region "$REGION" --command-id "$CID" \
  --instance-id "$INSTANCE" --query 'StandardOutputContent' --output text
if [[ "$ST" != "Success" ]]; then
  echo "restore failed ($ST)" >&2
  aws ssm get-command-invocation --region "$REGION" --command-id "$CID" \
    --instance-id "$INSTANCE" --query 'StandardErrorContent' --output text >&2
  exit 1
fi

# --load is not optional and not implied by the files being present: nothing
# loads a map at boot, and a localizer holding no map neither localizes nor
# announces, so discovery reports no VPS at all.
echo "== load + verify"
python3 "${REPO}/scripts/openvps_prepare_map.py" --instance "$INSTANCE" \
  --dataset "$DATASET" --map "$MAP" --load --verify

cat <<'NEXT'

== next
  The localizer now holds the map and announces over DDS. To point the demo
  task at this instance, put its private IP and security group into
  deploy/aws/config.yaml and redeploy:

    dds_peers: "udp/<private-ip>"
    security_group_ids: "<this stack's GpuSecurityGroup>"
    deploy/aws/deploy.sh

  A running task will not find a peer that was absent when it started, so an
  existing task needs --force-new-deployment even when config.yaml is unchanged.
NEXT
