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
STAGING_COMPOSE_FILE = STACK_DIR / "docker-compose.staging.yml"
CADDYFILE = STACK_DIR / "Caddyfile"
ENV_EXAMPLE = STACK_DIR / ".env.example"
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
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in services["caddy"]["volumes"]
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


def test_env_example_documents_public_ingress_contract() -> None:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "ORCHEO_PUBLIC_INGRESS_ENABLED=false" in content
    assert "ORCHEO_PUBLIC_HOST=" in content
    assert "COMPOSE_PROFILES=" in content
    assert "ORCHEO_CADDY_BACKEND_UPSTREAMS=backend:2025" in content
    assert "VITE_ORCHEO_ALLOWED_HOSTS=localhost,127.0.0.1" in content


def test_staging_compose_builds_local_images_from_repo_source() -> None:
    compose = yaml.safe_load(STAGING_COMPOSE_FILE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["backend"]["image"] == "orcheo-stack:staging-local"
    assert services["worker"]["image"] == "orcheo-stack:staging-local"
    assert services["celery-beat"]["image"] == "orcheo-stack:staging-local"
    assert services["studio"]["image"] == "orcheo-studio:staging-local"
    assert services["backend"]["build"] == {
        "context": "../..",
        "dockerfile": "deploy/stack/Dockerfile.orcheo.staging",
    }
    assert services["worker"]["build"] == services["backend"]["build"]
    assert services["celery-beat"]["build"] == services["backend"]["build"]
    assert services["studio"]["build"] == {
        "context": "../..",
        "dockerfile": "deploy/stack/Dockerfile.studio.staging",
    }


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


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not available")
def test_staging_stack_compose_config_renders(tmp_path: Path) -> None:
    temp_repo_root = tmp_path / "repo"
    temp_stack_dir = temp_repo_root / "deploy" / "stack"
    temp_stack_dir.mkdir(parents=True)
    for source in (COMPOSE_FILE, STAGING_COMPOSE_FILE, CADDYFILE, ENV_EXAMPLE):
        target = temp_stack_dir / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (temp_stack_dir / ".env").write_text(
        ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(temp_stack_dir / "docker-compose.yml"),
            "-f",
            str(temp_stack_dir / "docker-compose.staging.yml"),
            "--project-directory",
            str(temp_stack_dir),
            "config",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **COMPOSE_CONFIG_ENV},
        cwd=temp_repo_root,
    )

    assert result.returncode == 0, result.stderr
