"""Run the public proxy without executing a module twice through node imports."""

from __future__ import annotations
import asyncio
from orcheo.security.https_proxy import _serve


if __name__ == "__main__":
    asyncio.run(_serve())
