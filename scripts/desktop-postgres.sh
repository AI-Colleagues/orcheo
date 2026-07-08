#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
APP_SUPPORT_DIR="${2:-}"
LOG_DIR="${3:-}"

if [[ "${ACTION}" == "" || "${APP_SUPPORT_DIR}" == "" || "${LOG_DIR}" == "" ]]; then
  echo "usage: desktop-postgres.sh <start|stop> <app-support-dir> <log-dir>" >&2
  exit 64
fi

PG_ROOT="${APP_SUPPORT_DIR}/postgres"
PG_DATA_DIR="${PG_ROOT}/data"
PG_PORT_FILE="${PG_ROOT}/port"
PG_LOG="${LOG_DIR}/postgres.log"
PG_USER="orcheo_desktop"
PG_DB="orcheo_desktop"
PG_RUNTIME_SHARE_LINK="${ORCHEO_DESKTOP_POSTGRES_RUNTIME_SHARE_DIR:-/tmp/orcheo-pg17-share}"
PG_RUNTIME_PKGLIB_LINK="${ORCHEO_DESKTOP_POSTGRES_RUNTIME_PKGLIB_DIR:-/tmp/orcheo-pg17-lib}"
PG_RUNTIME_SYSCONF_LINK="${ORCHEO_DESKTOP_POSTGRES_RUNTIME_SYSCONF_DIR:-/tmp/orcheo-pg17-etc}"
PG_RUNTIME_LOCALE_LINK="${ORCHEO_DESKTOP_POSTGRES_RUNTIME_LOCALE_DIR:-/tmp/orcheo-pg17-locale}"

mkdir -p "${PG_ROOT}" "${LOG_DIR}"

