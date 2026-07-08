#!/usr/bin/env bash
set -euo pipefail

DEST_DIR="${1:-}"

if [[ "${DEST_DIR}" == "" ]]; then
  echo "usage: bundle-postgres-macos.sh <destination-dir>" >&2
  exit 64
fi

FORMULA="${ORCHEO_MACOS_POSTGRES_FORMULA:-postgresql@17}"
BREW="${HOMEBREW_BIN:-$(command -v brew || true)}"
SOURCE_DIR="${ORCHEO_MACOS_POSTGRES_SOURCE_DIR:-}"

if [[ "${SOURCE_DIR}" == "" ]]; then
  if [[ "${BREW}" == "" ]]; then
    echo "Homebrew is required to source ${FORMULA}; set ORCHEO_MACOS_POSTGRES_SOURCE_DIR to an existing Postgres prefix to avoid brew." >&2
    exit 69
  fi

  if ! "${BREW}" list --versions "${FORMULA}" >/dev/null 2>&1; then
    if [[ "${ORCHEO_MACOS_INSTALL_POSTGRES:-true}" != "true" ]]; then
      echo "${FORMULA} is not installed. Install it or set ORCHEO_MACOS_INSTALL_POSTGRES=true." >&2
      exit 69
    fi
    "${BREW}" install "${FORMULA}"
  fi

  SOURCE_DIR="$("${BREW}" --prefix "${FORMULA}")"
fi

if [[ ! -x "${SOURCE_DIR}/bin/initdb" || ! -x "${SOURCE_DIR}/bin/pg_ctl" || ! -x "${SOURCE_DIR}/bin/postgres" ]]; then
  echo "Postgres source dir must contain bin/initdb, bin/pg_ctl, and bin/postgres: ${SOURCE_DIR}" >&2
  exit 66
fi

rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"

rsync -a --delete \
  --exclude "var/" \
  --exclude "homebrew.mxcl.*" \
  --exclude "lib/postgresql/pgxs/" \
  --exclude ".brew/" \
  "${SOURCE_DIR}/" "${DEST_DIR}/"

if [[ "${ORCHEO_MACOS_BUNDLE_POSTGRES_LIBS:-true}" != "true" ]]; then
  exit 0
fi

FORMULA_MAJOR="${FORMULA##*@}"
if [[ ! "${FORMULA_MAJOR}" =~ ^[0-9]+$ ]]; then
  FORMULA_MAJOR="17"
fi
RUNTIME_SHARE_DIR="${ORCHEO_MACOS_POSTGRES_RUNTIME_SHARE_DIR:-/tmp/orcheo-pg${FORMULA_MAJOR}-share}"
RUNTIME_PKGLIB_DIR="${ORCHEO_MACOS_POSTGRES_RUNTIME_PKGLIB_DIR:-/tmp/orcheo-pg${FORMULA_MAJOR}-lib}"
RUNTIME_SYSCONF_DIR="${ORCHEO_MACOS_POSTGRES_RUNTIME_SYSCONF_DIR:-/tmp/orcheo-pg${FORMULA_MAJOR}-etc}"
RUNTIME_LOCALE_DIR="${ORCHEO_MACOS_POSTGRES_RUNTIME_LOCALE_DIR:-/tmp/orcheo-pg${FORMULA_MAJOR}-locale}"

RUNTIME_LIB_DIR="${DEST_DIR}/lib/orcheo-runtime"
SOURCE_PG_LIB_DIR_REAL="$(realpath "${SOURCE_DIR}/lib/postgresql")"
BREW_PREFIX=""
if [[ "${BREW}" != "" ]]; then
  BREW_PREFIX="$("${BREW}" --prefix 2>/dev/null || true)"
fi
mkdir -p "${RUNTIME_LIB_DIR}"

is_system_dependency() {
  case "$1" in
    /usr/lib/*|/System/Library/*)
      return 0
      ;;
  esac
  return 1
}

is_postgres_library_dependency() {
  local dependency_real
  dependency_real="$(realpath "$1" 2>/dev/null || true)"
  case "${dependency_real}" in
    "${SOURCE_PG_LIB_DIR_REAL}/"*)
      return 0
      ;;
  esac
  return 1
}

is_macho_file() {
  file "$1" 2>/dev/null | grep -q "Mach-O"
}

resolve_dependency_path() {
  local target="$1"
  local dependency="$2"
  local dependency_suffix
  local dependency_name
  local candidate

  case "${dependency}" in
    @loader_path/*)
      dependency_suffix="${dependency#@loader_path/}"
      candidate="$(cd "$(dirname "${target}")" && pwd)/${dependency_suffix}"
      if [[ -e "${candidate}" ]]; then
        echo "${candidate}"
        return
      fi
      ;;
    @executable_path/*)
      dependency_suffix="${dependency#@executable_path/}"
      candidate="$(cd "$(dirname "${target}")" && pwd)/${dependency_suffix}"
      if [[ -e "${candidate}" ]]; then
        echo "${candidate}"
        return
      fi
      ;;
    @rpath/*)
      ;;
    /*)
      echo "${dependency}"
      return
      ;;
    *)
      return
      ;;
  esac

  dependency_name="$(basename "${dependency}")"
  if [[ "${BREW_PREFIX}" != "" && -d "${BREW_PREFIX}/opt" ]]; then
    for candidate in "${BREW_PREFIX}"/opt/*/lib/"${dependency_name}"; do
      if [[ -e "${candidate}" ]]; then
        echo "${candidate}"
        return
      fi
    done
  fi
}

