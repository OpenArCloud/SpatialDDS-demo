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
  # Which interface RTPS binds and advertises.
  #
  # `autodetermine` is not safe here: on Fargate it selected loopback, so the
  # containers sharing the task's namespace discovered each other while every
  # packet to an off-host peer went nowhere — no error, a correct peer list, and
  # not one RTPS datagram arriving at the peer. Binding the address this
  # container actually routes from makes the choice explicit and visible.
  #
  # Found by connecting a UDP socket to a routable address and reading back the
  # local end: no packet is sent, and it needs no `ip`/`ifconfig` in the image.
  iface_addr=$(python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(('10.255.255.255', 1))
    print(s.getsockname()[0])
except OSError:
    pass
finally:
    s.close()
" 2>/dev/null || true)

  if [ -n "$iface_addr" ] && [ "$iface_addr" != "127.0.0.1" ]; then
    interface="<NetworkInterface address=\"${iface_addr}\"/>"
    echo "dds: interface -> ${iface_addr}"
  else
    # No routable address (an isolated container, say). Autodetermine is the
    # only option left, and single-host discovery still works over loopback.
    interface='<NetworkInterface autodetermine="true"/>'
    echo "dds: interface -> autodetermine (no routable address found)"
  fi

  # `|` as the sed delimiter: peer addresses contain slashes.
  sed -e "s|<!--PEERS-->|${peers}|" -e "s|<!--INTERFACE-->|${interface}|" \
      "$TEMPLATE" > "$TARGET"
  if [ -n "$peers" ]; then
    echo "dds: unicast peers -> ${configured}"
  else
    echo "dds: no peers configured (single-host discovery)"
  fi
fi

exec "$@"
