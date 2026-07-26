"""Non-executing validation for prebuilt Hosted Apps ZIP bundles."""

from __future__ import annotations
import base64
import hashlib
import json
import mimetypes
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import IO, BinaryIO
from pydantic import ValidationError
from orcheo.hosted_apps.errors import BundleValidationError
from orcheo.hosted_apps.models import AppManifest, BundleFile, BundleManifest


__all__ = ["BundleValidationLimits", "validate_bundle"]


_NESTED_ARCHIVE_SUFFIXES = (
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
)
_MIME_OVERRIDES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
}
_APP_MANIFEST_PATH = "orcheo.app.json"


@dataclass(frozen=True, slots=True)
class BundleValidationLimits:
    """Explicit bounds that prevent an archive from exhausting a validator."""

    max_archive_bytes: int = 50 * 1024 * 1024
    max_expanded_bytes: int = 250 * 1024 * 1024
    max_file_count: int = 5_000
    max_file_bytes: int = 25 * 1024 * 1024
    max_path_depth: int = 16
    max_app_manifest_bytes: int = 256 * 1024


class _HtmlPolicyParser(HTMLParser):
    """Collect CSP hashes while rejecting HTML execution mechanisms we cannot model."""

    def __init__(self) -> None:
        """Initialize strict HTML parsing state."""
        super().__init__(convert_charrefs=False)
        self._inline_script_parts: list[str] | None = None
        self.inline_script_hashes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Validate attributes and begin supported inline script collection."""
        attr_map = {name.lower(): value for name, value in attrs}
        for name, value in attrs:
            normalized_name = name.lower()
            normalized_value = (value or "").strip().lower()
            if normalized_name.startswith("on"):
                self._reject("hosted_apps.bundle.inline_event_handler")
            if normalized_value.startswith("javascript:"):
                self._reject("hosted_apps.bundle.javascript_url")
        if tag.lower() != "script":
            return
        if self._inline_script_parts is not None:
            self._reject("hosted_apps.bundle.nested_script")
        if "src" in attr_map:
            return
        script_type = (attr_map.get("type") or "text/javascript").strip().lower()
        if script_type not in {
            "application/javascript",
            "module",
            "text/ecmascript",
            "text/javascript",
        }:
            self._reject("hosted_apps.bundle.unsupported_inline_script")
        self._inline_script_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Validate attributes on self-closing elements."""
        self.handle_starttag(tag, attrs)
        if tag.lower() == "script":
            self._reject("hosted_apps.bundle.unsupported_inline_script")

    def handle_endtag(self, tag: str) -> None:
        """Hash an inline script exactly as parsed when its element closes."""
        if tag.lower() != "script" or self._inline_script_parts is None:
            return
        content = "".join(self._inline_script_parts).encode()
        digest = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
        self.inline_script_hashes.append(f"sha256-{digest}")
        self._inline_script_parts = None

    def handle_data(self, data: str) -> None:
        """Collect inline script bytes without interpreting them."""
        if self._inline_script_parts is not None:
            self._inline_script_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        """Keep entity syntax literal within an inline script payload."""
        if self._inline_script_parts is not None:
            self._inline_script_parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        """Keep character-reference syntax literal within an inline script payload."""
        if self._inline_script_parts is not None:
            self._inline_script_parts.append(f"&#{name};")

    def close(self) -> None:
        """Reject malformed unterminated scripts after parsing completes."""
        super().close()
        if self._inline_script_parts is not None:
            self._reject("hosted_apps.bundle.unterminated_inline_script")

    @staticmethod
    def _reject(code: str) -> None:
        raise BundleValidationError(
            code, "Bundle HTML uses an unsupported executable construct."
        )


def validate_bundle(
    archive: BinaryIO, *, limits: BundleValidationLimits | None = None
) -> BundleManifest:
    """Validate a ZIP without executing it and return its authoritative manifest.

    ``zipfile`` requires random access to the central directory. Callers should pass a
    server-side staged object; validation still processes every asset incrementally and
    does not extract or evaluate user-provided files onto a live application path.
    """
    limits = limits or BundleValidationLimits()
    _verify_archive_size(archive, limits)
    try:
        with zipfile.ZipFile(archive) as bundle:
            return _validate_zip(bundle, limits)
    except zipfile.BadZipFile as exc:
        raise BundleValidationError(
            "hosted_apps.bundle.invalid_zip", "Bundle is not a valid ZIP archive."
        ) from exc


