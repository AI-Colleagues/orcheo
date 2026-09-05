#!/usr/bin/env bash
set -euo pipefail

# Starts and stops the desktop app's own Redis instance, the Celery broker for
# the bundled worker and beat processes. Mirrors desktop-postgres.sh: the
# instance is loopback-only, keeps its data under the app support directory,
# and picks a port outside the range a normal Redis deployment would use.

ACTION="${1:-}"
APP_SUPPORT_DIR="${2:-}"
LOG_DIR="${3:-}"

if [[ "${ACTION}" == "" || "${APP_SUPPORT_DIR}" == "" || "${LOG_DIR}" == "" ]]; then
  echo "usage: desktop-redis.sh <start|stop|ping> <app-support-dir> <log-dir>" >&2
  exit 64
fi

REDIS_ROOT="${APP_SUPPORT_DIR}/redis"
REDIS_DATA_DIR="${REDIS_ROOT}/data"
REDIS_PORT_FILE="${REDIS_ROOT}/port"
REDIS_PID_FILE="${REDIS_ROOT}/redis.pid"
REDIS_LOG="${LOG_DIR}/redis.log"
REDIS_DB="${ORCHEO_DESKTOP_REDIS_DB:-0}"

mkdir -p "${REDIS_ROOT}" "${REDIS_DATA_DIR}" "${LOG_DIR}"

