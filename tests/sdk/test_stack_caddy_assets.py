"""Validation coverage for bundled Caddy ingress stack assets."""

from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
STACK_DIR = REPO_ROOT / "deploy" / "stack"
COMPOSE_FILE = STACK_DIR / "docker-compose.yml"
CADDYFILE = STACK_DIR / "Caddyfile"
ENV_EXAMPLE = STACK_DIR / ".env.example"
STUDIO_DOCKERFILE = STACK_DIR / "Dockerfile.studio"
STUDIO_ENTRYPOINT = STACK_DIR / "studio-entrypoint.sh"
COMPOSE_CONFIG_ENV = {
    "VITE_ORCHEO_CHATKIT_DOMAIN_KEY": "domain_pk_test",
}


def test_stack_compose_defines_public_ingress_and_direct_ports() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["backend"]["env_file"] == "${ORCHEO_STACK_ENV_FILE:-.env}"
    assert services["worker"]["env_file"] == "${ORCHEO_STACK_ENV_FILE:-.env}"
    assert services["celery-beat"]["env_file"] == "${ORCHEO_STACK_ENV_FILE:-.env}"
    assert services["studio"]["env_file"] == "${ORCHEO_STACK_ENV_FILE:-.env}"
    assert services["caddy"]["env_file"] == "${ORCHEO_STACK_ENV_FILE:-.env}"
    assert (
        "127.0.0.1:${ORCHEO_BACKEND_LOCAL_PORT:-2025}:2025"
        in services["backend"]["ports"]
    )
    assert (
        "127.0.0.1:${ORCHEO_STUDIO_LOCAL_PORT:-2026}:2026"
        in services["studio"]["ports"]
    )
    assert (
        "127.0.0.1:${ORCHEO_APP_GATEWAY_LOCAL_PORT:-2030}:2030"
        in services["app-gateway"]["ports"]
    )
    assert services["app-gateway"]["image"] == (
        "${ORCHEO_APP_GATEWAY_IMAGE:-ghcr.io/ai-colleagues/orcheo-app-gateway:latest}"
    )
    assert "command" not in services["app-gateway"]
    assert services["app-gateway"]["healthcheck"]["test"][:3] == [
        "CMD",
        "python",
        "-c",
    ]
    assert "profiles" not in services["app-gateway"]
    assert "profiles" not in services["validation-worker"]
    assert "profiles" not in services["hosted-app-cleanup"]
    assert services["backend"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        "curl -fsS http://localhost:2025/api/system/health > /dev/null",
    ]
    assert services["studio"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        "wget -q -O /dev/null http://127.0.0.1:2026/ || exit 1",
    ]
    assert (
        services["backend"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    )
    assert services["backend"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert "backend-local" not in services
    assert "studio-local" not in services
    assert services["caddy"]["depends_on"]["backend"]["condition"] == "service_healthy"
    assert services["caddy"]["depends_on"]["studio"]["condition"] == "service_healthy"
    assert services["caddy"]["profiles"] == ["public-ingress"]
    assert services["caddy"]["image"] == "${ORCHEO_CADDY_IMAGE:-caddy:2}"
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in services["caddy"]["volumes"]
    assert "./app-tls:/etc/orcheo/app-tls:ro" in services["caddy"]["volumes"]
    assert "caddy_data:/data" in services["caddy"]["volumes"]
    assert "caddy_config:/config" in services["caddy"]["volumes"]
    assert (
        "127.0.0.1:${ORCHEO_POSTGRES_LOCAL_PORT:-5432}:5432"
        in services["postgres"]["ports"]
    )
    assert (
        "127.0.0.1:${ORCHEO_REDIS_LOCAL_PORT:-6379}:6379" in services["redis"]["ports"]
    )
    assert "caddy_data" in compose["volumes"]
    assert "caddy_config" in compose["volumes"]


def test_caddyfile_routes_studio_api_and_websockets() -> None:
    content = CADDYFILE.read_text(encoding="utf-8")

    assert "{$ORCHEO_CADDY_SITE_ADDRESS}" in content
    assert "@backend path /api/* /ws/*" in content
    assert (
        "reverse_proxy @backend {$ORCHEO_CADDY_BACKEND_UPSTREAMS:backend:2025}"
        in content
    )
    assert "health_uri /api/system/health" in content
    assert "lb_policy round_robin" in content
    assert "reverse_proxy {$ORCHEO_CADDY_STUDIO_UPSTREAM:studio:2026}" in content
    assert "import /etc/orcheo/app-tls/Caddyfile" in content


def test_env_example_documents_public_ingress_contract() -> None:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "ORCHEO_PUBLIC_INGRESS_ENABLED=false" in content
    assert "ORCHEO_PUBLIC_HOST=" in content
    assert "COMPOSE_PROFILES=" in content
    assert "ORCHEO_HOSTED_APPS_ENABLED=true" in content
    assert (
        "# ORCHEO_APP_GATEWAY_IMAGE="
        "ghcr.io/ai-colleagues/orcheo-app-gateway:0.1.0" in content
    )
    assert "ORCHEO_APP_GATEWAY_LOCAL_PORT=2030" in content
    assert "ORCHEO_CADDY_BACKEND_UPSTREAMS=backend:2025" in content
    assert "VITE_ORCHEO_ALLOWED_HOSTS=localhost,127.0.0.1" in content
    assert "ORCHEO_APPS_BASE_DOMAIN=apps.localhost" in content


def test_studio_images_inject_hosted_app_address_settings() -> None:
    content = STUDIO_DOCKERFILE.read_text(encoding="utf-8")
    assert "VITE_ORCHEO_APPS_BASE_DOMAIN=__VITE_ORCHEO_APPS_BASE_DOMAIN__" in content
    assert "VITE_ORCHEO_APPS_PORT=__VITE_ORCHEO_APPS_PORT__" in content

    entrypoint = STUDIO_ENTRYPOINT.read_text(encoding="utf-8")
    assert '"VITE_ORCHEO_APPS_BASE_DOMAIN"' in entrypoint
    assert '"VITE_ORCHEO_APPS_PORT"' in entrypoint


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not available")
def test_stack_compose_config_renders_with_profiles(tmp_path: Path) -> None:
    temp_stack_dir = tmp_path / "stack"
    temp_stack_dir.mkdir()
    for source in (COMPOSE_FILE, CADDYFILE, ENV_EXAMPLE):
        target = temp_stack_dir / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (temp_stack_dir / ".env").write_text(
        ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "public-ingress",
            "-f",
            str(temp_stack_dir / "docker-compose.yml"),
            "--project-directory",
            str(temp_stack_dir),
            "config",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **COMPOSE_CONFIG_ENV},
        cwd=temp_stack_dir,
    )

    assert result.returncode == 0, result.stderr
