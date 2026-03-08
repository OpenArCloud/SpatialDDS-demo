#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
FIGURES_DIR="${SCRIPT_DIR}/figures"

mkdir -p "${RESULTS_DIR}" "${FIGURES_DIR}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[run_all] docker is required" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "[run_all] docker daemon not reachable" >&2
  exit 1
fi

if ! docker image inspect cyclonedds-python:latest >/dev/null 2>&1; then
  echo "[run_all] building cyclonedds-python image" >&2
  (cd "${REPO_ROOT}" && docker build -t cyclonedds-python .)
fi

cleanup() {
  if [[ "${STACK_MODE:-}" == "compose" ]]; then
    if command -v docker-compose >/dev/null 2>&1; then
      (cd "${REPO_ROOT}" && docker-compose down >/dev/null 2>&1 || true)
    else
      (cd "${REPO_ROOT}" && docker compose down >/dev/null 2>&1 || true)
    fi
  elif [[ "${STACK_MODE:-}" == "bridge-script" ]]; then
    "${REPO_ROOT}/stop_bridge_server_docker.sh" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

run_python_in_container() {
  docker run --rm --network host \
    -v "${REPO_ROOT}:/app" \
    -w /app/benchmarks \
    -e PYTHONUNBUFFERED=1 \
    -e PYTHONPATH=/app \
    -e SPATIALDDS_TRANSPORT=dds \
    -e SPATIALDDS_DDS_DOMAIN=1 \
    -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
    cyclonedds-python bash -lc "$1"
}

wait_for_health() {
  local url="http://localhost:8088/health"
  local attempts="${1:-60}"
  local delay=2

  for ((i=1; i<=attempts; i++)); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      echo "[run_all] bridge healthy at ${url}" >&2
      return 0
    fi
    sleep "${delay}"
  done
  return 1
}

start_stack() {
  if command -v docker-compose >/dev/null 2>&1; then
    echo "[run_all] starting docker-compose stack" >&2
    (cd "${REPO_ROOT}" && docker-compose up -d)
    STACK_MODE="compose"
    if wait_for_health 20; then
      return 0
    fi
    echo "[run_all] compose stack started but health check failed, falling back" >&2
    (cd "${REPO_ROOT}" && docker-compose down >/dev/null 2>&1 || true)
  elif command -v docker >/dev/null 2>&1; then
    echo "[run_all] starting docker compose stack" >&2
    (cd "${REPO_ROOT}" && docker compose up -d)
    STACK_MODE="compose"
    if wait_for_health 20; then
      return 0
    fi
    echo "[run_all] compose stack started but health check failed, falling back" >&2
    (cd "${REPO_ROOT}" && docker compose down >/dev/null 2>&1 || true)
  fi

  if [[ -x "${REPO_ROOT}/run_bridge_server_docker.sh" ]]; then
    echo "[run_all] starting bridge stack via run_bridge_server_docker.sh" >&2
    "${REPO_ROOT}/run_bridge_server_docker.sh" >/dev/null 2>&1 &
    STACK_MODE="bridge-script"
    if wait_for_health 180; then
      return 0
    fi
  fi

  echo "[run_all] unable to start SpatialDDS Docker stack (health endpoint unavailable)" >&2
  return 1
}

cd "${SCRIPT_DIR}"
start_stack

run_python_in_container "python3 -m pip install -r /app/requirements.txt -r /app/benchmarks/requirements.txt"
run_python_in_container "python3 bench_latency.py --iterations 1000 --output results/latency.csv"
run_python_in_container "python3 bench_discovery.py --services 1,5,10,25,50,100 --iterations 50 --output results/discovery.csv"
run_python_in_container "python3 bench_multioperator.py --operators 1,2,5,10,20 --duration 30 --output results/multioperator.csv"
run_python_in_container "python3 bench_coverage_query.py --entries 10,50,100,500,1000 --iterations 100 --output results/coverage_query.csv"
run_python_in_container "python3 plot_results.py --input results/ --output figures/"

cleanup
trap - EXIT INT TERM

{
  echo "[run_all] benchmark outputs:" 
  ls -1 "${RESULTS_DIR}"/*.csv
  echo "[run_all] figures:" 
  ls -1 "${FIGURES_DIR}"/*.pdf
} >&2