def _verify_archive_size(archive: BinaryIO, limits: BundleValidationLimits) -> None:
    """Check the staged archive size without copying it into memory."""
    try:
        position = archive.tell()
        archive.seek(0, 2)
        size = archive.tell()
        archive.seek(position)
    except (AttributeError, OSError):
        return
    if size > limits.max_archive_bytes:
        raise BundleValidationError(
            "hosted_apps.bundle.archive_too_large",
            "Bundle archive exceeds the allowed size.",
        )


def _validate_zip(
    bundle: zipfile.ZipFile, limits: BundleValidationLimits
) -> BundleManifest:
    """Validate ZIP members and build assets plus per-HTML CSP metadata."""
    infos = bundle.infolist()
    if len(infos) > limits.max_file_count:
        raise BundleValidationError(
            "hosted_apps.bundle.too_many_files", "Bundle contains too many files."
        )
    files: dict[str, BundleFile] = {}
    html_policy: dict[str, dict[str, tuple[str, ...]]] = {}
    app_manifest: AppManifest | None = None
    collision_keys: set[str] = set()
    expanded_bytes = 0
    for info in infos:
        path = _normalize_member_path(info.filename, limits)
        if path is None:
            continue
        _validate_member_kind(info, path)
        collision_key = unicodedata.normalize("NFC", path).casefold()
        if collision_key in collision_keys:
            raise BundleValidationError(
                "hosted_apps.bundle.path_collision",
                "Bundle contains colliding asset paths.",
            )
        collision_keys.add(collision_key)
        _validate_member_constraints(path, info, limits)
        expanded_bytes += info.file_size
        if expanded_bytes > limits.max_expanded_bytes:
            raise BundleValidationError(
                "hosted_apps.bundle.expanded_too_large",
                "Bundle expands beyond the allowed size.",
            )
        with bundle.open(info) as source:
            digest, content, first_bytes = _hash_member(
                source, keep_content=_is_html(path) or path == _APP_MANIFEST_PATH
            )
        _reject_executable_bytes(path, first_bytes)
        if path == _APP_MANIFEST_PATH:
            if content is None:
                raise AssertionError("App manifest content was not retained.")
            app_manifest = _parse_app_manifest(content)
            continue
        if content is not None:
            html_policy[path] = {"inline_script_hashes": _parse_html_policy(content)}
        files[path] = BundleFile(
            size_bytes=info.file_size,
            sha256=digest,
            content_type=_content_type(path),
        )
    if "index.html" not in files:
        raise BundleValidationError(
            "hosted_apps.bundle.index_missing", "Bundle must contain root index.html."
        )
    return BundleManifest(
        files=files,
        html_policy=html_policy,
        app_manifest=app_manifest,
    )


def _validate_member_constraints(
    path: str, info: zipfile.ZipInfo, limits: BundleValidationLimits
) -> None:
    """Enforce per-member type, size, and deploy-time manifest constraints."""
    if path.lower().endswith(_NESTED_ARCHIVE_SUFFIXES):
        raise BundleValidationError(
            "hosted_apps.bundle.nested_archive",
            "Nested archives are not supported.",
        )
    if info.file_size > limits.max_file_bytes:
        raise BundleValidationError(
            "hosted_apps.bundle.file_too_large",
            "A bundle file exceeds the allowed size.",
        )
    if path.casefold() == _APP_MANIFEST_PATH and path != _APP_MANIFEST_PATH:
        raise BundleValidationError(
            "hosted_apps.bundle.app_manifest_path_invalid",
            f"App manifest must use the exact root path {_APP_MANIFEST_PATH!r}.",
        )
    if path == _APP_MANIFEST_PATH and info.file_size > limits.max_app_manifest_bytes:
        raise BundleValidationError(
            "hosted_apps.bundle.app_manifest_too_large",
            "App manifest exceeds the allowed size.",
        )


