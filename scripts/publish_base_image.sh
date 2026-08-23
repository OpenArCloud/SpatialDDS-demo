#!/usr/bin/env bash
#
# Publish the CycloneDDS + Python base image to GHCR, multi-arch.
#
# `Dockerfile` FROMs ghcr.io/openarcloud/cyclonedds-python-base:11.0.1-ubuntu22.04,
# which is not in the registry: only the superseded 0.10.5-ubuntu22.04 tag was
# ever pushed, and that one carries a linux/arm64 manifest only. Until this runs,
# every `docker build -t cyclonedds-python .` on a machine without a locally
# built base fails at the FROM.
#
# Needs a GHCR login with `write:packages` on the openarcloud org:
#
#     echo "$GITHUB_TOKEN" | docker login ghcr.io -u <your-github-user> --password-stdin
#
# Then, from the repo root:
#
#     scripts/publish_base_image.sh                 # both arches, push
#     DRY_RUN=1 scripts/publish_base_image.sh       # show what would run
#     PLATFORMS=linux/amd64 scripts/publish_base_image.sh
#
# The package itself is already public, so no visibility change is needed for a
# new tag under it — GHCR inherits the package's visibility. (Verify after
# pushing: the check at the end pulls the tag list with an anonymous token, so
# it fails if the tag landed private.)
#
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/openarcloud/cyclonedds-python-base}"
VERSION="${VERSION:-11.0.1-ubuntu22.04}"
TAG="$IMAGE:$VERSION"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
BUILDER="${BUILDER:-spatialdds-base}"
DRY_RUN="${DRY_RUN:-}"

cd "$(dirname "$0")/.."

run() {
  printf '  $ %s\n' "$*"
  [ -n "$DRY_RUN" ] || "$@"
}

echo "Publishing $TAG for $PLATFORMS"
echo

# buildx with a container driver — the default "docker" driver cannot build
# more than the host's own architecture, and cross-building the CycloneDDS C
# library under QEMU is exactly what this needs.
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  echo "Creating buildx builder '$BUILDER'"
  run docker buildx create --name "$BUILDER" --driver docker-container --bootstrap
fi
run docker buildx use "$BUILDER"

echo
echo "Building and pushing (the arm64 leg compiles CycloneDDS under emulation;"
echo "expect this to take a while)"
run docker buildx build \
  --platform "$PLATFORMS" \
  -f Dockerfile.base \
  -t "$TAG" \
  --push \
  .

if [ -n "$DRY_RUN" ]; then
  echo
  echo "DRY_RUN set — nothing was built or pushed."
  exit 0
fi

echo
echo "Verifying the tag is anonymously pullable (i.e. actually public)"
token=$(curl -fsS "https://ghcr.io/token?service=ghcr.io&scope=repository:${IMAGE#ghcr.io/}:pull" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
status=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $token" \
  -H 'Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json' \
  "https://ghcr.io/v2/${IMAGE#ghcr.io/}/manifests/$VERSION")

if [ "$status" = "200" ]; then
  echo "  OK — $TAG is public"
  curl -fsS -H "Authorization: Bearer $token" \
    -H 'Accept: application/vnd.oci.image.index.v1+json' \
    "https://ghcr.io/v2/${IMAGE#ghcr.io/}/manifests/$VERSION" \
  | python3 -c '
import sys, json
for entry in json.load(sys.stdin).get("manifests", []):
    p = entry.get("platform", {})
    print("  platform:", p.get("os"), p.get("architecture"))
'
else
  echo "  HTTP $status anonymously — the tag pushed but the package is private."
  echo "  Make it public at:"
  echo "    https://github.com/orgs/openarcloud/packages/container/cyclonedds-python-base/settings"
  exit 1
fi
