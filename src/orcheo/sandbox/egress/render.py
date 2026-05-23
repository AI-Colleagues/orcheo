"""CLI: render the Envoy forward-proxy config from environment variables.

Used by ``make staging-up`` and ``orcheo install`` to materialize the
operator-managed allowlist (``ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS``) into a
concrete YAML file that the egress-proxy container mounts.

When ``--env-file`` is given the renderer parses that dotenv-style file
directly (no shell sourcing — values like ``FOO=bar baz`` would otherwise
make ``sh`` try to run ``baz`` as a command).
"""

from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
from orcheo.sandbox.egress.proxy import EnvoyForwardProxyConfig


_ENV_KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _read_env_value(env_file: Path, key: str) -> str | None:
    """Return the value for ``key`` from a dotenv-style file, or ``None``."""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        match = _ENV_KEY_PATTERN.match(line)
        if not match or match.group(1) != key:
            continue
        _, _, value = line.partition("=")
        # Strip surrounding quotes — leave inner spaces intact.
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
            stripped = stripped[1:-1]
        return stripped
    return None


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m orcheo.sandbox.egress.render``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Path to write the rendered envoy-forward-proxy.yaml.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=(
            "Optional dotenv-style file to read "
            "ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS from. "
            "When omitted the process environment is used."
        ),
    )
    args = parser.parse_args(argv)

    if args.env_file is not None:
        raw = (
            _read_env_value(args.env_file, "ORCHEO_SANDBOX_EGRESS_ALLOWED_HOSTS") or ""
        )
        hosts = tuple(item.strip() for item in raw.split(",") if item.strip())
        config = EnvoyForwardProxyConfig(global_allowed_hosts=hosts)
    else:
        config = EnvoyForwardProxyConfig.from_env()

    rendered = config.render_yaml()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
