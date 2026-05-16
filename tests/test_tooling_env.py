from pathlib import Path
from unittest.mock import patch
import pytest
from orcheo.tooling.env import (
    _create_state_directories,
    _extract_path_values,
    main,
    seed_env_file,
)


def write_env_example(tmp_path: Path, content: str) -> Path:
    example_path = tmp_path / ".env.example"
    example_path.write_text(content)
    return example_path


def test_seed_env_creates_file_and_directories(tmp_path: Path) -> None:
    write_env_example(
        tmp_path,
        "ORCHEO_CHATKIT_STORAGE_PATH=.orcheo/chatkit\nORCHEO_MCP_STDIO_LOG=.orcheo/mcp.log\n",
    )

    env_path = seed_env_file(project_root=tmp_path)

    assert env_path.exists()
    assert (tmp_path / ".orcheo").is_dir()


def test_seed_env_creates_directory_path(tmp_path: Path) -> None:
    write_env_example(
        tmp_path,
        "ORCHEO_VAULT_DIR=.orcheo/vault\n",
    )

    seed_env_file(project_root=tmp_path, overwrite=True)

    assert (tmp_path / ".orcheo" / "vault").is_dir()


def test_seed_env_skips_when_file_exists(tmp_path: Path) -> None:
    write_env_example(tmp_path, "ORCHEO_CHATKIT_STORAGE_PATH=.orcheo/chatkit\n")
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=1\n")

    result = seed_env_file(project_root=tmp_path)

    assert result.read_text() == "EXISTING=1\n"


def test_seed_env_overwrites_when_forced(tmp_path: Path) -> None:
    example = write_env_example(
        tmp_path, "ORCHEO_CHATKIT_STORAGE_PATH=.orcheo/chatkit\n"
    )
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=1\n")

    result = seed_env_file(project_root=tmp_path, overwrite=True)

    assert result.read_text() == example.read_text()


def test_seed_env_raises_when_example_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        seed_env_file(project_root=tmp_path)


def test_extract_path_values_filters_non_paths(tmp_path: Path) -> None:
    example = write_env_example(
        tmp_path,
        """
# comment line
ORCHEO_HOST=0.0.0.0
ORCHEO_CHATKIT_STORAGE_PATH=.orcheo/chatkit
ORCHEO_VAULT_DIR=.orcheo/vault
ORCHEO_ABS_PATH=/var/lib/orcheo
SECRET_PATH=$RUNTIME_SECRET
MALFORMED_LINE
""".strip(),
    )

    values = _extract_path_values(example)

    assert values == {Path(".orcheo/chatkit"), Path(".orcheo/vault")}


def test_main_seeds_environment(tmp_path: Path) -> None:
    write_env_example(tmp_path, "ORCHEO_CHATKIT_STORAGE_PATH=.orcheo/chatkit\n")

    result = main(["--root", str(tmp_path), "--force"])

    assert result == 0
    assert (tmp_path / ".env").exists()


def test_create_state_directories_skips_existing_directories(tmp_path: Path) -> None:
    example = write_env_example(
        tmp_path,
        "ORCHEO_CHATKIT_STORAGE_PATH=.orcheo/chatkit\nORCHEO_MCP_STDIO_LOG=.orcheo/mcp.log\n",
    )
    (tmp_path / ".orcheo" / "chatkit").mkdir(parents=True)

    with patch.object(
        Path, "mkdir", side_effect=AssertionError("mkdir should not be called")
    ):
        _create_state_directories(example, tmp_path)