if [[ -f "${APP_SUPPORT_DIR}/desktop.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${APP_SUPPORT_DIR}/desktop.env"
  set +a
fi

find_redis_bin_dir() {
  if [[ "${ORCHEO_DESKTOP_REDIS_BIN_DIR:-}" != "" ]] \
    && [[ -x "${ORCHEO_DESKTOP_REDIS_BIN_DIR}/redis-server" ]]; then
    echo "${ORCHEO_DESKTOP_REDIS_BIN_DIR}"
    return
  fi

  local script_dir bundled_candidate
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  bundled_candidate="$(cd "${script_dir}/.." && pwd)/../redis/bin"
  if [[ -x "${bundled_candidate}/redis-server" ]]; then
    echo "${bundled_candidate}"
    return
  fi

  local server_path
  server_path="$(command -v redis-server || true)"
  if [[ "${server_path}" != "" ]]; then
    dirname "${server_path}"
    return
  fi

  local candidate
  for candidate in \
    /opt/homebrew/opt/redis/bin \
    /usr/local/opt/redis/bin; do
    if [[ -x "${candidate}/redis-server" ]]; then
      echo "${candidate}"
      return
    fi
  done
}

is_running_on_port() {
  local bin_dir="$1"
  local port="$2"
  if [[ ! -x "${bin_dir}/redis-cli" ]]; then
    nc -z 127.0.0.1 "${port}" >/dev/null 2>&1
    return
  fi
  [[ "$("${bin_dir}/redis-cli" -h 127.0.0.1 -p "${port}" ping 2>/dev/null)" == "PONG" ]]
}

is_port_free() {
  ! nc -z 127.0.0.1 "$1" >/dev/null 2>&1
}

choose_port() {
  local bin_dir="$1"

  # Reuse the saved port when our own server still owns it, so a restart keeps
  # whatever the worker and beat were already pointed at.
  if [[ -f "${REDIS_PORT_FILE}" ]]; then
    local saved
    saved="$(cat "${REDIS_PORT_FILE}")"
    if [[ "${saved}" =~ ^[0-9]+$ ]]; then
      if is_port_free "${saved}" || is_running_on_port "${bin_dir}" "${saved}"; then
        echo "${saved}"
        return
      fi
    fi
  fi

  local port
  for port in $(seq 26379 26478); do
    if is_port_free "${port}"; then
      echo "${port}" | tee "${REDIS_PORT_FILE}" >/dev/null
      echo "${port}"
      return
    fi
  done

  echo "No free desktop Redis port found in 26379-26478." >&2
  exit 70
}

start_local_redis() {
  local bin_dir="$1"
  local port="$2"

  if is_running_on_port "${bin_dir}" "${port}"; then
    echo "redis://127.0.0.1:${port}/${REDIS_DB}"
    return
  fi

  # protected-mode plus a loopback bind keeps the broker unreachable from the
  # network; appendonly keeps queued tasks across an unclean shutdown.
  "${bin_dir}/redis-server" \
    --bind 127.0.0.1 \
    --port "${port}" \
    --dir "${REDIS_DATA_DIR}" \
    --pidfile "${REDIS_PID_FILE}" \
    --logfile "${REDIS_LOG}" \
    --daemonize yes \
    --protected-mode yes \
    --appendonly yes \
    --tcp-backlog "${ORCHEO_DESKTOP_REDIS_TCP_BACKLOG:-128}"

  local attempt
  for attempt in $(seq 1 50); do
    if is_running_on_port "${bin_dir}" "${port}"; then
      echo "redis://127.0.0.1:${port}/${REDIS_DB}"
      return
    fi
    sleep 0.2
  done

  echo "Desktop Redis did not become ready on port ${port}; see ${REDIS_LOG}." >&2
  exit 70
}

stop_local_redis() {
  local bin_dir="$1"

  if [[ -f "${REDIS_PORT_FILE}" ]]; then
    local port
    port="$(cat "${REDIS_PORT_FILE}")"
    if [[ "${port}" =~ ^[0-9]+$ ]] && [[ -x "${bin_dir}/redis-cli" ]]; then
      # `shutdown` flushes the append-only file before exiting, so queued tasks
      # survive into the next launch.
      "${bin_dir}/redis-cli" -h 127.0.0.1 -p "${port}" shutdown >/dev/null 2>&1 || true
    fi
  fi

  if [[ -f "${REDIS_PID_FILE}" ]]; then
    local pid
    pid="$(cat "${REDIS_PID_FILE}")"
    if [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
    rm -f "${REDIS_PID_FILE}"
  fi
}

# Answers whether the broker named by ORCHEO_DESKTOP_REDIS_PING_URL is actually
# reachable. The desktop shells inherit REDIS_URL from the environment and used
# to take a non-empty string as proof of a live broker; a Celery worker pointed
# at a dead one retries forever instead of exiting, so nothing would notice.
ping_broker_url() {
  local url="${ORCHEO_DESKTOP_REDIS_PING_URL:-}"
  if [[ "${url}" == "" ]]; then
    echo "Set ORCHEO_DESKTOP_REDIS_PING_URL to the broker URL to probe." >&2
    exit 64
  fi

  # redis[s]://[[user][:password]@]host[:port][/db] - strip the scheme, then any
  # credentials, then the path, and split off an optional port.
  local hostport="${url#*://}"
  hostport="${hostport##*@}"
  hostport="${hostport%%/*}"

  local host port
  if [[ "${hostport}" == \[*\]* ]]; then
    # Bracketed IPv6 literal: [::1] or [::1]:6379.
    host="${hostport%%\]*}"
    host="${host#\[}"
    port="${hostport##*\]}"
    port="${port#:}"
  elif [[ "${hostport}" == *:* ]]; then
    host="${hostport%:*}"
    port="${hostport##*:}"
  else
    host="${hostport}"
    port=""
  fi
  [[ "${port}" =~ ^[0-9]+$ ]] || port="6379"

  if [[ "${host}" == "" ]]; then
    echo "Could not read a host out of the broker URL." >&2
    exit 69
  fi

  # -G bounds the connect, -w the wait for data, so an unroutable address fails
  # in seconds instead of hanging the launch on the kernel's default timeout.
  if nc -z -G 2 -w 2 "${host}" "${port}" >/dev/null 2>&1; then
    return 0
  fi

  echo "No Redis answering at ${host}:${port}." >&2
  exit 69
}

case "${ACTION}" in
  ping)
    ping_broker_url
    ;;
  start)
    redis_bin_dir="$(find_redis_bin_dir || true)"
    if [[ "${redis_bin_dir}" == "" ]]; then
      echo "No local Redis binaries found. Rebuild Orcheo with bundled Redis, install Redis locally, or set REDIS_URL in ${APP_SUPPORT_DIR}/desktop.env." >&2
      exit 69
    fi
    start_local_redis "${redis_bin_dir}" "$(choose_port "${redis_bin_dir}")"
    ;;
  stop)
    redis_bin_dir="$(find_redis_bin_dir || true)"
    if [[ "${redis_bin_dir}" != "" ]]; then
      stop_local_redis "${redis_bin_dir}"
    fi
    ;;
  *)
    echo "unknown action: ${ACTION}" >&2
    exit 64
    ;;
esac
