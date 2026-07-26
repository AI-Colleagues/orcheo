#!/bin/sh

set -eu

runtime_user="${ORCHEO_RUNTIME_USER:-orcheo}"
runtime_group="${ORCHEO_RUNTIME_GROUP:-orcheo}"
home_dir="${ORCHEO_RUNTIME_HOME:-${HOME:-/data/home}}"

if [ "$home_dir" = "/root" ]; then
  home_dir="/data/home"
fi

export HOME="$home_dir"

codex_home="${CODEX_HOME:-$HOME/.codex}"
claude_home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
gemini_home="${GEMINI_CONFIG_DIR:-$HOME/.gemini}"
xdg_config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
xdg_cache_home="${XDG_CACHE_HOME:-$HOME/.cache}"
xdg_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
runtime_root="/data/agent-runtimes"
cache_dir="${ORCHEO_CACHE_DIR:-/data/cache/orcheo}"
plugin_dir="${ORCHEO_PLUGIN_DIR:-/data/plugins}"
uv_cache_dir="${UV_CACHE_DIR:-/data/cache/uv}"
app_bundle_dir="${ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT:-}"
workspace_root="/workspace/agents"
skill_ref="${ORCHEO_AGENT_SKILL_REF:-AI-Colleagues/agent-skills/orcheo}"

ensure_dir() {
  dir_path="$1"
  recursive="${2:-false}"
  if [ -z "$dir_path" ]; then
    return
  fi
  mkdir -p "$dir_path"
  if [ "$recursive" = "true" ]; then
    chown -R "$runtime_user:$runtime_group" "$dir_path"
  else
    chown "$runtime_user:$runtime_group" "$dir_path"
  fi
}

same_path_target() {
  left="$1"
  right="$2"
  if [ ! -e "$left" ] || [ ! -e "$right" ]; then
    return 1
  fi
  [ "$(stat -c '%d:%i' "$left")" = "$(stat -c '%d:%i' "$right")" ]
}

agent_skills_installed() {
  [ -f "$claude_home/skills/orcheo/SKILL.md" ] \
    && [ -f "$codex_home/skills/orcheo/SKILL.md" ] \
    && [ -f "$HOME/.orcheo/skills/orcheo/SKILL.md" ] \
    && [ -f "$gemini_home/skills/orcheo/SKILL.md" ]
}

install_agent_skills() {
  case "${ORCHEO_INSTALL_AGENT_SKILLS:-1}" in
    0|false|False|FALSE|no|No|NO)
      return
      ;;
  esac

  if agent_skills_installed || ! command -v skill-mgr >/dev/null 2>&1; then
    return
  fi

  if ! skill-mgr install "$skill_ref" --format json >/dev/null; then
    echo "Warning: failed to install Orcheo agent skills with skill-mgr." >&2
  fi
}

install_agent_skills_as_runtime_user() {
  gosu "$runtime_user:$runtime_group" env \
    HOME="$HOME" \
    ORCHEO_RUNTIME_HOME="$home_dir" \
    ORCHEO_RUNTIME_USER="$runtime_user" \
    ORCHEO_RUNTIME_GROUP="$runtime_group" \
    CODEX_HOME="$codex_home" \
    CLAUDE_CONFIG_DIR="$claude_home" \
    GEMINI_CONFIG_DIR="$gemini_home" \
    XDG_CONFIG_HOME="$xdg_config_home" \
    XDG_CACHE_HOME="$xdg_cache_home" \
    XDG_DATA_HOME="$xdg_data_home" \
    ORCHEO_AGENT_SKILL_REF="$skill_ref" \
    ORCHEO_INSTALL_AGENT_SKILLS="${ORCHEO_INSTALL_AGENT_SKILLS:-1}" \
    PATH="$PATH" \
    sh -c '
      case "${ORCHEO_INSTALL_AGENT_SKILLS:-1}" in
        0|false|False|FALSE|no|No|NO)
          exit 0
          ;;
      esac
      if [ -f "$CLAUDE_CONFIG_DIR/skills/orcheo/SKILL.md" ] \
        && [ -f "$CODEX_HOME/skills/orcheo/SKILL.md" ] \
        && [ -f "$HOME/.orcheo/skills/orcheo/SKILL.md" ] \
        && [ -f "$GEMINI_CONFIG_DIR/skills/orcheo/SKILL.md" ]; then
        exit 0
      fi
      if ! command -v skill-mgr >/dev/null 2>&1; then
        exit 0
      fi
      if ! skill-mgr install "$ORCHEO_AGENT_SKILL_REF" --format json >/dev/null; then
        echo "Warning: failed to install Orcheo agent skills with skill-mgr." >&2
      fi
    '
}

if [ "$(id -u)" -eq 0 ]; then
  ensure_dir /data
  ensure_dir "$HOME" true
  ensure_dir "$codex_home" true
  ensure_dir "$claude_home" true
  ensure_dir "$HOME/.orcheo" true
  ensure_dir "$gemini_home" true
  ensure_dir "$runtime_root" true
  ensure_dir "$cache_dir" true
  ensure_dir "$plugin_dir" true
  # Multiple Compose services share the uv cache and start concurrently. Only
  # claim the cache root here; recursively walking it races with uv creating and
  # removing temporary files in sibling containers.
  ensure_dir "$uv_cache_dir"
  if [ "${ORCHEO_APP_BUNDLE_BACKEND:-}" = "filesystem" ]; then
    ensure_dir "$app_bundle_dir"
  fi
  ensure_dir /workspace

  if [ ! -e "$workspace_root" ]; then
    ensure_dir "$workspace_root" true
  elif ! same_path_target "$workspace_root" /app; then
    ensure_dir "$workspace_root" true
  fi

  if [ -d /app/.venv ] || [ ! -e /app/.venv ]; then
    ensure_dir /app/.venv true
  fi

  install_agent_skills_as_runtime_user

  exec gosu "$runtime_user:$runtime_group" env \
    HOME="$HOME" \
    ORCHEO_RUNTIME_HOME="$home_dir" \
    ORCHEO_RUNTIME_USER="$runtime_user" \
    ORCHEO_RUNTIME_GROUP="$runtime_group" \
    CODEX_HOME="$codex_home" \
    CLAUDE_CONFIG_DIR="$claude_home" \
    GEMINI_CONFIG_DIR="$gemini_home" \
    XDG_CONFIG_HOME="$xdg_config_home" \
    XDG_CACHE_HOME="$xdg_cache_home" \
    XDG_DATA_HOME="$xdg_data_home" \
    PATH="$PATH" \
    "$@"
fi

install_agent_skills

exec "$@"