contains_line() {
  case $'\n'"$1" in
    *$'\n'"$2"$'\n'*)
      return 0
      ;;
  esac
  return 1
}

scan_queue=""
all_targets=""
copied_runtime_libs=""

add_scan_target() {
  if ! contains_line "${scan_queue}" "$1"; then
    scan_queue+="$1"$'\n'
  fi
  if ! contains_line "${all_targets}" "$1"; then
    all_targets+="$1"$'\n'
  fi
}

copy_runtime_dependency() {
  local target="$1"
  local dependency="$2"
  local dependency_path
  local dependency_real
  local dependency_name
  local bundled_dependency

  if [[ "${dependency}" == "" ]]; then
    return
  fi

  dependency_path="$(resolve_dependency_path "${target}" "${dependency}")"
  if [[ "${dependency_path}" == "" ]]; then
    return
  fi

  case "${dependency_path}" in
    @*|"")
      return
      ;;
  esac
  if is_system_dependency "${dependency_path}"; then
    return
  fi
  if [[ ! -e "${dependency_path}" ]]; then
    return
  fi
  if is_postgres_library_dependency "${dependency_path}"; then
    return
  fi

  dependency_real="$(realpath "${dependency_path}")"
  dependency_name="$(basename "${dependency}")"
  bundled_dependency="${RUNTIME_LIB_DIR}/${dependency_name}"

  if [[ ! -e "${bundled_dependency}" ]]; then
    cp -p "${dependency_real}" "${bundled_dependency}"
    chmod u+w "${bundled_dependency}"
    install_name_tool -id "@loader_path/${dependency_name}" "${bundled_dependency}" 2>/dev/null || true
  fi

  if ! contains_line "${copied_runtime_libs}" "${bundled_dependency}"; then
    copied_runtime_libs+="${bundled_dependency}"$'\n'
    add_scan_target "${bundled_dependency}"
  fi
}

rewrite_dependency_reference() {
  local target="$1"
  local dependency="$2"
  local dependency_path
  local dependency_real
  local dependency_name
  local replacement=""

  if [[ "${dependency}" == "" ]]; then
    return
  fi

  dependency_path="$(resolve_dependency_path "${target}" "${dependency}")"
  if [[ "${dependency_path}" == "" ]]; then
    return
  fi

  case "${dependency_path}" in
    @*|"")
      return
      ;;
  esac
  if is_system_dependency "${dependency_path}" || [[ ! -e "${dependency_path}" ]]; then
    return
  fi

  dependency_real="$(realpath "${dependency_path}")"
  dependency_name="$(basename "${dependency}")"

  if is_postgres_library_dependency "${dependency_path}"; then
    case "${target}" in
      "${DEST_DIR}/bin/"*)
        replacement="@loader_path/../lib/postgresql/${dependency_name}"
        ;;
      "${DEST_DIR}/lib/postgresql/"*)
        replacement="@loader_path/${dependency_name}"
        ;;
    esac
  elif [[ -e "${RUNTIME_LIB_DIR}/${dependency_name}" ]]; then
    case "${target}" in
      "${DEST_DIR}/bin/"*)
        replacement="@loader_path/../lib/orcheo-runtime/${dependency_name}"
        ;;
      "${DEST_DIR}/lib/postgresql/"*)
        replacement="@loader_path/../orcheo-runtime/${dependency_name}"
        ;;
      "${RUNTIME_LIB_DIR}/"*)
        replacement="@loader_path/${dependency_name}"
        ;;
    esac
  fi

  if [[ "${replacement}" != "" && "${replacement}" != "${dependency}" ]]; then
    chmod u+w "${target}"
    install_name_tool -change "${dependency}" "${replacement}" "${target}"
  fi
}

scan_dependencies() {
  local target="$1"
  local dependency

  if ! is_macho_file "${target}"; then
    return
  fi

  while IFS= read -r dependency; do
    copy_runtime_dependency "${target}" "${dependency}"
  done < <(otool -L "${target}" | awk 'NR > 1 { print $1 }')
}