if [[ -f "${APP_SUPPORT_DIR}/desktop.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${APP_SUPPORT_DIR}/desktop.env"
  set +a
fi

is_port_free() {
  local port="$1"
  ! nc -z 127.0.0.1 "${port}" >/dev/null 2>&1
}

choose_port() {
  if [[ -f "${PG_PORT_FILE}" ]]; then
    local saved
    saved="$(cat "${PG_PORT_FILE}")"
    if [[ "${saved}" =~ ^[0-9]+$ ]] && is_port_free "${saved}"; then
      echo "${saved}"
      return
    fi
  fi

  local port
  for port in $(seq 25432 25531); do
    if is_port_free "${port}"; then
      echo "${port}" | tee "${PG_PORT_FILE}" >/dev/null
      echo "${port}"
      return
    fi
  done

  echo "No free desktop Postgres port found in 25432-25531." >&2
  exit 70
}

configure_locale() {
  local locale_name="${ORCHEO_DESKTOP_POSTGRES_LOCALE:-en_US.UTF-8}"
  local timezone_name="${ORCHEO_DESKTOP_POSTGRES_TIMEZONE:-UTC}"
  export LANG="${LANG:-${locale_name}}"
  export LC_ALL="${LC_ALL:-${locale_name}}"
  export LC_CTYPE="${LC_CTYPE:-${locale_name}}"
  export TZ="${timezone_name}"

  if ! locale >/dev/null 2>&1; then
    export LANG="${locale_name}"
    export LC_ALL="${locale_name}"
    export LC_CTYPE="${locale_name}"
  fi
}

find_pg_bin_dir() {
  if [[ "${ORCHEO_DESKTOP_POSTGRES_BIN_DIR:-}" != "" ]] \
    && [[ -x "${ORCHEO_DESKTOP_POSTGRES_BIN_DIR}/initdb" ]] \
    && [[ -x "${ORCHEO_DESKTOP_POSTGRES_BIN_DIR}/pg_ctl" ]] \
    && [[ -x "${ORCHEO_DESKTOP_POSTGRES_BIN_DIR}/createdb" ]]; then
    echo "${ORCHEO_DESKTOP_POSTGRES_BIN_DIR}"
    return
  fi

  local script_dir bundled_candidate
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  bundled_candidate="$(cd "${script_dir}/.." && pwd)/../postgres/bin"
  if [[ -x "${bundled_candidate}/initdb" && -x "${bundled_candidate}/pg_ctl" && -x "${bundled_candidate}/createdb" ]]; then
    echo "${bundled_candidate}"
    return
  fi

  local initdb_path
  initdb_path="$(command -v initdb || true)"
  if [[ "${initdb_path}" != "" ]]; then
    local bin_dir
    bin_dir="$(dirname "${initdb_path}")"
    if [[ -x "${bin_dir}/pg_ctl" && -x "${bin_dir}/createdb" ]]; then
      echo "${bin_dir}"
      return
    fi
  fi

  local candidate
  for candidate in \
    /opt/homebrew/opt/postgresql@17/bin \
    /opt/homebrew/opt/postgresql@16/bin \
    /opt/homebrew/opt/postgresql@15/bin \
    /opt/homebrew/opt/postgresql/bin \
    /usr/local/opt/postgresql@17/bin \
    /usr/local/opt/postgresql@16/bin \
    /usr/local/opt/postgresql@15/bin \
    /usr/local/opt/postgresql/bin \
    /Applications/Postgres.app/Contents/Versions/latest/bin; do
    if [[ -x "${candidate}/initdb" && -x "${candidate}/pg_ctl" && -x "${candidate}/createdb" ]]; then
      echo "${candidate}"
      return
    fi
  done
}

find_pg_share_dir() {
  local bin_dir="$1"
  local prefix_dir
  prefix_dir="$(cd "${bin_dir}/.." && pwd)"

  local candidate
  for candidate in \
    "${prefix_dir}/share/postgresql" \
    "${prefix_dir}/share/postgresql@17" \
    "${prefix_dir}/share/postgresql@16" \
    "${prefix_dir}/share/postgresql@15"; do
    if [[ -f "${candidate}/postgres.bki" ]]; then
      echo "${candidate}"
      return
    fi
  done
}

ensure_runtime_symlink() {
  local link_path="$1"
  local target_path="$2"

  if [[ ! -e "${target_path}" ]]; then
    return
  fi

  if [[ -L "${link_path}" ]]; then
    local current_target
    current_target="$(readlink "${link_path}")"
    if [[ "${current_target}" == "${target_path}" ]]; then
      return
    fi
    rm -f "${link_path}"
  elif [[ -e "${link_path}" ]]; then
    echo "Cannot prepare bundled Postgres runtime path because ${link_path} already exists and is not a symlink." >&2
    exit 69
  fi

  ln -s "${target_path}" "${link_path}"
}

prepare_bundled_runtime_paths() {
  local bin_dir="$1"
  local prefix_dir
  prefix_dir="$(cd "${bin_dir}/.." && pwd)"

  if [[ ! -f "${prefix_dir}/share/postgresql/postgres.bki" ]]; then
    return
  fi

  mkdir -p "${PG_ROOT}/etc"
  ensure_runtime_symlink "${PG_RUNTIME_SHARE_LINK}" "${prefix_dir}/share/postgresql"
  ensure_runtime_symlink "${PG_RUNTIME_PKGLIB_LINK}" "${prefix_dir}/lib/postgresql"
  ensure_runtime_symlink "${PG_RUNTIME_SYSCONF_LINK}" "${PG_ROOT}/etc"
  ensure_runtime_symlink "${PG_RUNTIME_LOCALE_LINK}" "${prefix_dir}/share/locale"
}

start_local_postgres() {
  local bin_dir="$1"
  local port="$2"
  local share_dir
  local timezone_name="${ORCHEO_DESKTOP_POSTGRES_TIMEZONE:-UTC}"

  configure_locale
  prepare_bundled_runtime_paths "${bin_dir}"
  share_dir="$(find_pg_share_dir "${bin_dir}" || true)"

  if [[ ! -d "${PG_DATA_DIR}/base" ]]; then
    if [[ -d "${PG_DATA_DIR}" ]] && [[ ! -f "${PG_DATA_DIR}/PG_VERSION" ]] \
      && [[ -n "$(find "${PG_DATA_DIR}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      mv "${PG_DATA_DIR}" "${PG_DATA_DIR}.failed-$(date +%Y%m%d%H%M%S)"
    fi
    if [[ "${share_dir}" != "" ]]; then
      "${bin_dir}/initdb" -L "${share_dir}" -D "${PG_DATA_DIR}" -A trust -U "${PG_USER}" >>"${PG_LOG}" 2>&1
    else
      "${bin_dir}/initdb" -D "${PG_DATA_DIR}" -A trust -U "${PG_USER}" >>"${PG_LOG}" 2>&1
    fi
  fi

  if "${bin_dir}/pg_ctl" -D "${PG_DATA_DIR}" status >/dev/null 2>&1; then
    :
  else
    "${bin_dir}/pg_ctl" \
      -D "${PG_DATA_DIR}" \
      -l "${PG_LOG}" \
      -o "-h 127.0.0.1 -p ${port} -c timezone=${timezone_name} -c log_timezone=${timezone_name}" \
      start -w >>"${PG_LOG}" 2>&1
  fi

  "${bin_dir}/createdb" -h 127.0.0.1 -p "${port}" -U "${PG_USER}" "${PG_DB}" \
    >>"${PG_LOG}" 2>&1 || true

  echo "postgresql://${PG_USER}@127.0.0.1:${port}/${PG_DB}"
}

stop_local_postgres() {
  local bin_dir="$1"
  configure_locale
  if [[ -d "${PG_DATA_DIR}/base" ]]; then
    "${bin_dir}/pg_ctl" -D "${PG_DATA_DIR}" stop -m fast -w >>"${PG_LOG}" 2>&1 || true
  fi
}

case "${ACTION}" in
  start)
    port="$(choose_port)"
    pg_bin_dir="$(find_pg_bin_dir || true)"
    if [[ "${pg_bin_dir}" != "" ]]; then
      start_local_postgres "${pg_bin_dir}" "${port}"
      exit 0
    fi

    echo "No local PostgreSQL binaries found. Rebuild Orcheo with bundled Postgres, install PostgreSQL locally, or set ORCHEO_DESKTOP_POSTGRES_DSN in ~/Library/Application Support/Orcheo/desktop.env." >&2
    exit 69
    ;;
  stop)
    pg_bin_dir="$(find_pg_bin_dir || true)"
    if [[ "${pg_bin_dir}" != "" ]]; then
      stop_local_postgres "${pg_bin_dir}"
    fi
    ;;
  *)
    echo "unknown action: ${ACTION}" >&2
    exit 64
    ;;
esac
