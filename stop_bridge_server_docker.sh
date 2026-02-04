#!/usr/bin/env bash
set -u

container_id="$(docker ps -q --filter "name=dds_bridge_")"
if [[ -z "${container_id}" ]]; then
  echo "No running bridge container found."
  exit 0
fi

echo "Stopping bridge container: ${container_id}"
docker stop "${container_id}"
