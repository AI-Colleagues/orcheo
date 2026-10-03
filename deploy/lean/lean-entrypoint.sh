#!/bin/sh
# Render the Studio bundle with runtime VITE_ values, then hand off to the
# shared Orcheo entrypoint (runtime-user setup, agent skills, gosu).

set -eu

template_dir="/opt/orcheo/studio-template"
studio_dir="/opt/orcheo/studio"

# Studio talks to the backend on its own origin unless told otherwise, and
# hosted-app links follow the backend's apps domain by default.
: "${VITE_ORCHEO_APPS_BASE_DOMAIN:=${ORCHEO_APPS_BASE_DOMAIN:-}}"
export VITE_ORCHEO_APPS_BASE_DOMAIN

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

render_studio() {
  find "$studio_dir" -mindepth 1 -delete
  cp -R "$template_dir/." "$studio_dir/"

  for var in \
    VITE_ORCHEO_BACKEND_URL \
    VITE_ORCHEO_AUTH_DISABLED \
    VITE_ORCHEO_APPS_BASE_DOMAIN \
    VITE_ORCHEO_CHATKIT_DOMAIN_KEY \
    VITE_ORCHEO_APPS_PORT; do
    eval "value=\${$var-}"
    escaped_value="$(escape_sed_replacement "$value")"
    find "$studio_dir" -type f \( -name '*.js' -o -name '*.html' \) \
      -exec sed -i "s|__${var}__|${escaped_value}|g" {} +
  done
  python -m orcheo.studio_assets "$studio_dir"
}

# Only render the bundled Studio; a custom ORCHEO_STUDIO_DIST_DIR is served
# as-is.
if [ "${ORCHEO_STUDIO_DIST_DIR:-}" = "$studio_dir" ]; then
  render_studio
fi

# With no command, only render (used at image build time).
if [ "$#" -eq 0 ]; then
  exit 0
fi

exec orcheo-entrypoint "$@"
