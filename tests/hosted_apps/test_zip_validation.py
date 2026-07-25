"""Regression coverage for static Hosted Apps bundle validation."""

from __future__ import annotations

from io import BytesIO
import zipfile
import pytest
from orcheo.hosted_apps import BundleValidationError
from orcheo.hosted_apps.zip_validation import BundleValidationLimits, validate_bundle


def _bundle(entries: dict[str, bytes]) -> BytesIO:
    """Build an in-memory ZIP with explicitly supplied entries."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    buffer.seek(0)
    return buffer


def test_validator_builds_manifest_and_inline_csp_hashes() -> None:
    """Only validator-derived hashes permit supported inline scripts."""
    manifest = validate_bundle(
        _bundle(
            {
                "index.html": b"<script>window.answer = 42;</script><h1>App</h1>",
                "assets/main.js": b"console.log('safe');",
            }
        )
    )
    assert manifest.index == "index.html"
    assert manifest.files["assets/main.js"].content_type.startswith("text/javascript")
    assert manifest.html_policy["index.html"]["inline_script_hashes"] == (
        "sha256-8rs5+OC6hkqjGoFGpLXZTrVxMzBrQcCd14GHFVLvinM=",
    )


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ({"main.html": b"ok"}, "hosted_apps.bundle.index_missing"),
        (
            {"index.html": b"ok", "__orcheo/config": b"x"},
            "hosted_apps.bundle.reserved_path",
        ),
        (
            {"index.html": b"<button onclick='x()'>x</button>"},
            "hosted_apps.bundle.inline_event_handler",
        ),
        (
            {"index.html": b"<a href='javascript:bad()'>x</a>"},
            "hosted_apps.bundle.javascript_url",
        ),
        ({"index.html": b"ok", "../escape": b"x"}, "hosted_apps.bundle.unsafe_path"),
        (
            {"index.html": b"ok", "bundle.zip": b"nested"},
            "hosted_apps.bundle.nested_archive",
        ),
        ({"index.html": b"ok", "worker.exe": b"MZ"}, "hosted_apps.bundle.executable"),
        (
            {"index.html": b"ok", "unknown.bin": b"\x7fELF"},
            "hosted_apps.bundle.executable",
        ),
    ],
)
def test_validator_rejects_dangerous_bundle_content(
    entries: dict[str, bytes], code: str
) -> None:
    """Archive errors use stable author-safe error codes."""
    with pytest.raises(BundleValidationError) as exc_info:
        validate_bundle(_bundle(entries))
    assert exc_info.value.code == code


def test_validator_rejects_casefold_path_collisions() -> None:
    """A gateway never has to choose between ambiguous asset spellings."""
    with pytest.raises(BundleValidationError) as exc_info:
        validate_bundle(_bundle({"index.html": b"ok", "A.js": b"a", "a.js": b"b"}))
    assert exc_info.value.code == "hosted_apps.bundle.path_collision"


def test_validator_enforces_expanded_size_before_serving() -> None:
    """A compressed archive cannot bypass the expanded-byte budget."""
    with pytest.raises(BundleValidationError) as exc_info:
        validate_bundle(
            _bundle({"index.html": b"x" * 20}),
            limits=BundleValidationLimits(max_expanded_bytes=10),
        )
    assert (
        exc_info.value.code == "hosted_apps.bundle.file_too_large"
        or exc_info.value.code == "hosted_apps.bundle.expanded_too_large"
    )
