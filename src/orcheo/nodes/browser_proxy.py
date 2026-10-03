"""Compatibility imports for the public HTTPS proxy.

Keep the private helper aliases for existing imports and test integrations.
"""

from orcheo.security.https_proxy import (
    PublicHttpsProxy,
    _serve,
)
from orcheo.security.https_proxy import (
    _connect_host as _connect_host,
)
from orcheo.security.https_proxy import (
    _pipe as _pipe,
)
from orcheo.security.https_proxy import (
    _public_addresses as _public_addresses,
)


__all__ = ["PublicHttpsProxy"]


if __name__ == "__main__":
    import asyncio

    asyncio.run(_serve())