def _normalize_member_path(filename: str, limits: BundleValidationLimits) -> str | None:
    """Return a safe normalized logical asset path or ignore a directory entry."""
    if "\x00" in filename or "\\" in filename:
        raise BundleValidationError(
            "hosted_apps.bundle.unsafe_path", "Bundle contains an unsafe asset path."
        )
    path = PurePosixPath(filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BundleValidationError(
            "hosted_apps.bundle.unsafe_path", "Bundle contains an unsafe asset path."
        )
    if filename.endswith("/"):
        return None
    if len(path.parts) > limits.max_path_depth:
        raise BundleValidationError(
            "hosted_apps.bundle.path_too_deep",
            "Bundle contains an asset path that is too deep.",
        )
    normalized = "/".join(path.parts)
    if normalized == "__orcheo" or normalized.startswith("__orcheo/"):
        raise BundleValidationError(
            "hosted_apps.bundle.reserved_path",
            "Bundle uses the reserved __orcheo namespace.",
        )
    return normalized


def _validate_member_kind(info: zipfile.ZipInfo, path: str) -> None:
    """Reject symlinks and non-regular POSIX member types before reading bytes."""
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type and file_type != stat.S_IFREG:
        raise BundleValidationError(
            "hosted_apps.bundle.special_file",
            f"Bundle asset {path!r} is not a regular file.",
        )


def _hash_member(
    source: IO[bytes], *, keep_content: bool
) -> tuple[str, bytes | None, bytes]:
    """Digest a member incrementally and retain only HTML for policy parsing."""
    digest = hashlib.sha256()
    parts: list[bytes] | None = [] if keep_content else None
    first_bytes = b""
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
        if len(first_bytes) < 8:
            first_bytes = (first_bytes + chunk)[:8]
        if parts is not None:
            parts.append(chunk)
    return (
        digest.hexdigest(),
        b"".join(parts) if parts is not None else None,
        first_bytes,
    )


def _reject_executable_bytes(path: str, first_bytes: bytes) -> None:
    """Reject native executable magic without trying to interpret source files."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".exe", ".dll", ".dylib", ".so", ".com", ".msi"}:
        raise BundleValidationError(
            "hosted_apps.bundle.executable", "Bundle contains a server executable."
        )
    executable_magic = (
        b"MZ",
        b"\x7fELF",
        b"#!",
        b"\xfe\xed\xfa",
        b"\xcf\xfa\xed\xfe",
    )
    if first_bytes.startswith(executable_magic):
        raise BundleValidationError(
            "hosted_apps.bundle.executable", "Bundle contains a server executable."
        )


def _is_html(path: str) -> bool:
    """Return whether a path must be parsed for CSP-relevant HTML policy."""
    return PurePosixPath(path).suffix.lower() in {".html", ".htm"}


def _parse_html_policy(content: bytes) -> tuple[str, ...]:
    """Parse UTF-8 HTML and return sorted validator-derived script hashes."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleValidationError(
            "hosted_apps.bundle.invalid_html_encoding",
            "HTML assets must be UTF-8 encoded.",
        ) from exc
    parser = _HtmlPolicyParser()
    parser.feed(text)
    parser.close()
    return tuple(sorted(set(parser.inline_script_hashes)))


def _parse_app_manifest(content: bytes) -> AppManifest:
    """Decode the private deploy-time manifest using a strict bounded schema."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleValidationError(
            "hosted_apps.bundle.app_manifest_encoding_invalid",
            "App manifest must be UTF-8 encoded.",
        ) from exc
    try:
        payload = json.loads(text, object_pairs_hook=_unique_json_object)
        return AppManifest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise BundleValidationError(
            "hosted_apps.bundle.app_manifest_invalid",
            "App manifest does not match the supported schema.",
        ) from exc


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON object keys instead of silently choosing a value."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _content_type(path: str) -> str:
    """Derive a safe server-side MIME type for a validated logical asset."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[suffix]
    guessed, _encoding = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"
