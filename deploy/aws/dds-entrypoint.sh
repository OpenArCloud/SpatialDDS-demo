#!/usr/bin/env bash
#
# Render the CycloneDDS config, then exec the container's real command.
#
# The peer list cannot be baked into the image: it names addresses that are
# not known until deploy time, and change when a peer instance is replaced.
# So the image carries a template and this fills it in.
#
# SPATIALDDS_DDS_PEERS is space- or comma-separated, in Cyclone's peer syntax:
#
#     SPATIALDDS_DDS_PEERS="udp/10.0.1.42 udp/10.0.2.17"
#
# Empty is the normal single-task case and renders an empty <Peers/>, which is
# what lets participants in one network namespace discover each other by local
# port probing.
#
set -euo pipefail

TEMPLATE=${SPATIALDDS_DDS_TEMPLATE:-/etc/cyclonedds.xml.in}
TARGET=${SPATIALDDS_DDS_CONFIG:-/etc/cyclonedds.xml}

if [ -f "$TEMPLATE" ]; then
  peers=""
  # `:-` because the variable is genuinely optional: the CDK always sets it,
  # but docker-compose and a bare `docker run` do not, and `set -u` would
  # otherwise abort the container before its real command ever ran.
  configured=${SPATIALDDS_DDS_PEERS:-}
  for peer in ${configured//,/ }; do
    peers="${peers}<Peer address=\"${peer}\"/>"
  done
  # `|` as the sed delimiter: peer addresses contain slashes.
  sed "s|<!--PEERS-->|${peers}|" "$TEMPLATE" > "$TARGET"
  if [ -n "$peers" ]; then
    echo "dds: unicast peers -> ${configured}"
  else
    echo "dds: no peers configured (single-host discovery)"
  fi
fi

exec "$@"
