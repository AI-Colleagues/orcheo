"""Prepare deterministic gzip files after rendering Studio's runtime settings."""

from __future__ import annotations
import gzip
import sys
from pathlib import Path


def compress_assets(directory: Path) -> None:
    """Precompress text assets once so requests avoid repeated compression."""
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix not in {
            ".js",
            ".css",
            ".html",
            ".svg",
            ".json",
        }:
            continue
        content = path.read_bytes()
        compressed_path = path.with_name(path.name + ".gz")
        if len(content) < 1000:
            compressed_path.unlink(missing_ok=True)
            continue
        compressed = gzip.compress(content, mtime=0)
        if len(compressed) < len(content):
            compressed_path.write_bytes(compressed)
        else:
            compressed_path.unlink(missing_ok=True)


if __name__ == "__main__":
    compress_assets(Path(sys.argv[1]))
