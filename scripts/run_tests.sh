#!/usr/bin/env bash
#
# Every suite in this repo, in three tiers by cost.
#
#   scripts/run_tests.sh            fast      ~20s   host only
#   scripts/run_tests.sh standard   + DDS     ~3min  adds the container suites
#   scripts/run_tests.sh full       + ROS 2   ~25min adds ROS 2 and MQTT tiers
#
# Written because the container tiers were documented as "run whichever covers
# what you touched", which fails precisely when a change touches shared code
# and nobody notices. Two bugs lived six days behind a green host suite: the
# ROS 2 bridge could not start, and MCAP replay silently dropped a sample on a
# QoS mismatch. Only the container tiers see either.
#
# Every tier prints PASS/FAIL per suite and exits non-zero if any failed, so a
# suite that quietly stops running cannot look like one that passes.
#
set -uo pipefail
cd "$(dirname "$0")/.."

TIER="${1:-fast}"
FAILED=()
PASSED=0

run() {
  local name="$1"; shift
  printf '\n\033[1m── %s\033[0m\n' "$name"
  if "$@"; then
    printf '\033[32mPASS\033[0m  %s\n' "$name"
    PASSED=$((PASSED + 1))
  else
    printf '\033[31mFAIL\033[0m  %s\n' "$name"
    FAILED+=("$name")
  fi
}

have_docker() { docker info >/dev/null 2>&1; }
in_image() {
  docker run --rm --network host -v "$PWD:/app" -w /app -e PYTHONPATH=/app \
    -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python "$@"
}

# The repo is bind-mounted into the demo image, and the host and container run
# different Python versions. Bytecode written by one has twice now been read by
# the other as a truncated .pyc -- once killing the model publisher inside the
# container with `EOFError: EOF read where object expected`, once producing a
# spurious test failure on the host after a module was edited mid-run. Neither
# had anything to do with the code under test. Not writing it at all costs a
# few milliseconds per run and removes the whole class.
export PYTHONDONTWRITEBYTECODE=1

# ── fast: host only ─────────────────────────────────────────────────────────
run "host suite" python3 -m pytest -q \
  ar_demo/test_ar_demo_services.py \
  multi_operator_fusion \
  bridges/ros2_bridge/test_conversions.py \
  bridges/ros2_bridge/test_bridge_node.py \
  bridges/mqtt_bridge \
  bridges/web_bridge/test_router.py \
  bridges/web_bridge/test_client.py \
  bridges/web_bridge/test_dashboard_routes.py \
  bridges/web_bridge/test_discovery_http.py \
  bridges/web_bridge/test_wellknown_endpoints.py \
  bridges/web_bridge/test_model_cache.py \
  tests/test_catalog_filter.py \
  tests/test_command_lane.py \
  tests/test_autostart_gating.py \
  tests/test_duck_mover.py \
  bridges/mcap_bridge \
  tests/ \
  nuscenes/test_nuscenes_shapes.py \
  deepsense/test_deepsense_shapes.py

run "ar_demo protocol" bash -c 'cd ar_demo && PYTHONPATH=.. python3 spatialdds_demo_tests.py'

if [ "$TIER" = "fast" ]; then
  :
else
  if ! have_docker; then
    echo "Docker is not available; cannot run the $TIER tier." >&2
    FAILED+=("docker unavailable")
  else
    # ── standard: the container suites that need a real bus ────────────────
    run "IDL compile + protocol"  docker run --rm cyclonedds-python
    run "interop probe"           in_image python3 -m unittest tests.test_interop
    run "head-of-line isolation"  in_image python3 -m unittest tests.test_head_of_line
    run "ROS 2 DDS round-trip"    in_image python3 -m unittest bridges.ros2_bridge.test_dds_roundtrip
    run "MCAP live record→replay" in_image bash -lc \
      'python3 -m pip install -q mcap zstandard 2>/dev/null; python3 bridges/mcap_bridge/test_live.py'
    run "web bridge HTTP"         bash run_bridge_http_tests_docker.sh

    if [ "$TIER" = "full" ]; then
      # ── full: slow tiers. ROS 2 emulates amd64 on Apple Silicon. ─────────
      run "MQTT tier-2 (Mosquitto)" bash -c \
        'cd bridges/mqtt_bridge && docker compose -f docker-compose.test.yaml up \
           --abort-on-container-exit --exit-code-from tests'
      run "ROS 2 all tiers"        bash bridges/ros2_bridge/run_docker_tests.sh
    fi
  fi
fi

printf '\n\033[1m════ %s tier: %d passed, %d failed ════\033[0m\n' \
  "$TIER" "$PASSED" "${#FAILED[@]}"
if [ "${#FAILED[@]}" -gt 0 ]; then
  printf '  \033[31m%s\033[0m\n' "${FAILED[@]}"
  exit 1
fi
