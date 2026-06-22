#!/bin/bash
# Replace build-time placeholder strings with runtime environment variable values.
# This runs at container startup via nginx's /docker-entrypoint.d/ mechanism.

HTML_DIR="/usr/share/nginx/html"

escape_sed_replacement() {
  local raw_value="$1"
  raw_value="${raw_value//\\/\\\\}"
  raw_value="${raw_value//&/\\&}"
  raw_value="${raw_value//|/\\|}"
  printf '%s' "$raw_value"
}

# List of VITE_ variables and their placeholder strings
VARS=(
  "VITE_ORCHEO_BACKEND_URL"
  "VITE_ORCHEO_AUTH_DISABLED"
  "VITE_ORCHEO_CHATKIT_DOMAIN_KEY"
  "VITE_ORCHEO_ALLOWED_HOSTS"
)

for VAR in "${VARS[@]}"; do
  PLACEHOLDER="__${VAR}__"
  VALUE="${!VAR-}"
  ESCAPED_VALUE="$(escape_sed_replacement "$VALUE")"

  if [ -z "$VALUE" ]; then
    echo "studio-env: injecting empty value for $VAR"
  else
    echo "studio-env: injecting $VAR"
  fi

  find "$HTML_DIR" -type f \( -name '*.js' -o -name '*.html' \) \
    -exec sed -i "s|${PLACEHOLDER}|${ESCAPED_VALUE}|g" {} +
done