patch_target() {
  local target="$1"
  local dependency

  if ! is_macho_file "${target}"; then
    return
  fi

  case "${target}" in
    "${DEST_DIR}/lib/postgresql/"*)
      chmod u+w "${target}"
      install_name_tool -id "@loader_path/$(basename "${target}")" "${target}" 2>/dev/null || true
      ;;
    "${RUNTIME_LIB_DIR}/"*)
      chmod u+w "${target}"
      install_name_tool -id "@loader_path/$(basename "${target}")" "${target}" 2>/dev/null || true
      ;;
  esac

  while IFS= read -r dependency; do
    rewrite_dependency_reference "${target}" "${dependency}"
  done < <(otool -L "${target}" | awk 'NR > 1 { print $1 }')
}

patch_embedded_path() {
  local old_path="$1"
  local new_path="$2"
  local target="$3"

  if [[ "${old_path}" == "" || "${new_path}" == "" || ! -e "${target}" ]]; then
    return
  fi
  if (( ${#new_path} > ${#old_path} )); then
    echo "Cannot patch ${old_path} to longer path ${new_path}" >&2
    exit 69
  fi
  if ! grep -aqF "${old_path}" "${target}"; then
    return
  fi

  chmod u+w "${target}"
  OLD_PATH="${old_path}" NEW_PATH="${new_path}" perl -0pi -e '
    my $old = $ENV{"OLD_PATH"};
    my $new = $ENV{"NEW_PATH"};
    my $replacement = $new . ("\0" x (length($old) - length($new)));
    s/\Q$old\E/$replacement/g;
  ' "${target}"
}

patch_compiled_postgres_paths() {
  local target="$1"
  local source_share_dir
  local source_pkglib_dir
  local source_sysconf_dir
  local source_locale_dir

  if ! is_macho_file "${target}"; then
    return
  fi

  source_share_dir="$("${SOURCE_DIR}/bin/pg_config" --sharedir)"
  source_pkglib_dir="$("${SOURCE_DIR}/bin/pg_config" --pkglibdir)"
  source_sysconf_dir="$("${SOURCE_DIR}/bin/pg_config" --sysconfdir)"
  source_locale_dir="$("${SOURCE_DIR}/bin/pg_config" --localedir 2>/dev/null || true)"

  patch_embedded_path "${source_share_dir}" "${RUNTIME_SHARE_DIR}" "${target}"
  patch_embedded_path "${source_pkglib_dir}" "${RUNTIME_PKGLIB_DIR}" "${target}"
  patch_embedded_path "${source_sysconf_dir}" "${RUNTIME_SYSCONF_DIR}" "${target}"
  patch_embedded_path "${source_locale_dir}" "${RUNTIME_LOCALE_DIR}" "${target}"
}

while IFS= read -r target; do
  add_scan_target "${target}"
done < <(find "${DEST_DIR}/bin" -type f -perm -111)

if [[ -d "${DEST_DIR}/lib/postgresql" ]]; then
  while IFS= read -r target; do
    add_scan_target "${target}"
  done < <(find "${DEST_DIR}/lib/postgresql" -type f \( -name '*.dylib' -o -name '*.so' \))
fi

scan_index=1
while :; do
  current_target="$(printf "%s" "${scan_queue}" | awk -v line="${scan_index}" 'NR == line { print; exit }')"
  if [[ "${current_target}" == "" ]]; then
    break
  fi
  scan_index=$((scan_index + 1))
  scan_dependencies "${current_target}"
done

while IFS= read -r target; do
  if [[ "${target}" != "" ]]; then
    patch_target "${target}"
  fi
done < <(printf "%s" "${all_targets}")

while IFS= read -r target; do
  if [[ "${target}" != "" ]]; then
    patch_compiled_postgres_paths "${target}"
  fi
done < <(printf "%s" "${all_targets}")

while IFS= read -r target; do
  if [[ "${target}" != "" && -e "${target}" ]] && is_macho_file "${target}"; then
    codesign --force --sign - "${target}" >/dev/null 2>&1 || true
  fi
done < <(printf "%s" "${all_targets}")

bad_references="$(
  while IFS= read -r target; do
    if [[ "${target}" != "" && -e "${target}" ]] && is_macho_file "${target}"; then
      otool -L "${target}" | awk 'NR > 1 { print $1 }'
    fi
  done < <(printf "%s" "${all_targets}") | grep -E '^/(opt/homebrew|usr/local|opt/local)/' || true
)"

if [[ "${bad_references}" != "" ]]; then
  echo "Postgres bundle still contains non-system absolute library references:" >&2
  echo "${bad_references}" | sort -u >&2
  exit 69
fi

echo "Bundled Postgres from ${SOURCE_DIR} into ${DEST_DIR}"
