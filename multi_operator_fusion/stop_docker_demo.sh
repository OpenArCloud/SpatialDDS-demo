#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RERUN_PID_FILE="${SCRIPT_DIR}/.rerun_demo.pid"

stopped=0

if [[ -f "${RERUN_PID_FILE}" ]]; then
  pid="$(cat "${RERUN_PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}" >/dev/null 2>&1 || true
    echo "[multi_op] stopped rerun pid ${pid}" >&2
    stopped=1
  fi
  rm -f "${RERUN_PID_FILE}"
fi

if command -v lsof >/dev/null 2>&1; then
  for port in 9876 9090; do
    pids="$(lsof -tiTCP:${port} -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "${pids}" ]]; then
      echo "${pids}" | xargs kill >/dev/null 2>&1 || true
      echo "[multi_op] stopped listener(s) on port ${port}" >&2
      stopped=1
    fi
  done
fi

if [[ "${stopped}" -eq 0 ]]; then
  echo "[multi_op] no running demo services found" >&2
fi
