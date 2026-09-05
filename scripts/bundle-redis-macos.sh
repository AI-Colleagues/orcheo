#!/usr/bin/env bash
set -euo pipefail

# Stages a relocatable redis-server into a destination directory for the
# packaged desktop apps. Redis links against nothing but libSystem and
# openssl@3, so this is a much smaller job than bundle-postgres-macos.sh:
# copy the two binaries we need, copy the openssl dylibs next to them, and
# rewrite the Homebrew-absolute references to @loader_path.

DEST_DIR="${1:-}"

if [[ "${DEST_DIR}" == "" ]]; then
  echo "usage: bundle-redis-macos.sh <destination-dir>" >&2
  exit 64
fi

FORMULA="${ORCHEO_MACOS_REDIS_FORMULA:-redis}"
BREW="${HOMEBREW_BIN:-$(command -v brew || true)}"
SOURCE_DIR="${ORCHEO_MACOS_REDIS_SOURCE_DIR:-}"

if [[ "${SOURCE_DIR}" == "" ]]; then
  if [[ "${BREW}" == "" ]]; then
    echo "Homebrew is required to source ${FORMULA}; set ORCHEO_MACOS_REDIS_SOURCE_DIR to an existing Redis prefix to avoid brew." >&2
    exit 69
  fi

  if ! "${BREW}" list --versions "${FORMULA}" >/dev/null 2>&1; then
    if [[ "${ORCHEO_MACOS_INSTALL_REDIS:-true}" != "true" ]]; then
      echo "${FORMULA} is not installed. Install it or set ORCHEO_MACOS_INSTALL_REDIS=true." >&2
      exit 69
    fi
    "${BREW}" install "${FORMULA}"
  fi

  SOURCE_DIR="$("${BREW}" --prefix "${FORMULA}")"
fi

if [[ ! -x "${SOURCE_DIR}/bin/redis-server" ]]; then
  echo "Redis source dir must contain bin/redis-server: ${SOURCE_DIR}" >&2
  exit 66
fi

rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}/bin" "${DEST_DIR}/lib"

# redis-server is the broker; redis-cli is what the supervisor script uses to
# ping and to shut the server down. The remaining binaries (benchmark, sentinel,
# the check-* aliases) are dead weight in a packaged app.
for binary_name in redis-server redis-cli; do
  cp -p "${SOURCE_DIR}/bin/${binary_name}" "${DEST_DIR}/bin/${binary_name}"
  chmod u+w "${DEST_DIR}/bin/${binary_name}"
done

is_system_dependency() {
  case "$1" in
    /usr/lib/*|/System/Library/*)
      return 0
      ;;
  esac
  return 1
}

copied_libraries=""

copy_and_rewrite() {
  local target="$1"
  local dependency
  local dependency_name

  while IFS= read -r dependency; do
    if [[ "${dependency}" == "" ]] || is_system_dependency "${dependency}"; then
      continue
    fi
    case "${dependency}" in
      @*)
        # Already relocatable (or resolved through an rpath we do not ship);
        # nothing to copy and nothing to rewrite.
        continue
        ;;
    esac
    if [[ ! -e "${dependency}" ]]; then
      echo "Missing Redis dependency ${dependency} referenced by ${target}" >&2
      exit 69
    fi

    dependency_name="$(basename "${dependency}")"
    if [[ ! -e "${DEST_DIR}/lib/${dependency_name}" ]]; then
      cp -p "$(realpath "${dependency}")" "${DEST_DIR}/lib/${dependency_name}"
      chmod u+w "${DEST_DIR}/lib/${dependency_name}"
      install_name_tool -id "@loader_path/${dependency_name}" \
        "${DEST_DIR}/lib/${dependency_name}" 2>/dev/null || true
      copied_libraries+="${DEST_DIR}/lib/${dependency_name}"$'\n'
      # An openssl dylib pulls in its sibling, so recurse into what we copied.
      copy_and_rewrite "${DEST_DIR}/lib/${dependency_name}"
    fi

    case "${target}" in
      "${DEST_DIR}/bin/"*)
        install_name_tool -change "${dependency}" \
          "@loader_path/../lib/${dependency_name}" "${target}"
        ;;
      "${DEST_DIR}/lib/"*)
        install_name_tool -change "${dependency}" \
          "@loader_path/${dependency_name}" "${target}"
        ;;
    esac
  done < <(otool -L "${target}" | awk 'NR > 1 { print $1 }')
}

for binary_name in redis-server redis-cli; do
  copy_and_rewrite "${DEST_DIR}/bin/${binary_name}"
done

# Rewriting load commands invalidates the Homebrew signature, so re-sign.
while IFS= read -r target; do
  if [[ "${target}" != "" && -e "${target}" ]]; then
    codesign --force --sign - "${target}" >/dev/null 2>&1 || true
  fi
done < <(printf "%s%s\n%s\n" "${copied_libraries}" \
  "${DEST_DIR}/bin/redis-server" "${DEST_DIR}/bin/redis-cli")

bad_references="$(
  for binary_name in redis-server redis-cli; do
    otool -L "${DEST_DIR}/bin/${binary_name}" | awk 'NR > 1 { print $1 }'
  done | grep -E '^/(opt/homebrew|usr/local|opt/local)/' || true
)"

if [[ "${bad_references}" != "" ]]; then
  echo "Redis bundle still contains non-system absolute library references:" >&2
  echo "${bad_references}" | sort -u >&2
  exit 69
fi

echo "Bundled Redis from ${SOURCE_DIR} into ${DEST_DIR}"
